"""Typed scraper failures, so a caller can tell *why* a source produced nothing.

Every fetch failure used to arrive as a bare exception, which left the health
tracker guessing — it assumed "no jobs" always meant "the markup changed" and
told you to go read snapshots that a network-level refusal never writes. That
advice sent you looking in the wrong place.

The distinction that matters operationally is not the exception's type but who
has to act on it:

* :class:`MissingCredential` — you configure something. No amount of retrying,
  header tweaking or waiting will help.
* :class:`BlockedError` — the host refused the client outright (401/403/451),
  usually at a CDN or load balancer before the application sees the request.
  Nothing was parsed, so there is no HTML to snapshot.
* Anything else — a genuine transport or parse fault, worth investigating in
  the code.
"""
from __future__ import annotations


class ScraperError(Exception):
    """Base for failures a scraper can explain to the pipeline."""

    #: Short machine-readable cause, stored in the health file and used to pick
    #: the remediation text in :func:`pipeline.health.warnings`.
    reason = "error"


class MissingCredential(ScraperError):
    """A required API token or login is absent from the environment."""

    reason = "auth"


class BlockedError(ScraperError):
    """The host refused this client (401/403/451) before serving content."""

    reason = "blocked"

    def __init__(self, message: str, status_code: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


def classify(exc: BaseException) -> str:
    """Map any exception to a health ``reason`` string.

    Unknown exception types fall back to ``"error"`` rather than raising, since
    this runs inside the pipeline's failure path and must never itself fail.
    """
    return getattr(exc, "reason", "error")
