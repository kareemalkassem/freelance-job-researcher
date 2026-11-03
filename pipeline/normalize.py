"""Shared, pure normalization helpers used by scrapers.

Each scraper maps its own wildly-different raw payload into ``JobPosting``, but
the small conversions that would otherwise be copy-pasted (trimming a summary,
formatting a budget range, coercing an epoch to UTC) live here so they're tested
once and behave identically across platforms.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_WHITESPACE = re.compile(r"\s+")


def epoch_to_utc(ts: float | int | None) -> datetime | None:
    """Convert a Unix epoch (seconds) to a timezone-aware UTC datetime."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def humanize_age(dt: datetime | None, now: datetime | None = None) -> str:
    """Render a coarse 'Xm/Xh/Xd ago' label for display (not for filtering)."""
    if dt is None:
        return ""
    now = now or datetime.now(timezone.utc)
    seconds = (now - dt).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86_400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86_400)}d ago"


def summarize(text: str | None, max_chars: int = 280) -> str:
    """Collapse whitespace and truncate to a 2-3 line summary."""
    if not text:
        return ""
    collapsed = _WHITESPACE.sub(" ", text).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    # Cut on a word boundary near the limit so we don't slice mid-word.
    cut = collapsed[:max_chars].rsplit(" ", 1)[0]
    return cut + "…"  # ellipsis


def clean_skills(skills: list[str] | None) -> list[str]:
    """Strip, drop blanks, and de-duplicate while preserving order."""
    if not skills:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in skills:
        s = (raw or "").strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def format_budget(
    minimum: float | int | None,
    maximum: float | int | None,
    currency: str = "",
    budget_type: str = "",
) -> str:
    """Render a human-readable budget string, e.g. '250-750 USD' or '15/hr USD'."""
    currency = (currency or "").strip()

    def num(v: float | int | None) -> str:
        if v is None:
            return ""
        # Drop trailing .0 so 250.0 shows as 250.
        return str(int(v)) if float(v).is_integer() else str(v)

    lo, hi = num(minimum), num(maximum)
    if lo and hi and lo != hi:
        amount = f"{lo}-{hi}"
    else:
        amount = lo or hi
    if not amount:
        return ""

    suffix = "/hr" if budget_type == "hourly" else ""
    return f"{amount}{suffix} {currency}".strip()
