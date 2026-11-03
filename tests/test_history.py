from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models.job_posting import JobPosting
from pipeline import history

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _job(url="https://x/1"):
    return JobPosting(platform="mostaql", job_title="Flutter app", job_url=url)


def test_first_sighting_is_new_second_is_not():
    store = {}
    jobs = [_job()]
    assert history.mark_new(jobs, store, now=NOW) == 1
    assert jobs[0].is_new is True

    jobs2 = [_job()]
    assert history.mark_new(jobs2, store, now=NOW) == 0
    assert jobs2[0].is_new is False


def test_repeat_job_is_not_new_regression():
    """Without the seen-store, yesterday's job resurfaces as NEW every morning."""
    store = {}
    history.mark_new([_job()], store, now=NOW)
    tomorrow = [_job()]
    history.mark_new(tomorrow, store, now=NOW + timedelta(days=1))
    assert tomorrow[0].is_new is False


def test_roundtrip_persists(tmp_path):
    path = tmp_path / "seen.json"
    store = {}
    history.mark_new([_job()], store, now=NOW)
    history.save(store, path)
    assert history.load(path) == store


def test_missing_and_corrupt_files_start_empty(tmp_path):
    assert history.load(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert history.load(bad) == {}


def test_prune_drops_old_entries_only():
    store = {
        "https://old": (NOW - timedelta(days=60)).isoformat(),
        "https://fresh": (NOW - timedelta(days=2)).isoformat(),
    }
    pruned = history.prune(store, retention_days=30, now=NOW)
    assert "https://fresh" in pruned
    assert "https://old" not in pruned


def test_prune_keeps_unparseable_timestamps():
    """Forgetting a URL wrongly would resurface it as NEW — the exact noise
    this module exists to prevent."""
    store = {"https://weird": "not-a-date"}
    assert history.prune(store, retention_days=30, now=NOW) == store


def test_jobs_without_url_are_skipped():
    store = {}
    job = _job(url="")
    assert history.mark_new([job], store, now=NOW) == 0
    assert store == {}
