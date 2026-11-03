"""Keep only fresh, relevant, non-excluded jobs and score them.

Three gates a job must pass, then a score:
  1. recency   — posted within ``lookback_hours`` (default 24)
  2. relevance — mentions at least one configured keyword
  3. exclusion — contains none of the hard-exclude terms (unpaid, etc.)

A job whose ``posted_date`` couldn't be determined is treated as NOT recent: we
can't prove it's within 24h, and silently keeping it would break the core
promise of the spreadsheet. ``main.py`` reports how many were dropped this way.

Scoring itself lives in ``pipeline/scoring.py``; this module owns the gates and
the ordering.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models.job_posting import JobPosting
from pipeline import scoring


def _searchable_text(job: JobPosting) -> str:
    """Everything a keyword might legitimately appear in, lowercased."""
    return scoring.searchable_text(job)


def is_recent(
    job: JobPosting, lookback_hours: int, now: datetime | None = None
) -> bool:
    """True if the job was posted within the lookback window.

    Unknown ``posted_date`` -> False (can't confirm recency).
    Boundary is inclusive: a job exactly ``lookback_hours`` old is kept.
    """
    if job.posted_date is None:
        return False
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    return job.posted_date >= cutoff


def is_relevant(job: JobPosting, keywords: list[str]) -> bool:
    """True if any keyword appears in the job's searchable text."""
    if not keywords:
        return True  # no filter configured -> keep everything
    haystack = _searchable_text(job)
    return any(kw.lower() in haystack for kw in keywords)


def compute_match_score(job: JobPosting, config: dict) -> int:
    """Weighted 0-100 score. Kept here as the pipeline-facing entry point."""
    return scoring.compute_match_score(job, config)


def apply(
    jobs: list[JobPosting], config: dict, now: datetime | None = None
) -> list[JobPosting]:
    """Filter to fresh+relevant jobs, stamp match_score, sort best-first.

    Sort order is score descending then newest first — the sheet's job is to put
    the best opportunity in row 2, not to group by platform. (Platform grouping
    is one click of the autofilter away if you want it.)
    """
    keywords = config.get("keywords", [])
    lookback = int(config.get("lookback_hours", 24))

    kept: list[JobPosting] = []
    for job in jobs:
        if not is_recent(job, lookback, now):
            continue
        if not is_relevant(job, keywords):
            continue
        if scoring.is_excluded(job, config):
            continue
        job.match_score = scoring.compute_match_score(job, config)
        kept.append(job)

    return sort_jobs(kept)


def rescore(jobs: list[JobPosting], config: dict) -> list[JobPosting]:
    """Recompute scores after detail-page enrichment added budget/skills/rating.

    Enrichment happens after the gates (it's one request per surviving job), so
    the first score was computed without the client-quality data that only the
    detail page carries. This folds it in.
    """
    for job in jobs:
        job.match_score = scoring.compute_match_score(job, config)
    return sort_jobs(jobs)


def sort_jobs(jobs: list[JobPosting]) -> list[JobPosting]:
    """Best score first, then newest first as the tie-break."""
    return sorted(
        jobs,
        key=lambda j: (
            -j.final_score,
            -(j.posted_date.timestamp() if j.posted_date else 0.0),
        ),
    )
