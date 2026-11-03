"""Shared HTTP plumbing for HTML scrapers.

Every marketplace adapter needs the same four things: browser-ish headers, a
polite delay between pages, retries that don't hammer a struggling host, and a
way to see what the HTML actually looked like when a parse comes back empty.
Putting them here means a new adapter is mostly just CSS selectors.

Retry policy: only *transient* conditions are retried — network errors, 429,
and 5xx. A 403/404 is a real answer (blocked / gone), so it fails fast instead
of burning three attempts on a verdict that won't change. Of those, an
outright refusal (401/403/451) is re-raised as a typed
:class:`~scrapers.errors.BlockedError` so the health tracker can report the
real cause instead of guessing that a selector broke.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from scrapers.errors import BlockedError

log = logging.getLogger("researcher.http")

# A real Chrome UA: several Arabic marketplaces serve a stub or block outright
# when they see a generic Python client.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ar,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

DEFAULT_TIMEOUT = 30.0
_RETRY_STATUS = {429, 500, 502, 503, 504}
# Refusals that mean "not you, not now" — a CDN/WAF verdict, not a missing page.
_BLOCKED_STATUS = {401, 403, 451}


def build_client(headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """An httpx client with the shared header set and redirect following."""
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    return httpx.Client(timeout=timeout, headers=merged, follow_redirects=True)


def get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    attempts: int = 3,
    backoff: float = 1.5,
    sleep=time.sleep,
) -> httpx.Response:
    """GET with exponential backoff on transient failures.

    ``sleep`` is injectable so tests exercise the retry path without waiting.
    Honours ``Retry-After`` when the server sends one. Raises the last error if
    every attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url, params=params)
            if resp.status_code in _RETRY_STATUS and attempt < attempts:
                delay = _retry_after(resp) or backoff ** attempt
                log.info(
                    "%s -> HTTP %d, retrying in %.1fs (attempt %d/%d)",
                    url, resp.status_code, delay, attempt, attempts,
                )
                sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            # A non-retryable status (403/404/410) must surface immediately.
            if isinstance(exc, httpx.HTTPStatusError) and (
                exc.response.status_code not in _RETRY_STATUS
            ):
                status = exc.response.status_code
                if status in _BLOCKED_STATUS:
                    raise BlockedError(
                        f"HTTP {status} — host refused the client before serving "
                        f"content ({url})",
                        status_code=status,
                        url=url,
                    ) from exc
                raise
            last_exc = exc
            if attempt >= attempts:
                break
            delay = backoff ** attempt
            log.info(
                "%s -> %s, retrying in %.1fs (attempt %d/%d)",
                url, type(exc).__name__, delay, attempt, attempts,
            )
            sleep(delay)
    raise last_exc if last_exc else RuntimeError(f"GET failed: {url}")


def _retry_after(resp: httpx.Response) -> float | None:
    """Parse a numeric Retry-After header, ignoring the HTTP-date form."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def snapshot(platform: str, body: str, snapshot_dir: str | Path, reason: str = "empty") -> Path | None:
    """Save a raw response body so a broken parse can be debugged offline.

    Scrapers call this when a fetch succeeds but yields zero rows — the classic
    signature of a site changing its markup. Having yesterday's HTML on disk is
    the difference between "fix it in ten minutes" and "re-reverse-engineer the
    whole page". Never raises: a failed snapshot must not fail the run.
    """
    try:
        out_dir = Path(snapshot_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"{platform}_{reason}_{stamp}.html"
        path.write_text(body, encoding="utf-8")
        log.info("%s: saved raw snapshot -> %s", platform, path)
        return path
    except OSError as exc:
        log.warning("%s: could not write snapshot — %s", platform, exc)
        return None
