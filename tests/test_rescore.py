from __future__ import annotations

from models.job_posting import JobPosting
from pipeline import filter as job_filter

CONFIG = {
    "scoring": {
        "keyword_weights": {"flutter": 4},
        "keyword_cap": 10,
        "weights": {"keywords": 0.7, "client": 0.3},
        "client": {
            "unknown_is_neutral": True,
            "good_rating": 4.5,
            "good_hire_rate": 70,
            "good_jobs_posted": 20,
            "good_budget": 500,
            "good_hourly": 25,
        },
        "currency_rates": {"USD": 1.0},
    }
}


def _job(**kw):
    base = dict(platform="mostaql", job_title="Flutter app", job_url="https://x/1")
    base.update(kw)
    return JobPosting(**base)


def test_rescore_picks_up_budget_from_enrichment():
    """Before enrichment a Mostaql job has no budget and scores neutral on the
    client component; after enrichment the real budget must move the score."""
    job = _job()
    job.match_score = job_filter.compute_match_score(job, CONFIG)
    before = job.match_score

    job.budget_amount = "2000 USD"  # what the detail page revealed
    job.budget_type = "fixed"
    job_filter.rescore([job], CONFIG)

    assert job.match_score > before


def test_rescore_reflects_a_bad_hire_rate():
    good, bad = _job(), _job()
    good.hire_rate = "95%"
    bad.hire_rate = "5%"
    job_filter.rescore([good, bad], CONFIG)
    assert good.match_score > bad.match_score


def test_rescore_returns_list_sorted_best_first():
    low, high = _job(job_title="app"), _job(job_title="Flutter app")
    ordered = job_filter.rescore([low, high], CONFIG)
    assert ordered[0] is high


def test_rescore_is_idempotent():
    job = _job(budget_amount="500 USD", budget_type="fixed")
    job_filter.rescore([job], CONFIG)
    once = job.match_score
    job_filter.rescore([job], CONFIG)
    assert job.match_score == once
