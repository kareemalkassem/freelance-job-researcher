"""Mostaql (مستقل) scraper — parses the public projects listing HTML.

The listing card gives us title, url, client name, a precise ``<time datetime>``
and an Arabic brief. Budget and skills live on the per-project detail page and
are intentionally NOT fetched in v1 (keeps request volume low; keyword matching
works fine on the rich title+brief text).

Recency comes from the ``datetime`` attribute when present; the shared Arabic
relative-time parser is the fallback for any card missing it.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from models.job_posting import JobPosting
from pipeline import normalize
from scrapers import http as http_util
from scrapers.arabic_time import parse_arabic_relative_time
from scrapers.base_scraper import BaseScraper

# Re-exported for callers/tests that learned this name here before it moved to
# scrapers/arabic_time.py (Khamsat needs the same parser).
__all__ = [
    "parse_arabic_relative_time",
    "parse_listing",
    "parse_detail",
    "MostaqlScraper",
]

log = logging.getLogger("researcher.mostaql")

_BASE = "https://mostaql.com/projects"

# Arabic labels on the project detail page. Matched by substring because the
# markup wraps them in varying amounts of whitespace and nested tags.
_LABEL_BUDGET = "الميزانية"
_LABEL_HIRE_RATE = "معدل التوظيف"
_MONEY = re.compile(r"\d+(?:[.,]\d+)?")


def _parse_iso(value: str, tz_offset_hours: float = 0.0) -> datetime | None:
    """Parse Mostaql's 'YYYY-MM-DD HH:MM:SS' into aware UTC.

    The attribute carries no timezone; ``tz_offset_hours`` says how many hours
    ahead of UTC that wall-clock is (0 = treat as UTC).
    """
    try:
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return None
    return naive.replace(tzinfo=timezone.utc) - timedelta(hours=tz_offset_hours)


def parse_listing(
    html: str, now: datetime | None = None, tz_offset_hours: float = 0.0
) -> list[JobPosting]:
    """Pure parse: HTML string → list of JobPosting. No network."""
    now = now or datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "lxml")
    jobs: list[JobPosting] = []

    for row in soup.select("tr.project-row"):
        link = row.select_one("h2 a")
        if not link:
            continue
        title = link.get_text(strip=True)
        url = (link.get("href") or "").strip()
        if not title or not url:
            continue

        time_el = row.select_one("time[datetime]")
        posted_date = None
        if time_el and time_el.get("datetime"):
            posted_date = _parse_iso(time_el["datetime"], tz_offset_hours)
        if posted_date is None and time_el:
            posted_date = parse_arabic_relative_time(time_el.get_text(" "), now)

        bdi = row.select_one("ul.project__meta bdi")
        client_name = bdi.get_text(strip=True) if bdi else ""

        brief = row.select_one("p.project__brief")
        summary = normalize.summarize(brief.get_text(" ", strip=True)) if brief else ""

        jobs.append(
            JobPosting(
                platform="mostaql",
                job_title=title,
                job_url=url,
                posted_date=posted_date,
                posted_time_ago=normalize.humanize_age(posted_date, now),
                description_summary=summary,
                client_name=client_name,
            )
        )
    return jobs


def _meta_value(soup, label: str) -> str:
    """Read a ``meta-row`` value by its Arabic label.

    The page renders each fact as ``<div class="meta-row"><div class="meta-label">
    الميزانية</div><div class="meta-value">$50.00 - $100.00</div></div>``, and
    ships the whole block twice (desktop + mobile layouts) — the first match is
    always the desktop one and they carry identical values.
    """
    for row in soup.select("div.meta-row"):
        lab = row.select_one("div.meta-label")
        if lab and label in lab.get_text(" ", strip=True):
            val = row.select_one("div.meta-value")
            if val:
                return val.get_text(" ", strip=True)
    return ""


def _table_meta_value(soup, label: str) -> str:
    """Read a value from the client's ``table.table-meta`` sidebar by label."""
    for row in soup.select("table.table-meta tr"):
        cells = row.select("th, td")
        if len(cells) >= 2 and label in cells[0].get_text(" ", strip=True):
            return cells[1].get_text(" ", strip=True)
    return ""


