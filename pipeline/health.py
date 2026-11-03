"""Scraper health tracking — notice a broken adapter before you lose a week.

A scraper that breaks doesn't crash. The site quietly changes a class name, the
selector matches nothing, and the run reports a cheerful "fetched 0 jobs" every
morning while you assume the market is slow. This module keeps a per-platform
streak of consecutive runs that produced nothing (or failed outright) and flags
a platform once the streak crosses a threshold.

Combined with the raw-HTML snapshots that scrapers write on an empty parse, a
flagged platform gives you both the alert and the evidence needed to fix it.

A streak alone doesn't say *why*, and the difference decides who fixes it. A
parse that returned zero rows really does suggest changed markup, and there
will be a snapshot to read. A 403 at the load balancer never reached the parser
and never wrote a snapshot — pointing that case at ``logs/snapshots/`` sends you
to an empty directory. So ``record`` also stores the failure ``reason`` reported
by the caller, and ``warnings`` uses it to give advice that matches reality.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("researcher.health")


def load(path: str | Path) -> dict:
    """Read the health store. Missing or corrupt starts empty."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("health store unreadable (%s) — starting fresh", exc)
        return {}


def save(store: dict, path: str | Path) -> None:
    """Persist the health store; never fatal."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        log.warning("could not write health store — %s", exc)


def record(
    store: dict,
    raw_counts: dict,
    failures: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Update streaks from this run's per-platform raw counts.

    ``raw_counts`` maps platform -> number of jobs fetched, or None if the fetch
    raised. A positive count resets the streak; zero or None extends it.

    ``failures`` optionally maps platform -> reason string (see
    :mod:`scrapers.errors`) explaining *why* a fetch raised. It is kept separate
    from ``raw_counts`` so the count stays a plain number that the rest of the
    pipeline and the Summary sheet can use without unpacking anything.
    """
    now = now or datetime.now(timezone.utc)
    failures = failures or {}
    for platform, count in raw_counts.items():
        entry = store.get(platform) or {}
        if count:
            entry["streak"] = 0
            entry["last_ok"] = now.isoformat()
            entry.pop("reason", None)
        else:
            entry["streak"] = int(entry.get("streak", 0)) + 1
            reason = failures.get(platform)
            # A successful fetch that parsed to zero rows has no reason at all;
            # clearing it keeps a stale "blocked" from outliving the block.
            if reason:
                entry["reason"] = reason
            else:
                entry.pop("reason", None)
        entry["last_run"] = now.isoformat()
        entry["last_count"] = count
        store[platform] = entry
    return store


# Remediation text per failure reason. The default (no reason recorded) means
# the fetch itself succeeded, so changed markup really is the likely culprit.
_ADVICE = {
    "blocked": (
        "blocked at the network edge — the host refused the client before "
        "serving any content, so there is no saved HTML to inspect and no "
        "selector to fix; this needs a different IP/proxy or an official API"
    ),
    "auth": (
        "missing or rejected credential — set the platform's token in .env; "
        "no amount of retrying will help"
    ),
    "error": (
        "the fetch raised before returning rows — check the run log for the "
        "traceback"
    ),
    None: "markup may have changed; check logs/snapshots/",
}


def warnings(store: dict, threshold: int = 3) -> list[str]:
    """Human-readable warnings for platforms stuck at zero for too long.

    The advice is chosen from the recorded failure reason so it points at the
    thing that actually needs doing.
    """
    out = []
    for platform, entry in sorted(store.items()):
        entry = entry or {}
        streak = int(entry.get("streak", 0))
        if streak >= threshold:
            last_ok = entry.get("last_ok") or "never"
            reason = entry.get("reason")
            advice = _ADVICE.get(reason, _ADVICE["error"])
            out.append(
                f"{platform}: {streak} consecutive runs with no jobs "
                f"(last success: {last_ok[:10]}) — {advice}"
            )
    return out
