"""Optional semantic matching — catch jobs that describe you without your words.

A client writing "تطبيق يعمل على أندرويد وآيفون بنفس الكود" or "cross-platform
app in Dart" never types "Flutter", so the keyword gate can miss them. Embedding
the job text and comparing it to a profile of your ideal work gives a similarity
signal that doesn't care which words were used.

This is the roadmap's stretch item and it is **off by default**, for two honest
reasons: it needs a local Ollama with an embedding model pulled, and it only
earns its place if keyword + AI scoring is still missing good jobs. When it's
off, nothing here runs and ``semantic_score`` stays None.

Cosine similarity is computed in plain Python — no numpy, no new dependency.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import httpx

from models.job_posting import JobPosting
from pipeline import ai as ai_module

log = logging.getLogger("researcher.embeddings")

_OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
_TIMEOUT = 60.0

DEFAULT_PROFILE = (
    "Flutter and Dart mobile application development. Cross-platform iOS and "
    "Android apps from one codebase. GetX state management, clean architecture, "
    "REST API integration, Firebase, custom UI. Building, rebuilding or "
    "finishing a mobile app for a paying client."
)


def embed_ollama(text: str, config: dict) -> list[float]:
    """One local embedding call. Returns the raw vector."""
    model = config.get("model", "nomic-embed-text")
    url = config.get("ollama_url", _OLLAMA_EMBED_URL)
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, json={"model": model, "prompt": text})
        resp.raise_for_status()
        data = resp.json()
    vector = data.get("embedding") or []
    if not vector:
        raise ValueError("embedding provider returned no vector")
    return [float(x) for x in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Standard cosine similarity, guarded against zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def to_score(similarity: float) -> int:
    """Map cosine [-1, 1] onto the same 0-100 scale everything else uses."""
    return max(0, min(100, round((similarity + 1.0) / 2.0 * 100)))


def _job_text(job: JobPosting) -> str:
    parts = [job.job_title, job.description_summary, " ".join(job.required_skills)]
    return " ".join(p for p in parts if p)


def enrich(jobs: list[JobPosting], config: dict, embed=None) -> int:
    """Attach ``semantic_score`` to each job. Returns how many were scored.

    Cached by job URL in the same style as the AI cache. Any failure disables
    the pass for this run rather than failing it — an embedding is a bonus
    signal, never a gate.
    """
    conf = ((config.get("ai") or {}).get("semantic") or {})
    if not conf.get("enabled", False) or not jobs:
        return 0

    embedder = embed or embed_ollama
    profile_text = conf.get("profile") or DEFAULT_PROFILE
    cache_path = Path(conf.get("cache_path", "cache/embeddings.json"))
    model = str(conf.get("model", "nomic-embed-text"))
    cache = ai_module.load_cache(cache_path)

    try:
        profile_vec = embedder(profile_text, conf)
    except Exception as exc:
        log.warning("semantic: profile embedding failed (%s) — pass skipped", exc)
        return 0

    scored = 0
    dirty = False
    for job in jobs:
        key = f"{model}|{job.job_url}"
        cached = cache.get(key)
        if isinstance(cached, dict) and "score" in cached:
            job.semantic_score = int(cached["score"])
            scored += 1
            continue
        text = _job_text(job)
        if not text.strip():
            continue
        try:
            vector = embedder(text, conf)
        except Exception as exc:
            log.warning("semantic: embedding failed (%s) — stopping pass", exc)
            break
        score = to_score(cosine_similarity(profile_vec, vector))
        job.semantic_score = score
        cache[key] = {"score": score}
        dirty = True
        scored += 1

    if dirty:
        ai_module.save_cache(cache, cache_path)
    return scored


def blend_into_match_score(jobs: list[JobPosting], config: dict) -> None:
    """Optionally fold the semantic signal back into the keyword score.

    Off unless ``ai.semantic.blend_weight`` > 0. Only affects ``match_score``;
    an AI fit score, when present, still wins the final ranking.
    """
    conf = ((config.get("ai") or {}).get("semantic") or {})
    weight = float(conf.get("blend_weight", 0.0) or 0.0)
    if weight <= 0:
        return
    weight = min(1.0, weight)
    for job in jobs:
        if job.semantic_score is None:
            continue
        job.match_score = round((1 - weight) * job.match_score + weight * job.semantic_score)
