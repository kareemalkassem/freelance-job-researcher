from __future__ import annotations

from models.job_posting import JobPosting
from pipeline import scoring

CONFIG = {
    "scoring": {
        "keyword_weights": {"flutter": 4, "getx": 3, "dart": 3, "android": 1},
        "keyword_cap": 10,
        "negative_keywords": {"wordpress": 20, "revenue share": 35},
        "exclude_keywords": ["unpaid"],
        "weights": {"keywords": 0.7, "client": 0.3},
        "client": {
            "unknown_is_neutral": True,
            "good_rating": 4.5,
            "good_hire_rate": 70,
            "good_jobs_posted": 20,
            "good_budget": 500,
            "good_hourly": 25,
        },
        "currency_rates": {"USD": 1.0, "EGP": 0.021},
    }
}


def _job(**kw):
    base = dict(platform="freelancer", job_title="Flutter app", job_url="https://x/1")
    base.update(kw)
    return JobPosting(**base)


def test_score_is_bounded_0_100():
    weak = scoring.compute_match_score(_job(job_title="something"), CONFIG)
    strong = scoring.compute_match_score(
        _job(
            job_title="Flutter GetX Dart Android app",
            client_rating=5.0,
            client_jobs_posted=100,
            hire_rate="95%",
            budget_amount="2000 USD",
            budget_type="fixed",
        ),
        CONFIG,
    )
    assert 0 <= weak <= 100 and 0 <= strong <= 100
    assert strong > weak


def test_weights_make_flutter_beat_android():
    flutter = scoring.compute_match_score(_job(job_title="Flutter developer"), CONFIG)
    android = scoring.compute_match_score(_job(job_title="Android developer"), CONFIG)
    assert flutter > android


def test_negative_keyword_subtracts_points():
    clean = scoring.compute_match_score(_job(job_title="Flutter app"), CONFIG)
    dirty = scoring.compute_match_score(_job(job_title="Flutter app for WordPress site"), CONFIG)
    assert dirty < clean


def test_revenue_share_penalty_regression():
    """Remove the penalty and this equity-only post ranks like a paying job."""
    paid = scoring.compute_match_score(_job(job_title="Flutter app build"), CONFIG)
    unpaid = scoring.compute_match_score(
        _job(job_title="Flutter app build", description_summary="revenue share only"), CONFIG
    )
    assert unpaid < paid


def test_is_excluded():
    assert scoring.is_excluded(_job(job_title="Flutter unpaid work"), CONFIG) is True
    assert scoring.is_excluded(_job(job_title="Flutter paid work"), CONFIG) is False


def test_good_client_outranks_unknown_client_at_equal_keywords():
    title = "Flutter GetX app"
    good = scoring.compute_match_score(
        _job(job_title=title, client_rating=5.0, client_jobs_posted=50, hire_rate="90%"),
        CONFIG,
    )
    unknown = scoring.compute_match_score(_job(job_title=title), CONFIG)
    assert good > unknown


def test_unknown_client_scores_neutral_not_zero():
    """Arabic listings expose no client data; they must not be buried for it."""
    title = "Flutter GetX app"
    neutral = scoring.compute_match_score(_job(job_title=title), CONFIG)

    strict = {"scoring": {**CONFIG["scoring"], "client": {**CONFIG["scoring"]["client"],
                                                          "unknown_is_neutral": False}}}
    zeroed = scoring.compute_match_score(_job(job_title=title), strict)
    assert neutral > zeroed


def test_parse_budget_amount_midpoint_and_currency():
    assert scoring.parse_budget_amount("250-750 USD", {"USD": 1.0}) == 500.0
    assert scoring.parse_budget_amount("15/hr USD", {"USD": 1.0}) == 15.0
    assert scoring.parse_budget_amount("", {}) is None
    assert scoring.parse_budget_amount("no numbers here", {}) is None
    # 3000 EGP converts to ~63 USD rather than looking like a 3000-dollar job.
    assert scoring.parse_budget_amount("3000 EGP", {"EGP": 0.021}) == 63.0


def test_falls_back_to_high_value_keywords_when_no_weights():
    legacy = {"high_value_keywords": ["flutter", "getx"]}
    hit = scoring.compute_match_score(_job(job_title="Flutter GetX"), legacy)
    miss = scoring.compute_match_score(_job(job_title="plain website"), legacy)
    assert hit > miss
