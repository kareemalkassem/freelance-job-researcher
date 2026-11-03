"""Seen-job history, so "new since last run" is a fact and not a guess.

A job stays visible on a board for days. Without memory, the same posting looks
brand new in every morning's sheet and you re-read rows you already rejected.
This module keeps a small JSON file of every job URL ever exported with the
timestamp it was first seen, marks each job ``is_new`` accordingly, and forgets
entries older than the retention window so the file can't grow forever.

Storage is a flat JSON file on purpose — the project's principles rule out a
database, and at a few hundred URLs a day this stays a few hundred KB.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models.job_posting import JobPosting

log = logging.getLogger("researcher.history")


def load(path: str | Path) -> dict:
    """Read the seen-store. A missing or corrupt file starts empty."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("history unreadable (%s) — starting fresh", exc)
        return {}


def save(store: dict, path: str | Path) -> None:
    """Persist the seen-store; never fatal."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        log.warning("could not write history — %s", exc)


def prune(store: dict, retention_days: int, now: datetime | None = None) -> dict:
    """Drop entries older than the retention window.

    Entries with an unparseable timestamp are kept: forgetting a URL wrongly
    would resurface it as "new", which is exactly the noise this prevents.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, retention_days))
    kept = {}
    for url, seen_at in store.items():
        try:
            when = datetime.fromisoformat(str(seen_at))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            kept[url] = seen_at
            continue
        if when >= cutoff:
            kept[url] = seen_at
    return kept


def mark_new(
    jobs: list[JobPosting],
    store: dict,
    now: datetime | None = None,
) -> int:
    """Set ``is_new`` on each job and record first-sighting. Returns new count.

    Mutates ``store`` so the caller can prune and save once.
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    new_count = 0
    for job in jobs:
        if not job.job_url:
            continue
        if job.job_url in store:
            job.is_new = False
        else:
            job.is_new = True
            store[job.job_url] = stamp
            new_count += 1
    return new_count
