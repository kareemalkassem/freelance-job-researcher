"""Optional AI pass: fit score, reason, summary and red flags in one call.

Keyword scoring can tell you a post contains the word "mobile". It cannot tell
you whether the post is a serious Flutter build with a real budget or a vague
one-liner from a client who will vanish. That judgement is what this module buys.

Design constraints, straight from the project's guiding principles:

* **Cheap.** One request per job returns all four fields, results are cached by
  URL forever, and only jobs that already survived the keyword+recency gates are
  ever sent. A typical day is a handful of calls.
* **Optional.** ``ai.provider: none`` (the default) makes this a no-op. Nothing
  downstream requires an AI field to exist.
* **Never fatal.** Any failure — no key, no network, malformed JSON, a model
  that ignored the schema — logs a warning and leaves the job's AI fields empty,
  so the run still produces a spreadsheet ranked by the keyword score.
* **No new dependencies.** Both providers are plain HTTPS calls over httpx,
  which the project already ships.

Tests inject a fake ``call_model`` so the whole path is exercised offline.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import httpx

from models.job_posting import JobPosting

log = logging.getLogger("researcher.ai")

# Bump when the rubric below changes so stale cached judgements are recomputed
# rather than silently reused against a different question.
PROMPT_VERSION = "v1"

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_OLLAMA_URL = "http://localhost:11434/api/chat"
_TIMEOUT = 60.0

_SYSTEM_PROMPT = """You screen freelance job posts for a specific developer and \
reply with JSON only.

The developer: a Flutter / Dart mobile developer who works with GetX and clean \
architecture, builds cross-platform iOS + Android apps, and wants real projects \
from clients who can pay.

Score fit 0-100 using this rubric:
  85-100  Flutter/Dart build, clear scope, credible budget, serious client.
  60-84   Mobile app work this developer can clearly do (may be React Native
          adjacent, vague budget, or thin detail but a real project).
  30-59   Loosely related (generic "app" request, mostly other stack, tiny scope).
  0-29    Not mobile development, spam, unpaid, or content/design-only work.

Reply with EXACTLY this JSON object and nothing else:
{"fit_score": <int 0-100>,
 "reason": "<one short sentence on why it does or doesn't fit>",
 "summary": "<one short sentence describing what the client wants>",
 "red_flags": ["<short flag>", "..."]}

