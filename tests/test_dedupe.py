from __future__ import annotations

from models.job_posting import JobPosting
from pipeline.dedupe import dedupe, normalize_title


def _job(title, client="Ahmed", platform="mostaql", score=10, url=None):
    return JobPosting(
        platform=platform,
        job_title=title,
        job_url=url or f"https://{platform}.com/{abs(hash(title)) % 1000}",
        client_name=client,
        match_score=score,
    )


def test_normalize_strips_punctuation_and_case():
    assert normalize_title("Build a Flutter App!!") == "build a flutter app"


def test_normalize_folds_arabic_variants():
    # أ/إ/آ -> ا and ة -> ه, so these two spellings collapse to one key.
    assert normalize_title("تطبيق أندرويد") == normalize_title("تطبيق اندرويد")
    assert normalize_title("خدمة برمجة") == normalize_title("خدمه برمجة")


def test_cross_platform_duplicate_collapses():
    jobs = [
        _job("Build a Flutter app", platform="mostaql", score=40),
        _job("Build a Flutter app!", platform="khamsat", score=60),
    ]
    survivors, removed = dedupe(jobs)
    assert removed == 1
    assert len(survivors) == 1


def test_highest_scoring_copy_survives():
    jobs = [
        _job("Flutter delivery app", platform="mostaql", score=40),
        _job("Flutter delivery app", platform="khamsat", score=75),
    ]
    survivors, _ = dedupe(jobs)
    assert survivors[0].match_score == 75
    assert survivors[0].platform == "khamsat"


def test_different_clients_are_not_merged():
    jobs = [
        _job("Flutter app", client="Ahmed"),
        _job("Flutter app", client="Sara"),
    ]
    survivors, removed = dedupe(jobs)
    assert removed == 0
    assert len(survivors) == 2


def test_order_is_preserved_for_survivors():
    jobs = [
        _job("Third", score=10),
        _job("First", score=90),
        _job("Second", score=50),
    ]
    survivors, removed = dedupe(jobs)
    assert removed == 0
    assert [j.job_title for j in survivors] == ["Third", "First", "Second"]


def test_untitled_jobs_are_never_merged():
    jobs = [_job("", url="https://a/1"), _job("", url="https://a/2")]
    survivors, removed = dedupe(jobs)
    assert removed == 0
    assert len(survivors) == 2


def test_dedupe_prefers_ai_fit_score_when_present():
    low, high = _job("Same job", score=90), _job("Same job", score=10)
    high.fit_score = 95  # AI says this copy is the good one
    survivors, _ = dedupe([low, high])
    assert survivors[0].fit_score == 95
