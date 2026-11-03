"""Weighted relevance scoring, normalized to 0-100.

v1 scored a job by counting keyword hits, where every hit was worth the same
point. That ranked "build a Flutter app with GetX and clean architecture"
identically to a post that merely said "mobile". This module fixes that with
three changes:

1. **Weights** — "flutter" is worth more than "mobile", set in config.yaml.
2. **Negative keywords** — terms that subtract points (or hard-exclude the job
   entirely, for things like unpaid / revenue-share posts).
3. **Client quality** — budget, rating, hire rate and jobs-posted are already
   scraped, so a funded client with a real hiring history outranks a brand-new
   zero-history poster at the same keyword strength.

The two components are blended by configurable weights and clamped to 0-100, so
the number means the same thing on every platform.

**The neutral-client rule.** Mostaql and Khamsat expose no rating, hire rate or
budget on their listings. Scoring their absence as zero would bury every Arabic
job under every Freelancer job — punishing a source for what the *scraper*
can't see rather than for anything the client did. So a job with no client
signals at all scores neutral (0.5) on that component instead of zero. Set
``scoring.client.unknown_is_neutral: false`` to score absence as zero instead.
"""
from __future__ import annotations

import re

from models.job_posting import JobPosting

# Sensible defaults so the scorer works before config.yaml grows a scoring block.
_DEFAULT_WEIGHTS = {"keywords": 0.7, "client": 0.3}
_DEFAULT_CLIENT = {
    "unknown_is_neutral": True,
    "good_rating": 4.5,
    "good_hire_rate": 70.0,
    "good_jobs_posted": 20.0,
    "good_budget": 500.0,
    "good_hourly": 25.0,
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def searchable_text(job: JobPosting) -> str:
    """Everything a keyword might legitimately appear in, lowercased."""
    parts = [job.job_title, job.description_summary, " ".join(job.required_skills)]
    return " ".join(p for p in parts if p).lower()


def parse_budget_amount(budget_amount: str, currency_rates: dict | None = None) -> float | None:
    """Pull a comparable number out of '250-750 USD' / '15/hr USD'.

    Ranges collapse to their midpoint. When the trailing currency code is known
    in ``currency_rates`` the value is converted so a 3000 EGP job doesn't
    outrank a 400 USD one purely on digit count. Unknown codes pass through at
    1.0 — documented, and better than dropping the signal.
    """
    if not budget_amount:
        return None
    numbers = [float(n) for n in _NUMBER.findall(budget_amount)]
    if not numbers:
        return None
    value = sum(numbers[:2]) / len(numbers[:2])  # midpoint of a range, or the lone value

    rates = currency_rates or {}
    if rates:
        code_match = re.search(r"\b([A-Z]{3})\b", budget_amount.upper())
        if code_match:
            value *= float(rates.get(code_match.group(1), 1.0))
    # Round off binary-float noise (3000 * 0.021 -> 63.00000000000001).
    return round(value, 2)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def keyword_component(job: JobPosting, config: dict) -> tuple[float, int]:
    """Weighted keyword strength as 0..1, plus the raw weight for reporting.

    Falls back to the flat ``high_value_keywords`` list (weight 1 each) when no
    ``keyword_weights`` are configured, so an untouched v1 config still scores.
    """
    scoring = config.get("scoring", {}) or {}
    weights: dict = scoring.get("keyword_weights") or {
        kw: 1 for kw in config.get("high_value_keywords", [])
    }
    cap = float(scoring.get("keyword_cap", 10) or 10)

    haystack = searchable_text(job)
    raw = sum(float(w) for kw, w in weights.items() if str(kw).lower() in haystack)
    return _clamp01(raw / cap if cap else 0.0), int(raw)


def client_component(job: JobPosting, config: dict) -> float:
    """Client-quality strength as 0..1, averaged over whatever signals exist."""
    scoring = config.get("scoring", {}) or {}
    conf = {**_DEFAULT_CLIENT, **(scoring.get("client") or {})}
    rates = scoring.get("currency_rates") or {}

    signals: list[float] = []

    if job.client_rating is not None:
        signals.append(_clamp01(job.client_rating / float(conf["good_rating"])))

    hire = _NUMBER.search(job.hire_rate or "")
    if hire:
        signals.append(_clamp01(float(hire.group()) / float(conf["good_hire_rate"])))

    if job.client_jobs_posted is not None:
        signals.append(_clamp01(job.client_jobs_posted / float(conf["good_jobs_posted"])))

    budget = parse_budget_amount(job.budget_amount, rates)
    if budget is not None:
        target = float(conf["good_hourly"] if job.budget_type == "hourly" else conf["good_budget"])
        signals.append(_clamp01(budget / target) if target else 0.0)

    if not signals:
        return 0.5 if conf.get("unknown_is_neutral", True) else 0.0
    return sum(signals) / len(signals)


def penalty_points(job: JobPosting, config: dict) -> int:
    """Points to subtract for negative keywords present in the job text."""
    scoring = config.get("scoring", {}) or {}
    negatives: dict = scoring.get("negative_keywords") or {}
    haystack = searchable_text(job)
    return int(sum(float(pts) for kw, pts in negatives.items() if str(kw).lower() in haystack))


def is_excluded(job: JobPosting, config: dict) -> bool:
    """True if the job contains a hard-exclude term and must never be shown."""
    scoring = config.get("scoring", {}) or {}
    excludes = scoring.get("exclude_keywords") or []
    haystack = searchable_text(job)
    return any(str(kw).lower() in haystack for kw in excludes)


def compute_match_score(job: JobPosting, config: dict) -> int:
    """Blend keyword strength and client quality into a 0-100 score."""
    scoring = config.get("scoring", {}) or {}
    weights = {**_DEFAULT_WEIGHTS, **(scoring.get("weights") or {})}

    kw_norm, _raw = keyword_component(job, config)
    client_norm = client_component(job, config)

    total_weight = float(weights.get("keywords", 0.7)) + float(weights.get("client", 0.3))
    if total_weight <= 0:
        return 0
    blended = (
        float(weights.get("keywords", 0.7)) * kw_norm
        + float(weights.get("client", 0.3)) * client_norm
    ) / total_weight

    score = round(blended * 100) - penalty_points(job, config)
    return max(0, min(100, int(score)))
