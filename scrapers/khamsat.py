"""Khamsat (خمسات) scraper — the buyer-requests feed.

Khamsat's main catalogue is *sellers advertising services*, which is the wrong
direction for us. The useful surface is the community requests board
(``/community/requests``), where buyers post what they need — that is the
Khamsat equivalent of a job post.

Two things to know about the markup:

* Each ``tr.forum_post`` carries **two** ``span[title]`` timestamps: the post
  time and the last-interaction time. We scope to the ``li.d-lg-inline-block``
  cell so we read when it was *posted*, not when someone last replied — reading
  the wrong one would make stale requests look fresh and break the 24h gate.
* The board is a single fixed page of the 25 newest requests; ``?page=`` /
  ``?p=`` / ``?offset=`` are all ignored by the server (verified), so there is
  no pagination to walk. Running daily, 25 newest is the whole 24h window.

Requests carry no budget, skills, or brief on the listing — only a title — so
relevance matching for this source runs on the title alone.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup

from models.job_posting import JobPosting
from pipeline import normalize
from scrapers import http as http_util
from scrapers.arabic_time import parse_arabic_relative_time, parse_gmt_title
from scrapers.base_scraper import BaseScraper

_BASE = "https://khamsat.com/community/requests"
_SITE = "https://khamsat.com"


def _absolute(href: str) -> str:
    """Listing hrefs are site-relative; the sheet needs a clickable full URL."""
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"{_SITE}{href}" if href.startswith("/") else f"{_SITE}/{href}"


def _posted_date(row, now: datetime) -> datetime | None:
    """Read the post time, preferring the exact GMT tooltip over relative text."""
    cell = row.select_one("td.details-td li.d-lg-inline-block")
    if cell is None:
        return None
    span = cell.select_one("span[title]")
    if span is not None:
        exact = parse_gmt_title(span.get("title", ""))
        if exact is not None:
            return exact
        return parse_arabic_relative_time(span.get_text(" ", strip=True), now)
    return parse_arabic_relative_time(cell.get_text(" ", strip=True), now)


def parse_listing(html: str, now: datetime | None = None) -> list[JobPosting]:
    """Pure parse: HTML string → list of JobPosting. No network."""
    now = now or datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "lxml")
    jobs: list[JobPosting] = []

    for row in soup.select("tr.forum_post"):
        link = row.select_one("td.details-td h3.details-head a")
        if link is None:
            continue
        title = link.get_text(" ", strip=True)
        url = _absolute(link.get("href", ""))
        if not title or not url:
            continue

        posted_date = _posted_date(row, now)

        author = row.select_one("td.details-td ul.details-list a.user")
        client_name = author.get_text(" ", strip=True) if author else ""

        jobs.append(
            JobPosting(
                platform="khamsat",
                job_title=title,
                job_url=url,
                posted_date=posted_date,
                posted_time_ago=normalize.humanize_age(posted_date, now),
                client_name=client_name,
            )
        )
    return jobs


class KhamsatScraper(BaseScraper):
    platform = "khamsat"

    def fetch(self, config: dict) -> list[JobPosting]:
        pconf = config.get("platforms", {}).get("khamsat", {})
        snapshot_dir = config.get("snapshot_dir", "logs/snapshots")
        attempts = int(config.get("http_retry_attempts", 3))

        with http_util.build_client() as client:
            resp = http_util.get_with_retry(client, _BASE, attempts=attempts)
            jobs = parse_listing(resp.text)

        if not jobs and pconf.get("snapshot_on_empty", True):
            # Fetch worked but nothing parsed -> the markup almost certainly moved.
            http_util.snapshot(self.platform, resp.text, snapshot_dir)
        return jobs
