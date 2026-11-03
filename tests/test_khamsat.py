from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scrapers.khamsat import parse_listing

FIXTURE = Path(__file__).parent / "fixtures" / "khamsat_requests.html"
HTML = FIXTURE.read_text(encoding="utf-8")

# The fixture was captured 2026-07-24 ~17:30 UTC.
NOW = datetime(2026, 7, 24, 17, 30, 0, tzinfo=timezone.utc)


def test_parses_all_request_rows():
    jobs = parse_listing(HTML, now=NOW)
    assert len(jobs) == 5
    assert all(j.platform == "khamsat" for j in jobs)


def test_titles_and_absolute_urls():
    jobs = parse_listing(HTML, now=NOW)
    titles = [j.job_title for j in jobs]
    assert "مبرمج مختص لارفيل وفلاتر" in titles
    assert all(j.job_url.startswith("https://khamsat.com/community/requests/") for j in jobs)


def test_exact_gmt_timestamp_is_used():
    jobs = parse_listing(HTML, now=NOW)
    by_title = {j.job_title: j for j in jobs}
    job = by_title["مبرمج مختص لارفيل وفلاتر"]
    assert job.posted_date == datetime(2026, 7, 24, 16, 9, 9, tzinfo=timezone.utc)


def test_uses_post_time_not_last_interaction_regression():
    """Each row carries two span[title] stamps: post time and last interaction.

    Reading the last-interaction stamp would make this 12-day-old request look
    fresh and leak past the 24h gate. It must resolve to its 12/07 post time.
    """
    jobs = parse_listing(HTML, now=NOW)
    stale = next(j for j in jobs if j.job_title == "إنجاز مشاريع database")
    assert stale.posted_date == datetime(2026, 7, 12, 18, 37, 14, tzinfo=timezone.utc)
    assert (NOW - stale.posted_date).days >= 11


def test_client_name_extracted():
    jobs = parse_listing(HTML, now=NOW)
    by_title = {j.job_title: j for j in jobs}
    assert by_title["مبرمج مختص لارفيل وفلاتر"].client_name == ".Fadi A"


def test_humanized_age_present():
    jobs = parse_listing(HTML, now=NOW)
    job = next(j for j in jobs if j.job_title == "مبرمج مختص لارفيل وفلاتر")
    assert job.posted_time_ago.endswith("ago")


def test_empty_html_yields_no_jobs():
    assert parse_listing("<html><body></body></html>", now=NOW) == []
