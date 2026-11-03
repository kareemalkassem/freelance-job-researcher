"""Regression tests for how a dead source is reported.

Context: mostaql and khamsat returned HTTP 403 at the load balancer for eight
consecutive daily runs. Every run exited 0 and wrote a workbook containing only
headers, so nothing looked wrong, and the one warning that did fire blamed the
markup and pointed at an empty snapshots directory.

Each test below fails if that behaviour comes back.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from models.job_posting import JobPosting
from pipeline import export_excel, health
from scrapers import http as http_mod
from scrapers.errors import BlockedError, MissingCredential, classify

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


# --- reason plumbing -------------------------------------------------------

def test_http_403_raises_blocked_not_generic_status_error():
    """A 403 is an edge refusal, and must arrive typed so health can say so."""
    def handler(request):
        return httpx.Response(403, text="<html><h1>403 Forbidden</h1></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(BlockedError) as excinfo:
        http_mod.get_with_retry(client, "https://mostaql.com/projects")
    assert excinfo.value.status_code == 403
    assert classify(excinfo.value) == "blocked"


def test_non_blocking_status_still_raises_normally():
    """404 is not a block — it must not be mislabelled as one."""
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        http_mod.get_with_retry(client, "https://mostaql.com/nope")


def test_classify_falls_back_for_unknown_exceptions():
    assert classify(ValueError("boom")) == "error"
    assert classify(MissingCredential("no token")) == "auth"


# --- the misleading warning ------------------------------------------------

def test_blocked_source_warning_does_not_blame_markup():
    """The 403 case must not send you to snapshots that were never written."""
    store = {}
    for _ in range(3):
        health.record(store, {"mostaql": None}, failures={"mostaql": "blocked"}, now=NOW)

    warn = health.warnings(store, threshold=3)[0]
    assert "markup" not in warn.lower()
    assert "snapshot" not in warn.lower()
    assert "blocked" in warn.lower()


def test_auth_failure_warning_names_the_credential_fix():
    store = {}
    for _ in range(3):
        health.record(store, {"freelancer": None}, failures={"freelancer": "auth"}, now=NOW)

    warn = health.warnings(store, threshold=3)[0]
    assert "markup" not in warn.lower()
    assert "credential" in warn.lower() or ".env" in warn.lower()


def test_silent_zero_parse_still_blames_markup():
    """The original diagnosis stays correct when the fetch actually succeeded."""
    store = {}
    for _ in range(3):
        health.record(store, {"khamsat": 0}, now=NOW)

    warn = health.warnings(store, threshold=3)[0]
    assert "markup" in warn.lower()
    assert "snapshot" in warn.lower()


# --- never clobber good data with an empty sheet ---------------------------

def _job(title="Build a Flutter app"):
    return JobPosting(platform="mostaql", job_title=title, job_url="https://x/1")


def test_empty_run_does_not_overwrite_a_populated_workbook(tmp_path):
    """An outage must not destroy the morning's results on a same-day re-run."""
    first = export_excel.export([_job()], tmp_path, now=NOW)
    size_with_data = first.stat().st_size

    second = export_excel.export([], tmp_path, now=NOW)

    assert first.stat().st_size == size_with_data, "populated workbook was clobbered"
    assert second != first, "empty run should divert to a sibling file"
    assert second.exists()


def test_empty_run_still_writes_when_nothing_would_be_lost(tmp_path):
    """With no prior file there is nothing to protect — write normally."""
    path = export_excel.export([], tmp_path, now=NOW)
    assert path.name == "freelance_jobs_2026-08-28.xlsx"


def test_normal_same_day_rerun_still_overwrites(tmp_path):
    """The overwrite-per-day behaviour must survive for runs that found jobs."""
    first = export_excel.export([_job("one")], tmp_path, now=NOW)
    second = export_excel.export([_job("two"), _job("three")], tmp_path, now=NOW)
    assert first == second
