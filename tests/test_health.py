from __future__ import annotations

from datetime import datetime, timezone

from pipeline import health

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def test_successful_run_resets_streak():
    store = {"mostaql": {"streak": 5}}
    health.record(store, {"mostaql": 12}, now=NOW)
    assert store["mostaql"]["streak"] == 0
    assert store["mostaql"]["last_ok"].startswith("2026-07-24")


def test_zero_and_failed_runs_extend_streak():
    store = {}
    health.record(store, {"mostaql": 0, "khamsat": None}, now=NOW)
    health.record(store, {"mostaql": 0, "khamsat": None}, now=NOW)
    assert store["mostaql"]["streak"] == 2
    assert store["khamsat"]["streak"] == 2


def test_warning_only_after_threshold():
    store = {}
    for _ in range(2):
        health.record(store, {"mostaql": 0}, now=NOW)
    assert health.warnings(store, threshold=3) == []

    health.record(store, {"mostaql": 0}, now=NOW)
    warns = health.warnings(store, threshold=3)
    assert len(warns) == 1
    assert "mostaql" in warns[0]


def test_silent_breakage_is_caught_regression():
    """A scraper whose selectors broke returns 0 forever without raising.
    Without this streak tracking that failure is invisible in the logs."""
    store = {}
    for _ in range(4):
        health.record(store, {"khamsat": 0}, now=NOW)
    assert health.warnings(store, threshold=3)


def test_roundtrip_and_corrupt_file(tmp_path):
    path = tmp_path / "health.json"
    store = health.record({}, {"mostaql": 3}, now=NOW)
    health.save(store, path)
    assert health.load(path)["mostaql"]["streak"] == 0

    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert health.load(bad) == {}
