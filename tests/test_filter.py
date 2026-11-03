from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models.job_posting import JobPosting
from pipeline.filter import apply, compute_match_score, is_recent, is_relevant

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _job(title="Flutter app needed", hours_ago=1.0, platform="freelancer", skills=None):
    return JobPosting(
        platform=platform,
        job_title=title,
        job_url="https://example.com/1",
        posted_date=NOW - timedelta(hours=hours_ago),
        required_skills=skills or ["Flutter"],
    )


def test_is_recent_boundary():
    assert is_recent(_job(hours_ago=23), 24, now=NOW) is True
    assert is_recent(_job(hours_ago=24), 24, now=NOW) is True  # inclusive boundary
    assert is_recent(_job(hours_ago=25), 24, now=NOW) is False


def test_is_recent_unknown_date_is_not_recent():
    job = _job()
    job.posted_date = None
    assert is_recent(job, 24, now=NOW) is False


def test_is_relevant():
    assert is_relevant(_job(title="Build a Flutter app"), ["flutter"]) is True
    assert is_relevant(_job(title="WordPress site", skills=["PHP"]), ["flutter"]) is False
    assert is_relevant(_job(), []) is True  # no filter -> keep


def test_compute_match_score_uses_weights_from_config():
    config = {"scoring": {"keyword_weights": {"flutter": 4, "getx": 3}, "keyword_cap": 10}}
    # skills=[] so the default "Flutter" skill doesn't leak into the weak job.
    strong = compute_match_score(_job(title="Flutter GetX app", skills=[""]), config)
    weak = compute_match_score(_job(title="GetX only", skills=[""]), config)
    assert strong > weak


def test_apply_keeps_fresh_relevant_and_sorts_best_first():
    config = {
        "lookback_hours": 24,
        "keywords": ["flutter", "dart"],
        "scoring": {"keyword_weights": {"flutter": 4, "getx": 3, "dart": 3}},
    }
    jobs = [
        _job(title="Old Flutter job", hours_ago=48),
        _job(title="Fresh Flutter GetX job", hours_ago=2, platform="mostaql"),
        _job(title="Fresh WordPress job", hours_ago=1, skills=["PHP"]),
        _job(title="Recent Dart task", hours_ago=5, skills=["Dart"]),
    ]
    kept = apply(jobs, config, now=NOW)

    titles = [j.job_title for j in kept]
    assert "Old Flutter job" not in titles        # too old
    assert "Fresh WordPress job" not in titles    # not relevant
    assert titles[0] == "Fresh Flutter GetX job"  # highest score first
    assert kept[0].match_score > kept[1].match_score


def test_stale_job_is_excluded_regression():
    """If the recency gate is removed, this 25h-old job would leak through."""
    config = {"lookback_hours": 24, "keywords": ["flutter"]}
    stale = _job(title="Flutter app", hours_ago=25)
    assert apply([stale], config, now=NOW) == []


def test_hard_exclude_keyword_drops_job_regression():
    """A job that matches keywords but is unpaid must never reach the sheet."""
    config = {
        "lookback_hours": 24,
        "keywords": ["flutter"],
        "scoring": {"exclude_keywords": ["unpaid"]},
    }
    job = _job(title="Flutter app, unpaid internship", hours_ago=1)
    assert apply([job], config, now=NOW) == []
