"""Freelancer.com scraper — official REST API (active projects).

Auth is a free OAuth token (header ``Freelancer-OAuth-V1``). One request with
``user_details=true`` embeds the employers, so we read client country/rating
from the same payload instead of an N+1 lookup per project.

``parse_response`` is pure (dict → list[JobPosting]) so it's tested against a
saved JSON fixture with no network.
"""
from __future__ import annotations

import os
import time
import logging

import httpx

from models.job_posting import JobPosting
from pipeline import normalize
from scrapers.errors import MissingCredential
from scrapers.base_scraper import BaseScraper

_BASE = "https://www.freelancer.com/api/projects/0.1/projects/active/"
_PROJECT_URL = "https://www.freelancer.com/projects/{seo_url}"
_HEADERS_UA = "freelance-job-researcher/1.0"
_TIMEOUT = 30.0

log = logging.getLogger("researcher.freelancer")


def _dig(obj: dict, *keys, default=None):
    """Safe nested lookup: _dig(user, 'location', 'country', 'name')."""
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _parse_project(project: dict, users: dict, now=None) -> JobPosting:
    """Map one raw Freelancer project (+ embedded users) into a JobPosting."""
    owner_id = str(project.get("owner_id", ""))
    user = users.get(owner_id, {}) if isinstance(users, dict) else {}

    posted_date = normalize.epoch_to_utc(
        project.get("submitdate") or project.get("time_submitted")
    )
    skills = normalize.clean_skills(
        [j.get("name", "") for j in project.get("jobs", []) or []]
    )
    budget_type = project.get("type", "") or ""
    budget_amount = normalize.format_budget(
        _dig(project, "budget", "minimum"),
        _dig(project, "budget", "maximum"),
        _dig(project, "currency", "code", default="") or "",
        budget_type,
    )
    seo_url = project.get("seo_url", "")
    job_url = _PROJECT_URL.format(seo_url=seo_url) if seo_url else ""

    rating = _dig(user, "employer_reputation", "entire_history", "overall")
    jobs_posted = _dig(user, "employer_reputation", "entire_history", "all")
    completion = _dig(user, "employer_reputation", "entire_history", "completion_rate")

    return JobPosting(
        platform="freelancer",
        job_title=project.get("title", ""),
        job_url=job_url,
        posted_date=posted_date,
        posted_time_ago=normalize.humanize_age(posted_date, now),
        description_summary=normalize.summarize(
            project.get("preview_description") or project.get("description")
        ),
        budget_type=budget_type,
        budget_amount=budget_amount,
        required_skills=skills,
        client_name=_dig(user, "display_name", default="")
        or user.get("username", ""),
        client_country=_dig(user, "location", "country", "name", default=""),
        client_rating=float(rating) if isinstance(rating, (int, float)) else None,
        client_jobs_posted=int(jobs_posted)
        if isinstance(jobs_posted, (int, float))
        else None,
        hire_rate=f"{round(completion * 100)}%"
        if isinstance(completion, (int, float))
        else "",
    )


def parse_response(payload: dict, now=None) -> list[JobPosting]:
    """Pure parse: full API payload → list of JobPosting."""
    result = payload.get("result", {}) or {}
    projects = result.get("projects", []) or []
    users = result.get("users", {}) or {}
    return [_parse_project(p, users, now) for p in projects]


def _queries_from(pconf: dict) -> list[str]:
    """Search terms for this run.

    Accepts ``queries:`` (a list, one request each) or the original scalar
    ``query:``. The API filters server-side, so a term that is never searched
    can never appear in the sheet no matter what the keyword config says.
    """
    raw = pconf.get("queries") or pconf.get("query") or "Flutter"
    if isinstance(raw, str):
        raw = [raw]
    seen, out = set(), []
    for term in raw:
        term = str(term).strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)
    return out


class FreelancerScraper(BaseScraper):
    platform = "freelancer"

    def __init__(self, token: str | None = None):
        self.token = token if token is not None else os.getenv(
            "FREELANCER_OAUTH_TOKEN", ""
        )

    def fetch(self, config: dict) -> list[JobPosting]:
        """Query the public project search.

        The token is **optional**. ``projects/active`` is a public read endpoint
        and answers without any credential; supplying one only raises the rate
        limit. This scraper originally refused to run without a token and so
        reported "no jobs" every day for weeks while the API was working fine —
        a self-inflicted outage. Absent token now means "search anonymously",
        and only a token the server actually *rejects* is a credential fault.
        """
        pconf = config.get("platforms", {}).get("freelancer", {})
        queries = _queries_from(pconf)
        delay = float(config.get("request_delay_seconds", 0) or 0)

        headers = {"User-Agent": _HEADERS_UA}
        if self.token:
            headers["Freelancer-OAuth-V1"] = self.token

        merged: dict[str, JobPosting] = {}
        errors: list[str] = []
        with httpx.Client(timeout=_TIMEOUT) as client:
            for index, term in enumerate(queries):
                if index and delay:
                    time.sleep(delay)  # one shared host; stay polite
                params = {
                    "query": term,
                    "limit": pconf.get("limit", 50),
                    "job_details": "true",
                    "user_details": "true",
                    "full_description": "true",
                }
                try:
                    resp = client.get(_BASE, params=params, headers=headers)
                    if resp.status_code in (401, 403) and self.token:
                        # We sent a token and it was refused — worth naming
                        # precisely, since anonymous access would have worked.
                        raise MissingCredential(
                            f"FREELANCER_OAUTH_TOKEN was rejected (HTTP "
                            f"{resp.status_code}) — clear it from .env to "
                            f"search anonymously, or issue a new one."
                        )
                    resp.raise_for_status()
                    payload = resp.json()
                except MissingCredential:
                    raise  # a bad credential breaks every term, not just this one
                except Exception as exc:
                    # One dud term must not cost the whole profile its results.
                    errors.append(f"{term}: {exc}")
                    log.warning("freelancer: query %r failed — %s", term, exc)
                    continue

                for job in parse_response(payload):
                    # The same project legitimately matches several terms; keep
                    # one copy so raw counts and AI spend reflect real volume.
                    merged.setdefault(job.job_url or job.job_title, job)

        if errors and not merged:
            # Every term failed: that is an outage, not an empty market.
            raise RuntimeError("all freelancer queries failed — " + "; ".join(errors))
        return list(merged.values())
