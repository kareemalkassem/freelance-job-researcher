"""The seam that makes every source look identical to the pipeline.

A scraper's only job is: given config, return a list of already-normalized
``JobPosting`` objects. Whether it did that by hitting a JSON API (Freelancer)
or parsing HTML (Mostaql) is its own business. Adding Upwork later means writing
one more subclass — nothing in the pipeline changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from models.job_posting import JobPosting


class BaseScraper(ABC):
    #: Short platform label, e.g. "freelancer" / "mostaql". Set on each subclass.
    platform: str = "unknown"

    @abstractmethod
    def fetch(self, config: dict) -> list[JobPosting]:
        """Return normalized postings for this platform.

        Implementations must not raise on an empty result — return ``[]``.
        Network/parse failures may raise; ``main.py`` isolates them per-scraper.
        """
        raise NotImplementedError

    def enrich(self, jobs: list[JobPosting], config: dict) -> int:
        """Optionally fill in fields that only exist on a job's detail page.

        Called with **only the jobs that survived the filter**, so a source that
        needs one request per job costs a dozen requests instead of a hundred.
        Returns how many jobs were enriched.

        The default is a no-op: a listing that already carries everything (the
        Freelancer API) has nothing to add, and a source whose detail pages
        aren't worth the requests can simply not implement this.
        """
        return 0
