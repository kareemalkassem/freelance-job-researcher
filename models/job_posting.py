"""The common job schema every scraper normalizes into.

One dataclass shared across platforms so the pipeline (score, filter, export)
never has to know whether a row came from Freelancer's JSON or Mostaql's HTML.
Fields map 1:1 to the Excel columns.

Scoring fields fill in progressively as a job moves down the pipeline:
``match_score`` (weighted keywords + client quality, always present) →
``semantic_score`` (optional embedding similarity) → ``fit_score`` /
``fit_reason`` / ``ai_summary`` / ``red_flags`` (optional AI pass). Anything the
pipeline couldn't or wasn't configured to compute stays empty, and the export
still works — no stage is mandatory except scraping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Column order used by the Excel export. Keep in sync with JobPosting fields.
EXCEL_COLUMNS: list[str] = [
    "is_new",
    "final_score",
    "fit_score",
    "match_score",
    "fit_reason",
    "red_flags",
    "platform",
    "job_title",
    "ai_summary",
    "description_summary",
    "posted_date",
    "posted_time_ago",
    "budget_type",
    "budget_amount",
    "required_skills",
    "client_name",
    "client_country",
    "client_rating",
    "client_jobs_posted",
    "hire_rate",
    "contact_info",
    "semantic_score",
    "job_url",
]

# Columns whose header labels read better than the auto Title-Case of the field.
COLUMN_LABELS: dict[str, str] = {
    "is_new": "New",
    "final_score": "Score",
    "fit_score": "AI Fit",
    "match_score": "Keyword Score",
    "fit_reason": "Why It Fits",
    "red_flags": "Red Flags",
    "ai_summary": "AI Summary",
    "semantic_score": "Semantic",
    "job_url": "Job URL",
}


@dataclass
class JobPosting:
    """A single freelance job, normalized to the common schema.

    Only ``platform``, ``job_title`` and ``job_url`` are required — every other
    field defaults to empty/None so a partially-populated Mostaql or Khamsat row
    is still a valid object. ``posted_date`` is timezone-aware UTC when known.
    """

    platform: str
    job_title: str
    job_url: str
    posted_date: datetime | None = None
    posted_time_ago: str = ""
    description_summary: str = ""
    budget_type: str = ""  # "fixed" | "hourly" | ""
    budget_amount: str = ""  # human-readable, e.g. "$250-$750 USD"
    required_skills: list[str] = field(default_factory=list)
    client_name: str = ""
    client_country: str = ""
    client_rating: float | None = None
    client_jobs_posted: int | None = None
    hire_rate: str = ""  # e.g. "80%"
    contact_info: str = ""  # best-effort; usually blank (platform-hidden)

    # --- scoring (filled by the pipeline, never by scrapers) ---
    match_score: int = 0  # weighted keyword + client-quality score, 0-100
    semantic_score: int | None = None  # embedding similarity, 0-100, optional
    fit_score: int | None = None  # AI judgement, 0-100, optional
    fit_reason: str = ""  # one-line AI justification
    ai_summary: str = ""  # one-line AI summary of the job
    red_flags: str = ""  # comma-separated AI-detected warnings
    is_new: bool = True  # False once seen in a previous run

    @property
    def final_score(self) -> int:
        """The number the sheet sorts on: AI fit when available, else keywords.

        The AI pass reads the whole description and judges real fit, so when it
        ran it outranks the keyword heuristic. When it didn't run (disabled,
        offline, over budget) this silently falls back — which is why every
        stage downstream can rely on ``final_score`` always being an int.
        """
        return self.fit_score if self.fit_score is not None else self.match_score
