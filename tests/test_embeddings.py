from __future__ import annotations

from models.job_posting import JobPosting
from pipeline import embeddings


def _job(title="Flutter app", url="https://x/1"):
    return JobPosting(platform="mostaql", job_title=title, job_url=url, match_score=40)


def _config(tmp_path, **overrides):
    sem = {"enabled": True, "model": "test-embed",
           "cache_path": str(tmp_path / "emb.json"), "blend_weight": 0.0}
    sem.update(overrides)
    return {"ai": {"semantic": sem}}


def test_cosine_similarity_basics():
    assert embeddings.cosine_similarity([1, 0], [1, 0]) == 1.0
    assert embeddings.cosine_similarity([1, 0], [-1, 0]) == -1.0
    assert embeddings.cosine_similarity([1, 0], [0, 1]) == 0.0
    assert embeddings.cosine_similarity([], [1]) == 0.0        # guard
    assert embeddings.cosine_similarity([0, 0], [1, 1]) == 0.0  # zero vector


def test_to_score_maps_to_0_100():
    assert embeddings.to_score(1.0) == 100
    assert embeddings.to_score(-1.0) == 0
    assert embeddings.to_score(0.0) == 50


def test_disabled_by_default():
    job = _job()
    assert embeddings.enrich([job], {"ai": {}}) == 0
    assert job.semantic_score is None


def test_enrich_scores_and_caches(tmp_path):
    calls = []

    def fake_embed(text, conf):
        calls.append(text)
        return [1.0, 0.0] if "Flutter" in text or "Dart" in text else [0.0, 1.0]

    job = _job()
    assert embeddings.enrich([job], _config(tmp_path), embed=fake_embed) == 1
    assert job.semantic_score == 100  # profile and job point the same way

    before = len(calls)
    job2 = _job()
    embeddings.enrich([job2], _config(tmp_path), embed=fake_embed)
    # Only the profile is re-embedded; the job comes from cache.
    assert len(calls) == before + 1
    assert job2.semantic_score == 100


def test_provider_failure_is_not_fatal(tmp_path):
    def boom(text, conf):
        raise RuntimeError("ollama not running")

    job = _job()
    assert embeddings.enrich([job], _config(tmp_path), embed=boom) == 0
    assert job.semantic_score is None


def test_blend_weight_zero_leaves_match_score_alone():
    job = _job()
    job.semantic_score = 100
    embeddings.blend_into_match_score([job], {"ai": {"semantic": {"blend_weight": 0.0}}})
    assert job.match_score == 40


def test_blend_weight_moves_match_score():
    job = _job()
    job.semantic_score = 100
    embeddings.blend_into_match_score([job], {"ai": {"semantic": {"blend_weight": 0.5}}})
    assert job.match_score == 70  # halfway between 40 and 100
