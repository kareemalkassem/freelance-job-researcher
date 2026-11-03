from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from scrapers.errors import MissingCredential
from scrapers.freelancer import FreelancerScraper, parse_response

FIXTURE = Path(__file__).parent / "fixtures" / "freelancer_response.json"
NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class _FakeResponse:
    """Minimal stand-in for httpx.Response covering what fetch() touches."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://www.freelancer.com/api"),
                response=httpx.Response(self.status_code),
            )


def test_parse_response_maps_all_fields():
    jobs = parse_response(_payload(), now=NOW)
    assert len(jobs) == 2

    flutter = jobs[0]
    assert flutter.platform == "freelancer"
    assert flutter.job_title == "Build a Flutter mobile app with GetX"
    assert flutter.job_url == (
        "https://www.freelancer.com/projects/mobile-phone/build-flutter-app-39000001"
    )
    assert flutter.budget_type == "fixed"
    assert flutter.budget_amount == "250-750 USD"
    assert flutter.required_skills == ["Flutter", "Dart", "Mobile App Development"]
    assert flutter.client_name == "Acme Apps"
    assert flutter.client_country == "United States"
    assert flutter.client_rating == 4.8
    assert flutter.client_jobs_posted == 42
    assert flutter.hire_rate == "90%"
    assert flutter.posted_date is not None
    assert flutter.posted_date.tzinfo == timezone.utc


def test_parse_response_hourly_budget_suffix():
    react = parse_response(_payload(), now=NOW)[1]
    assert react.budget_type == "hourly"
    assert react.budget_amount == "15-30/hr USD"


def test_parse_response_empty_payload():
    assert parse_response({}, now=NOW) == []


def test_fetch_without_token_still_returns_jobs(monkeypatch):
    """The public search endpoint needs no auth.

    Regression: this scraper used to refuse to run without an OAuth token and
    reported "last success: never" for 17 consecutive days. The token was never
    required — verified live against projects/active, which answers 200 with
    real projects and no credential. Refusing to try was the entire bug.
    """
    captured = {}

    def fake_get(self, url, params=None, headers=None):
        captured["headers"] = headers or {}
        captured["url"] = url
        return _FakeResponse(_payload())

    monkeypatch.setattr("httpx.Client.get", fake_get)
    jobs = FreelancerScraper(token="").fetch({})

    assert len(jobs) == 2
    assert "Freelancer-OAuth-V1" not in captured["headers"], (
        "no auth header should be sent when no token is configured"
    )


def test_fetch_with_token_sends_auth_header(monkeypatch):
    """A configured token is still used — it raises the rate limit."""
    captured = {}

    def fake_get(self, url, params=None, headers=None):
        captured["headers"] = headers or {}
        return _FakeResponse(_payload())

    monkeypatch.setattr("httpx.Client.get", fake_get)
    FreelancerScraper(token="secret-token").fetch({})
    assert captured["headers"].get("Freelancer-OAuth-V1") == "secret-token"


def test_rejected_token_is_reported_as_credential_problem(monkeypatch):
    """A *bad* token is still a credential fault, unlike an absent one."""
    def fake_get(self, url, params=None, headers=None):
        return _FakeResponse({"status": "error"}, status_code=401)

    monkeypatch.setattr("httpx.Client.get", fake_get)
    with pytest.raises(MissingCredential):
        FreelancerScraper(token="expired").fetch({})


def test_multiple_queries_are_all_fetched_and_merged(monkeypatch):
    """One server-side query caps what the profile can ever match.

    The API filters server-side, so a config listing Python/React keywords still
    sees only Flutter results unless each term is actually searched. Regression
    guard for a skills profile silently reduced to its first entry.
    """
    seen = []

    def fake_get(self, url, params=None, headers=None):
        seen.append(params["query"])
        return _FakeResponse(_payload())

    monkeypatch.setattr("httpx.Client.get", fake_get)
    scraper = FreelancerScraper(token="")
    jobs = scraper.fetch(
        {"platforms": {"freelancer": {"queries": ["Flutter", "React", "Python"]}}},
    )

    assert seen == ["Flutter", "React", "Python"]
    # Same fixture each time: identical jobs must collapse, not triple.
    assert len(jobs) == 2


def test_single_query_string_still_supported(monkeypatch):
    """Back-compat: the original scalar `query:` key keeps working."""
    seen = []

    def fake_get(self, url, params=None, headers=None):
        seen.append(params["query"])
        return _FakeResponse(_payload())

    monkeypatch.setattr("httpx.Client.get", fake_get)
    FreelancerScraper(token="").fetch(
        {"platforms": {"freelancer": {"query": "Laravel"}}}
    )
    assert seen == ["Laravel"]


def test_one_failing_query_does_not_lose_the_others(monkeypatch):
    """A bad term must not sink the whole profile's worth of results."""
    def fake_get(self, url, params=None, headers=None):
        if params["query"] == "React":
            raise httpx.ConnectError("boom")
        return _FakeResponse(_payload())

    monkeypatch.setattr("httpx.Client.get", fake_get)
    jobs = FreelancerScraper(token="").fetch(
        {"platforms": {"freelancer": {"queries": ["Flutter", "React"]}}}
    )
    assert len(jobs) == 2, "surviving queries should still return their jobs"
