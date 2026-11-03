"""Cross-platform de-duplication.

Clients cross-post: the same brief lands on Mostaql and Khamsat, or gets posted
twice on one board. As sources grow, duplicates are the fastest way to make the
sheet feel noisy. Matching is done on a normalized title plus the client name,
which is deliberately conservative — two genuinely different projects from the
same client keep their own rows, and only a near-identical title collapses.

The survivor is the highest-scoring copy, so nothing is lost by deduping *after*
scoring: if one platform exposed a budget and the other didn't, you keep the
richer row. Deduping before the AI pass also means you never pay twice to judge
the same job.
"""
from __future__ import annotations

import re
import unicodedata

from models.job_posting import JobPosting

# Arabic diacritics (harakat) and tatweel carry no meaning for matching.
_ARABIC_MARKS = re.compile(r"[ً-ْـ]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_title(text: str) -> str:
    """Fold a title down to its comparable core.

    Unicode-normalizes, strips Arabic diacritics, drops punctuation and emoji,
    unifies alef/ya variants (أ إ آ -> ا, ى -> ي, ة -> ه) which Arabic posters
    use interchangeably, and collapses whitespace.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).lower()
    folded = _ARABIC_MARKS.sub("", folded)
    folded = folded.translate(str.maketrans("أإآ", "ااا")).replace("ى", "ي").replace("ة", "ه")
    folded = _PUNCT.sub(" ", folded)
    return _WS.sub(" ", folded).strip()


def dedupe_key(job: JobPosting) -> tuple[str, str]:
    """The identity two postings must share to be considered the same job."""
    return (normalize_title(job.job_title), normalize_title(job.client_name))


def dedupe(jobs: list[JobPosting]) -> tuple[list[JobPosting], int]:
    """Collapse duplicates, keeping the best-scoring copy of each.

    Returns ``(jobs, removed_count)``. Input order is preserved for survivors so
    an already-sorted list stays sorted.
    """
    best: dict[tuple[str, str], JobPosting] = {}
    order: list[tuple[str, str]] = []

    for job in jobs:
        key = dedupe_key(job)
        if not key[0]:  # untitled: can't match it to anything, always keep
            key = ("", job.job_url)
        incumbent = best.get(key)
        if incumbent is None:
            best[key] = job
            order.append(key)
        elif job.final_score > incumbent.final_score:
            best[key] = job

    survivors = [best[k] for k in order]
    return survivors, len(jobs) - len(survivors)