red_flags examples: "no budget stated", "unrealistic budget", "vague scope",
"urgent/rush", "asks for free work", "possible scam". Use [] when there are none.
Keep reason and summary under 20 words each. Write them in English."""


def _job_prompt(job: JobPosting) -> str:
    """Render the job into the compact block the model judges."""
    lines = [
        f"Platform: {job.platform}",
        f"Title: {job.job_title}",
    ]
    if job.budget_amount:
        lines.append(f"Budget: {job.budget_amount} ({job.budget_type or 'unspecified'})")
    if job.required_skills:
        lines.append(f"Skills: {', '.join(job.required_skills)}")
    if job.client_country:
        lines.append(f"Client country: {job.client_country}")
    if job.client_rating is not None:
        lines.append(f"Client rating: {job.client_rating}")
    if job.description_summary:
        lines.append(f"Description: {job.description_summary}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

def call_anthropic(prompt: str, config: dict) -> str:
    """One Messages API call. Returns the raw text the model produced."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — add it to .env")
    model = config.get("model", "claude-haiku-4-5-20251001")
    payload = {
        "model": model,
        "max_tokens": int(config.get("max_tokens", 300)),
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(_ANTHROPIC_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    blocks = data.get("content", []) or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def call_ollama(prompt: str, config: dict) -> str:
    """One local Ollama chat call — zero marginal cost, nothing leaves the box."""
    model = config.get("model", "llama3.1")
    url = config.get("ollama_url", _OLLAMA_URL)
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return (data.get("message", {}) or {}).get("content", "")


_PROVIDERS = {"anthropic": call_anthropic, "ollama": call_ollama}


# --------------------------------------------------------------------------
# Response handling
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_ai_response(text: str) -> dict:
    """Extract the judgement dict from whatever the model actually returned.

    Models wrap JSON in prose or code fences often enough that being strict here
    would throw away good answers. Falls back to the first {...} block. Raises
    ValueError only when there is genuinely no object to read.
    """
    if not text:
        raise ValueError("empty model response")
    candidate = text.strip()

    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        brace = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not brace:
            raise ValueError(f"no JSON object in response: {text[:120]!r}")
        data = json.loads(brace.group())

    if not isinstance(data, dict):
        raise ValueError("model returned JSON that is not an object")

    raw_score = data.get("fit_score")
    try:
        score = max(0, min(100, int(float(raw_score))))
    except (TypeError, ValueError):
        raise ValueError(f"missing/invalid fit_score: {raw_score!r}")

    flags = data.get("red_flags") or []
    if isinstance(flags, str):
        flags = [flags]

    return {
        "fit_score": score,
        "reason": str(data.get("reason", "") or "").strip(),
        "summary": str(data.get("summary", "") or "").strip(),
        "red_flags": ", ".join(str(f).strip() for f in flags if str(f).strip()),
    }


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def _cache_key(job: JobPosting, model: str) -> str:
    """Model + rubric version + URL, so changing either invalidates cleanly."""
    return f"{model}|{PROMPT_VERSION}|{job.job_url}"


def load_cache(path: str | Path) -> dict:
    """Read the judgement cache; a missing or corrupt file starts empty."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("ai cache unreadable (%s) — starting fresh", exc)
        return {}


def save_cache(cache: dict, path: str | Path) -> None:
    """Persist the cache; never fatal."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        log.warning("could not write ai cache — %s", exc)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def enrich(
    jobs: list[JobPosting], config: dict, call_model=None
) -> tuple[int, int, int]:
    """Attach AI fields to eligible jobs in place.

    Returns ``(scored, from_cache, failed)`` for the run report. ``call_model``
    is injectable — tests pass a fake, production resolves it from the provider
    name.
    """
    conf = (config.get("ai") or {})
    provider = str(conf.get("provider", "none")).lower()
    if provider in ("", "none", "off", "disabled") or not jobs:
        return (0, 0, 0)

    caller = call_model or _PROVIDERS.get(provider)
    if caller is None:
        log.warning("ai: unknown provider %r — skipping", provider)
        return (0, 0, 0)

    model = str(conf.get("model", "claude-haiku-4-5-20251001"))
    cache_path = conf.get("cache_path", "cache/ai_scores.json")
    cache = load_cache(cache_path)

    min_score = int(conf.get("min_match_score", 0))
    max_jobs = int(conf.get("max_jobs_per_run", 25))

    scored = cached = failed = 0
    budget_left = max_jobs

    for job in jobs:
        key = _cache_key(job, model)
        if key in cache:
            _apply(job, cache[key])
            cached += 1
            continue
        if job.match_score < min_score:
            continue  # not worth paying for
        if budget_left <= 0:
            log.info("ai: per-run cap of %d reached — remaining jobs keep keyword scores", max_jobs)
            break
        try:
            raw = caller(_job_prompt(job), conf)
            judgement = parse_ai_response(raw)
        except Exception as exc:  # any provider/parse failure degrades, never fails
            failed += 1
            budget_left -= 1
            log.warning("ai: %s — falling back to keyword score for %s", exc, job.job_url[:60])
            continue
        cache[key] = judgement
        _apply(job, judgement)
        scored += 1
        budget_left -= 1

    if scored:
        save_cache(cache, cache_path)
    return (scored, cached, failed)


def _apply(job: JobPosting, judgement: dict) -> None:
    """Copy a judgement onto the job."""
    job.fit_score = judgement.get("fit_score")
    job.fit_reason = judgement.get("reason", "")
    job.ai_summary = judgement.get("summary", "")
    job.red_flags = judgement.get("red_flags", "")
