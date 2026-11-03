from __future__ import annotations

from models.job_posting import JobPosting
from pipeline import notify


def _job(title="Flutter app", score=90, is_new=True, url="https://x/1"):
    job = JobPosting(platform="mostaql", job_title=title, job_url=url, match_score=score)
    job.is_new = is_new
    return job


def _config(**overrides):
    conf = {"enabled": True, "channels": ["telegram"], "min_score": 80,
            "max_items": 5, "new_only": True}
    conf.update(overrides)
    return {"notify": conf}


def test_disabled_sends_nothing():
    sent = []
    fired = notify.dispatch([_job()], {"notify": {"enabled": False}},
                            senders={"telegram": lambda t, c: sent.append(t) or True})
    assert fired == [] and sent == []


def test_below_threshold_sends_nothing():
    sent = []
    fired = notify.dispatch([_job(score=50)], _config(),
                            senders={"telegram": lambda t, c: sent.append(t) or True})
    assert fired == [] and sent == []


def test_already_seen_job_does_not_alert_again_regression():
    """Without new_only, the same 90-score job pings you every single morning."""
    sent = []
    fired = notify.dispatch([_job(is_new=False)], _config(),
                            senders={"telegram": lambda t, c: sent.append(t) or True})
    assert fired == [] and sent == []


def test_high_scoring_new_job_fires():
    sent = []
    fired = notify.dispatch([_job()], _config(),
                            senders={"telegram": lambda t, c: sent.append(t) or True})
    assert fired == ["telegram"]
    assert "Flutter app" in sent[0]


def test_max_items_caps_digest():
    jobs = [_job(title=f"Job {i}", url=f"https://x/{i}") for i in range(10)]
    picked = notify.select(jobs, _config(max_items=3))
    assert len(picked) == 3


def test_render_includes_score_url_and_flags():
    job = _job()
    job.fit_reason = "Direct Flutter build"
    job.red_flags = "vague scope"
    text = notify.render([job])
    assert "[90]" in text
    assert "https://x/1" in text
    assert "vague scope" in text
    assert "Direct Flutter build" in text


def test_broken_channel_does_not_break_the_run():
    def boom(text, conf):
        raise RuntimeError("telegram down")

    fired = notify.dispatch([_job()], _config(channels=["telegram", "email"]),
                            senders={"telegram": boom, "email": lambda t, c: True})
    assert fired == ["email"]


def test_unknown_channel_is_ignored():
    fired = notify.dispatch([_job()], _config(channels=["carrier-pigeon"]), senders={})
    assert fired == []
