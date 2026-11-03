from __future__ import annotations

import json

import pytest

from models.job_posting import JobPosting
from pipeline import ai


def _job(title="Flutter app", score=50, url="https://x/1"):
    return JobPosting(
        platform="freelancer", job_title=title, job_url=url, match_score=score
    )


def _config(tmp_path, **overrides):
    conf = {
        "provider": "fake",
        "model": "test-model",
        "cache_path": str(tmp_path / "ai.json"),
        "min_match_score": 0,
        "max_jobs_per_run": 25,
    }
    conf.update(overrides)
    return {"ai": conf}


GOOD = json.dumps(
    {
        "fit_score": 88,
        "reason": "Direct Flutter build with clear scope",
        "summary": "Client wants a Flutter delivery app",
        "red_flags": ["vague timeline"],
    }
)


# --- response parsing -------------------------------------------------

def test_parse_plain_json():
    out = ai.parse_ai_response(GOOD)
    assert out["fit_score"] == 88
    assert out["red_flags"] == "vague timeline"


def test_parse_json_in_code_fence():
    out = ai.parse_ai_response(f"```json\n{GOOD}\n```")
    assert out["fit_score"] == 88


def test_parse_json_wrapped_in_prose():
    out = ai.parse_ai_response(f"Sure! Here you go:\n{GOOD}\nHope that helps.")
    assert out["fit_score"] == 88


def test_parse_clamps_out_of_range_score():
    assert ai.parse_ai_response('{"fit_score": 250}')["fit_score"] == 100
    assert ai.parse_ai_response('{"fit_score": -10}')["fit_score"] == 0


def test_parse_rejects_unusable_response():
    with pytest.raises(ValueError):
        ai.parse_ai_response("no json at all")
    with pytest.raises(ValueError):
        ai.parse_ai_response('{"reason": "missing the score"}')


def test_parse_accepts_string_red_flags():
    assert ai.parse_ai_response('{"fit_score": 5, "red_flags": "scam"}')["red_flags"] == "scam"


# --- enrich -----------------------------------------------------------

def test_provider_none_is_a_no_op():
    job = _job()
    assert ai.enrich([job], {"ai": {"provider": "none"}}) == (0, 0, 0)
    assert job.fit_score is None


def test_enrich_applies_fields_and_caches(tmp_path):
    calls = []

    def fake(prompt, conf):
        calls.append(prompt)
        return GOOD

    job = _job()
    scored, cached, failed = ai.enrich([job], _config(tmp_path), call_model=fake)
    assert (scored, cached, failed) == (1, 0, 0)
    assert job.fit_score == 88
    assert job.fit_reason.startswith("Direct Flutter")
    assert job.ai_summary
    assert job.red_flags == "vague timeline"

    # Second run reads the cache instead of calling again.
    job2 = _job()
    scored2, cached2, _ = ai.enrich([job2], _config(tmp_path), call_model=fake)
    assert (scored2, cached2) == (0, 1)
    assert job2.fit_score == 88
    assert len(calls) == 1


def test_failure_falls_back_to_keyword_score(tmp_path):
    def boom(prompt, conf):
        raise RuntimeError("API down")

    job = _job(score=42)
    scored, cached, failed = ai.enrich([job], _config(tmp_path), call_model=boom)
    assert (scored, cached, failed) == (0, 0, 1)
    assert job.fit_score is None
    assert job.final_score == 42  # still ranks on the keyword score


def test_min_match_score_gates_spending(tmp_path):
    def fake(prompt, conf):
        raise AssertionError("should not be called")

    job = _job(score=10)
    assert ai.enrich([job], _config(tmp_path, min_match_score=50), call_model=fake) == (0, 0, 0)


def test_max_jobs_per_run_caps_cost(tmp_path):
    calls = []

    def fake(prompt, conf):
        calls.append(1)
        return GOOD

    jobs = [_job(url=f"https://x/{i}") for i in range(5)]
    scored, _, _ = ai.enrich(jobs, _config(tmp_path, max_jobs_per_run=2), call_model=fake)
    assert scored == 2
    assert len(calls) == 2
    assert jobs[4].fit_score is None


def test_prompt_version_change_invalidates_cache(tmp_path, monkeypatch):
    def fake(prompt, conf):
        return GOOD

    job = _job()
    ai.enrich([job], _config(tmp_path), call_model=fake)

    monkeypatch.setattr(ai, "PROMPT_VERSION", "v2")
    job2 = _job()
    scored, cached, _ = ai.enrich([job2], _config(tmp_path), call_model=fake)
    assert (scored, cached) == (1, 0)  # recomputed, not reused


def test_corrupt_cache_starts_fresh(tmp_path):
    path = tmp_path / "ai.json"
    path.write_text("{not json", encoding="utf-8")
    assert ai.load_cache(path) == {}