def parse_detail(html: str) -> dict:
    """Pure parse of a project detail page → the fields the listing lacks.

    Returns only what it actually found, so the caller can merge without
    overwriting good listing data with blanks. Budget is rendered in USD on the
    page ("$50.00 - $100.00") and normalized to the same shape Freelancer
    produces, so the scorer's currency handling works identically.
    """
    soup = BeautifulSoup(html, "lxml")
    found: dict = {}

    budget_text = _meta_value(soup, _LABEL_BUDGET)
    if budget_text:
        amounts = [float(m.replace(",", "")) for m in _MONEY.findall(budget_text)]
        if amounts:
            low = amounts[0]
            high = amounts[1] if len(amounts) > 1 else None
            found["budget_amount"] = normalize.format_budget(low, high, "USD", "fixed")
            found["budget_type"] = "fixed"

    skills = [
        a.get_text(" ", strip=True)
        for a in soup.select("ul.skills a[href*='/projects/skill/']")
    ]
    skills = normalize.clean_skills(skills)
    if skills:
        found["required_skills"] = skills

    # Mostaql shows "لم يحسب بعد" ("not calculated yet") for clients with too
    # little history. That's not a rate — keep the column numeric-or-empty so
    # the scorer sees a genuine absence rather than unparseable text.
    hire_rate = _table_meta_value(soup, _LABEL_HIRE_RATE)
    if hire_rate and _MONEY.search(hire_rate):
        found["hire_rate"] = hire_rate

    brief = soup.select_one("div.carda__content")
    if brief:
        text = brief.get_text(" ", strip=True)
        if text:
            found["description_summary"] = normalize.summarize(text)

    return found


class MostaqlScraper(BaseScraper):
    platform = "mostaql"

    def fetch(self, config: dict) -> list[JobPosting]:
        pconf = config.get("platforms", {}).get("mostaql", {})
        category = pconf.get("category", "development")
        max_pages = int(pconf.get("max_pages", 2))
        delay = float(config.get("request_delay_seconds", 2.5))
        tz_offset = float(pconf.get("tz_offset_hours", 0.0))
        snapshot_dir = config.get("snapshot_dir", "logs/snapshots")
        attempts = int(config.get("http_retry_attempts", 3))

        jobs: list[JobPosting] = []
        last_body = ""
        with http_util.build_client() as client:
            for page in range(1, max_pages + 1):
                resp = http_util.get_with_retry(
                    client, _BASE, params={"category": category, "page": page},
                    attempts=attempts,
                )
                last_body = resp.text
                page_jobs = parse_listing(resp.text, tz_offset_hours=tz_offset)
                if not page_jobs:
                    break  # no more results
                jobs.extend(page_jobs)
                if page < max_pages:
                    import time as _time

                    _time.sleep(delay)

        if not jobs and pconf.get("snapshot_on_empty", True) and last_body:
            http_util.snapshot(self.platform, last_body, snapshot_dir)
        return jobs

    def enrich(self, jobs: list[JobPosting], config: dict) -> int:
        """Fetch each kept job's detail page for budget, skills and hire rate.

        This runs *after* filtering, so it costs one request per surviving job
        (a dozen) rather than one per fetched job (a hundred). That's the whole
        reason budget and skills were blank in v1 — walking every listing was
        never worth it, but walking the shortlist is.

        A failure on one job is logged and skipped; the rest still enrich.
        """
        pconf = config.get("platforms", {}).get("mostaql", {})
        if not pconf.get("enrich_details", True):
            return 0

        mine = [j for j in jobs if j.platform == self.platform and j.job_url]
        mine = mine[: int(pconf.get("detail_max_jobs", 25))]
        if not mine:
            return 0

        delay = float(config.get("request_delay_seconds", 2.5))
        attempts = int(config.get("http_retry_attempts", 3))
        enriched = 0

        with http_util.build_client() as client:
            for index, job in enumerate(mine):
                try:
                    resp = http_util.get_with_retry(client, job.job_url, attempts=attempts)
                    details = parse_detail(resp.text)
                except Exception as exc:
                    log.warning("mostaql: detail fetch failed for %s — %s", job.job_url[:60], exc)
                    continue
                if details:
                    for key, value in details.items():
                        setattr(job, key, value)
                    enriched += 1
                if index < len(mine) - 1:
                    time.sleep(delay)

        return enriched
