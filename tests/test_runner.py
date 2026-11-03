"""Tests for the pipeline seam that both frontends sit on.

``main.py`` had no test coverage at all while it *was* the pipeline — the whole
orchestration was reachable only by running the program. Extracting
:func:`runner.execute` made that logic addressable, so these cover the parts a
frontend actually depends on: overrides landing where they claim to, the
workbook path coming back, and a washout reporting failure rather than success.
"""
from __future__ import annotations

import openpyxl
import pytest

import runner
from models.job_posting import JobPosting
from runner import RunOptions


def _job(title="Build a Flutter app", score=80):
    # final_score is a read-only property deriving from fit_score/match_score.
    return JobPosting(
        platform="freelancer",
        job_title=title,
        job_url=f"https://x/{title}",
        match_score=score,
    )


@pytest.fixture
def base_config(tmp_path):
    return {
        "output_dir": str(tmp_path / "out"),
        "snapshot_dir": str(tmp_path / "snaps"),
        "lookback_hours": 24,
        "keywords": ["flutter"],
        "history": {"path": str(tmp_path / "seen.json")},
        "health": {"path": str(tmp_path / "health.json")},
        "platforms": {"freelancer": {"enabled": True, "query": "Flutter"}},
    }


# --- overrides -------------------------------------------------------------

def test_lookback_override_replaces_config_value(base_config):
    runner.apply_overrides(base_config, RunOptions(lookback_hours=168))
    assert base_config["lookback_hours"] == 168


def test_queries_override_reaches_the_freelancer_platform(base_config):
    runner.apply_overrides(base_config, RunOptions(queries=["React", "Python"]))
    assert base_config["platforms"]["freelancer"]["queries"] == ["React", "Python"]


def test_no_overrides_leaves_config_untouched(base_config):
    runner.apply_overrides(base_config, RunOptions())
    assert base_config["lookback_hours"] == 24
    assert "queries" not in base_config["platforms"]["freelancer"]


def test_parse_terms_splits_trims_and_drops_blanks():
    assert runner.parse_terms(" Flutter , React ,, Python ") == [
        "Flutter", "React", "Python",
    ]


def test_parse_terms_blank_returns_none_so_config_wins():
    """Empty input must fall back to config.yaml, never search for nothing."""
    assert runner.parse_terms("") is None
    assert runner.parse_terms("   ,  , ") is None
    assert runner.parse_terms(None) is None


# --- execute ---------------------------------------------------------------

def test_execute_returns_the_path_actually_written(base_config, tmp_path, monkeypatch):
    """A frontend must report the real path, which may be a diverted sibling."""
    monkeypatch.setattr(
        runner, "fetch_all", lambda config, only=None: ({"freelancer": 1}, {}, [_job()], [_job()])
    )
    monkeypatch.setattr(runner, "build_scrapers", lambda config, only: [])

    result = runner.execute(base_config, RunOptions(), root=tmp_path)

    assert result.excel_path is not None
    assert result.excel_path.exists()
    assert result.kept == 1
    assert result.exit_code == 0
    assert not result.washout
    wb = openpyxl.load_workbook(result.excel_path)
    assert wb["Jobs"].max_row == 2  # header + one job


def test_execute_reports_failure_when_nothing_was_exported(base_config, tmp_path, monkeypatch):
    """Regression: a washout used to exit 0 and look like a healthy run."""
    monkeypatch.setattr(
        runner,
        "fetch_all",
        lambda config, only=None: ({"freelancer": None}, {"freelancer": "blocked"}, [], []),
    )
    monkeypatch.setattr(runner, "build_scrapers", lambda config, only: [])

    result = runner.execute(base_config, RunOptions(), root=tmp_path)

    assert result.washout
    assert result.exit_code == 1
    assert result.failures == {"freelancer": "blocked"}


def test_dry_run_writes_nothing_but_still_reports(base_config, tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner, "fetch_all", lambda config, only=None: ({"freelancer": 1}, {}, [_job()], [_job()])
    )
    monkeypatch.setattr(runner, "build_scrapers", lambda config, only: [])

    result = runner.execute(base_config, RunOptions(dry_run=True), root=tmp_path)

    assert result.excel_path is None
    assert result.kept == 1
