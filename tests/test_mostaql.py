from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from models.job_posting import JobPosting
from scrapers.mostaql import (
    MostaqlScraper,
    _parse_iso,
    parse_arabic_relative_time,
    parse_detail,
    parse_listing,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mostaql_listing.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "mostaql_detail.html"
NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_listing_extracts_jobs_from_real_page():
    html = FIXTURE.read_text(encoding="utf-8")
    jobs = parse_listing(html, now=NOW)
    assert len(jobs) >= 10  # the saved page has ~25 rows

    first = jobs[0]
    assert first.platform == "mostaql"
    assert first.job_title  # non-empty
    assert first.job_url.startswith("https://mostaql.com/project/")
    assert first.client_name  # e.g. "Mn D."
    # Precise timestamp came from the <time datetime> attribute.
    assert first.posted_date == datetime(2026, 7, 17, 22, 6, 1, tzinfo=timezone.utc)


def test_parse_listing_all_rows_have_dates():
    html = FIXTURE.read_text(encoding="utf-8")
    jobs = parse_listing(html, now=NOW)
    assert all(j.posted_date is not None for j in jobs)


def test_parse_arabic_relative_time_units():
    assert parse_arabic_relative_time("منذ 4 ساعات", now=NOW) == NOW - timedelta(hours=4)
    assert parse_arabic_relative_time("منذ ساعة", now=NOW) == NOW - timedelta(hours=1)
    assert parse_arabic_relative_time("منذ 3 أيام", now=NOW) == NOW - timedelta(days=3)
    assert parse_arabic_relative_time("منذ ٥ دقائق", now=NOW) == NOW - timedelta(minutes=5)
    assert parse_arabic_relative_time("nonsense", now=NOW) is None


def test_parse_iso_timezone_offset():
    assert _parse_iso("2026-07-17 22:06:01", 0) == datetime(
        2026, 7, 17, 22, 6, 1, tzinfo=timezone.utc
    )
    # UTC+3 wall-clock -> subtract 3h to get UTC.
    assert _parse_iso("2026-07-17 22:06:01", 3) == datetime(
        2026, 7, 17, 19, 6, 1, tzinfo=timezone.utc
    )
    assert _parse_iso("garbage", 0) is None


# --- detail-page enrichment -------------------------------------------

DETAIL_HTML = DETAIL_FIXTURE.read_text(encoding="utf-8")


def test_parse_detail_extracts_budget():
    """Budget is the field the listing can never provide — v1 left it blank."""
    details = parse_detail(DETAIL_HTML)
    assert details["budget_amount"] == "50-100 USD"
    assert details["budget_type"] == "fixed"


def test_parse_detail_extracts_skills():
    details = parse_detail(DETAIL_HTML)
    assert "flutter" in details["required_skills"]
    assert "لارافيل" in details["required_skills"]
    # clean_skills de-duplicates the desktop/mobile copies of the same list.
    assert len(details["required_skills"]) == len(set(details["required_skills"]))


def test_parse_detail_extracts_client_hire_rate():
    assert parse_detail(DETAIL_HTML)["hire_rate"] == "7.69%"


def test_parse_detail_rejects_non_numeric_hire_rate():
    """New clients show "لم يحسب بعد" (not calculated yet). That is an absence,
    not a rate — storing it would put Arabic prose in a numeric column."""
    html = DETAIL_HTML.replace("7.69%", "لم يحسب بعد")
    assert "hire_rate" not in parse_detail(html)


def test_parse_detail_extracts_full_description():
    summary = parse_detail(DETAIL_HTML)["description_summary"]
    assert len(summary) > 40
    assert "تطبيق" in summary


def test_parse_detail_returns_only_what_it_found():
    """Missing fields must be absent, not blank — merging blanks would wipe
    good listing data."""
    assert parse_detail("<html><body></body></html>") == {}


def _scraper_config(**overrides):
    mostaql = {"enrich_details": True, "detail_max_jobs": 25}
    mostaql.update(overrides)
    return {"request_delay_seconds": 0, "platforms": {"mostaql": mostaql}}


def _job(url="https://mostaql.com/project/1", platform="mostaql"):
    return JobPosting(platform=platform, job_title="تطبيق", job_url=url)


def test_enrich_merges_detail_fields_into_jobs(monkeypatch):
    scraper = MostaqlScraper()
    monkeypatch.setattr(
        "scrapers.mostaql.http_util.get_with_retry",
        lambda *a, **k: type("R", (), {"text": DETAIL_HTML})(),
    )
    job = _job()
    assert scraper.enrich([job], _scraper_config()) == 1
    assert job.budget_amount == "50-100 USD"
    assert job.hire_rate == "7.69%"
    assert "flutter" in job.required_skills


def test_enrich_ignores_other_platforms(monkeypatch):
    scraper = MostaqlScraper()
    monkeypatch.setattr(
        "scrapers.mostaql.http_util.get_with_retry",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    assert scraper.enrich([_job(platform="khamsat")], _scraper_config()) == 0


def test_enrich_can_be_disabled(monkeypatch):
    scraper = MostaqlScraper()
    monkeypatch.setattr(
        "scrapers.mostaql.http_util.get_with_retry",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    assert scraper.enrich([_job()], _scraper_config(enrich_details=False)) == 0


def test_enrich_respects_detail_max_jobs(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return type("R", (), {"text": DETAIL_HTML})()

    monkeypatch.setattr("scrapers.mostaql.http_util.get_with_retry", fake)
    jobs = [_job(url=f"https://mostaql.com/project/{i}") for i in range(10)]
    assert MostaqlScraper().enrich(jobs, _scraper_config(detail_max_jobs=3)) == 3
    assert len(calls) == 3


def test_enrich_survives_a_failing_detail_page(monkeypatch):
    calls = []

    def flaky(client, url, **k):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("502 from Mostaql")
        return type("R", (), {"text": DETAIL_HTML})()

    monkeypatch.setattr("scrapers.mostaql.http_util.get_with_retry", flaky)
    jobs = [_job(url="https://mostaql.com/project/1"), _job(url="https://mostaql.com/project/2")]
    assert MostaqlScraper().enrich(jobs, _scraper_config()) == 1
    assert jobs[0].budget_amount == ""      # the failed one is untouched
    assert jobs[1].budget_amount == "50-100 USD"
