"""The pipeline itself, with no opinion about how it was started.

``main.py`` used to *be* the pipeline: argument parsing, the seven stages,
logging and the process exit code were one function. That is fine with exactly
one frontend and impossible with two — a GUI cannot recover the workbook path
from an ``int``, and cannot show progress from text already written to stdout.

So the stages live here and return a :class:`RunResult`. ``main.py`` is now a
thin CLI wrapper and ``gui.py`` a thin window; both call :func:`execute` and
render the same result their own way. Nothing here touches ``sys.argv``,
prints, or exits — callers decide what to do with the outcome.

This module sits above both packages on purpose: it is the only place that
imports ``scrapers`` *and* ``pipeline``, which is why it lives in neither.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from models.job_posting import JobPosting
from pipeline import ai as ai_module
from pipeline import dedupe as dedupe_module
from pipeline import embeddings as embeddings_module
from pipeline import export_excel
from pipeline import filter as job_filter
from pipeline import health as health_module
from pipeline import history as history_module
from pipeline import notify as notify_module
from scrapers.errors import classify
from scrapers.freelancer import FreelancerScraper
from scrapers.khamsat import KhamsatScraper
from scrapers.mostaql import MostaqlScraper

log = logging.getLogger("researcher")

# name -> scraper factory. Adding a platform = one entry here + its scraper class.
SCRAPERS = {
    "freelancer": FreelancerScraper,
    "mostaql": MostaqlScraper,
    "khamsat": KhamsatScraper,
}


@dataclass
class RunOptions:
    """Everything a caller can vary per run. Defaults mirror a plain CLI run."""

    platform: str | None = None
    dry_run: bool = False
    no_ai: bool = False
    no_enrich: bool = False
    top: int = 0
    out_dir: str | None = None
    # Override config["lookback_hours"] — the GUI's freshness box.
    lookback_hours: int | None = None
    # Override the Freelancer search terms — the GUI's search-terms box.
    queries: list[str] | None = None


@dataclass
class RunResult:
    """What a run produced. Everything a frontend needs to report, nothing more."""

    exit_code: int = 0
    excel_path: Path | None = None
    jobs: list[JobPosting] = field(default_factory=list)
    fetched: int = 0
    deduped: int = 0
    new_count: int = 0
    per_platform: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)
    health_warnings: list[str] = field(default_factory=list)
    notified: list[str] = field(default_factory=list)

    @property
    def kept(self) -> int:
        return len(self.jobs)

    @property
    def washout(self) -> bool:
        """True when the run delivered nothing — an outage, not a quiet market."""
        return not self.jobs


def _resolve(root: Path, value: str) -> Path:
    """Config paths are relative to the project root unless absolute."""
    p = Path(value)
    return p if p.is_absolute() else root / p


def parse_terms(raw: str | None) -> list[str] | None:
    """Split a comma-separated search-term string into a clean list.

    Both frontends accept the same format: the CLI's --queries flag and the
    GUI's search-terms box. Blank input returns None so the caller falls back
    to config.yaml rather than searching for nothing.
    """
    if not raw:
        return None
    terms = [t.strip() for t in raw.split(",")]
    return [t for t in terms if t] or None


def build_scrapers(config: dict, only: str | None):
    """Instantiate enabled scrapers (optionally just one via ``only``)."""
    platforms = config.get("platforms", {})
    built = []
    for name, cls in SCRAPERS.items():
        if only and name != only:
            continue
        if platforms.get(name, {}).get("enabled", False):
            built.append((name, cls()))
    return built


def fetch_all(config: dict, only: str | None = None):
    """Fetch from each scraper (isolated), then filter+score the combined result.

    Returns ``(raw_counts, failures, all_jobs, kept)``. ``failures`` maps the
    platforms that raised to a short reason (see :mod:`scrapers.errors`) so the
    health report can explain the outage instead of guessing at its cause.
    """
    raw_counts: dict[str, int | None] = {}
    failures: dict[str, str] = {}
    all_jobs = []
    for name, scraper in build_scrapers(config, only):
        try:
            jobs = scraper.fetch(config)
            raw_counts[name] = len(jobs)
            all_jobs.extend(jobs)
            log.info("%s: fetched %d jobs", name, len(jobs))
        except Exception as exc:  # isolate: one bad source must not sink the run
            raw_counts[name] = None
            failures[name] = classify(exc)
            log.warning("%s: skipped [%s] — %s", name, failures[name], exc)
    kept = job_filter.apply(all_jobs, config)
    return raw_counts, failures, all_jobs, kept


def apply_overrides(config: dict, opts: RunOptions) -> dict:
    """Fold per-run overrides into a config dict, in place.

    Kept separate from :func:`execute` so a frontend can show the user exactly
    what a run will use before starting it.
    """
    if opts.no_ai:
        config.setdefault("ai", {})["provider"] = "none"
        config.setdefault("ai", {}).setdefault("semantic", {})["enabled"] = False
    if opts.lookback_hours:
        config["lookback_hours"] = int(opts.lookback_hours)
    if opts.queries:
        config.setdefault("platforms", {}).setdefault("freelancer", {})["queries"] = list(
            opts.queries
        )
    return config


def execute(
    config: dict, opts: RunOptions | None = None, root: Path | None = None
) -> RunResult:
    """Run the full pipeline and return what it produced.

    Never raises for an expected failure (a dead source, a locked workbook) and
    never exits the process — the caller decides how to report. ``exit_code`` is
    1 when the run exported nothing, so a scheduled task still goes red.
    """
    opts = opts or RunOptions()
    root = root or Path(__file__).resolve().parent
    apply_overrides(config, opts)

    out_dir = Path(opts.out_dir or config.get("output_dir", "output"))
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    config["snapshot_dir"] = str(_resolve(root, config.get("snapshot_dir", "logs/snapshots")))

    # --- 1. scrape + gate + score ---------------------------------------
    raw_counts, failures, all_jobs, kept = fetch_all(config, opts.platform)

    # --- 2. detail-page enrichment for the shortlist only ----------------
    enriched_total = 0
    if not opts.no_enrich:
        for name, scraper in build_scrapers(config, opts.platform):
            try:
                count = scraper.enrich(kept, config)
                if count:
                    log.info("%s: enriched %d job(s) from detail pages", name, count)
                    enriched_total += count
            except Exception as exc:  # enrichment is a bonus, never a blocker
                log.warning("%s: enrichment skipped — %s", name, exc)
    if enriched_total:
        kept = job_filter.rescore(kept, config)

    # --- 3. dedupe (after scoring, before paying for AI) ------------------
    kept, removed = dedupe_module.dedupe(kept)

    # --- 4. optional semantic + AI passes --------------------------------
    ai_conf = config.get("ai") or {}
    if ai_conf.get("cache_path"):
        ai_conf["cache_path"] = str(_resolve(root, ai_conf["cache_path"]))
    sem_conf = ai_conf.get("semantic") or {}
    if sem_conf.get("cache_path"):
        sem_conf["cache_path"] = str(_resolve(root, sem_conf["cache_path"]))

    semantic_scored = embeddings_module.enrich(kept, config)
    embeddings_module.blend_into_match_score(kept, config)
    ai_scored, ai_cached, ai_failed = ai_module.enrich(kept, config)
    kept = job_filter.sort_jobs(kept)

    # --- 5. new-since-last-run ------------------------------------------
    hist_conf = config.get("history") or {}
    hist_path = _resolve(root, hist_conf.get("path", "cache/seen_jobs.json"))
    store = history_module.load(hist_path)
    new_count = history_module.mark_new(kept, store)

    # --- 6. report -------------------------------------------------------
    kept_by: dict[str, int] = {}
    for job in kept:
        kept_by[job.platform] = kept_by.get(job.platform, 0) + 1

    lookback = config.get("lookback_hours", 24)
    log.info(
        "kept %d of %d fetched jobs after %sh + keyword filter",
        len(kept),
        len(all_jobs),
        lookback,
    )
    for name, raw in raw_counts.items():
        raw_label = "FAILED" if raw is None else str(raw)
        log.info("  %-11s raw=%s  kept=%d", name, raw_label, kept_by.get(name, 0))
    if removed:
        log.info("deduped %d cross-posted duplicate(s)", removed)
    if semantic_scored:
        log.info("semantic: scored %d job(s)", semantic_scored)
    if ai_scored or ai_cached or ai_failed:
        log.info("ai: %d scored, %d from cache, %d failed", ai_scored, ai_cached, ai_failed)
    log.info("%d of %d are new since the last run", new_count, len(kept))

    health_conf = config.get("health") or {}
    health_path = _resolve(root, health_conf.get("path", "cache/scraper_health.json"))
    health_store = health_module.record(
        health_module.load(health_path), raw_counts, failures=failures
    )
    health_warnings = health_module.warnings(
        health_store, int(health_conf.get("alert_after_runs", 3))
    )
    for warning in health_warnings:
        log.warning("HEALTH  %s", warning)

    if opts.top:
        for job in kept[: opts.top]:
            flag = "NEW " if job.is_new else "    "
            log.info("  %s[%3d] %-8s %s", flag, job.final_score, job.platform, job.job_title[:70])

    result = RunResult(
        jobs=kept,
        fetched=len(all_jobs),
        deduped=removed,
        new_count=new_count,
        failures=failures,
        health_warnings=health_warnings,
        per_platform={
            name: {"raw": "failed" if raw is None else raw, "kept": kept_by.get(name, 0)}
            for name, raw in raw_counts.items()
        },
    )

    # A run that delivered nothing is a failure worth surfacing. Reporting
    # success here is what let an eight-run outage pass unnoticed: the workbook
    # appeared each morning, the scheduled task went green, and only the row
    # count (which nobody checks) said otherwise.
    if result.washout:
        log.error(
            "NO JOBS — every enabled source returned nothing (%s)",
            ", ".join(
                f"{name}={failures.get(name) or ('0 rows' if raw else 'failed')}"
                for name, raw in raw_counts.items()
            )
            or "no sources enabled",
        )
    result.exit_code = 1 if result.washout else 0

    if opts.dry_run:
        log.info("dry-run: no file written")
        return result

    # --- 7. persist + export + notify ------------------------------------
    history_module.save(
        history_module.prune(store, int(hist_conf.get("retention_days", 30))), hist_path
    )
    health_module.save(health_store, health_path)

    stats = {
        "fetched": len(all_jobs),
        "deduped": removed,
        "enriched": enriched_total,
        "ai_scored": ai_scored + ai_cached,
        "per_platform": result.per_platform,
    }
    # The export may divert to a sibling name (locked file, empty-run guard), so
    # the path it returns is the one to report — never the one we expected.
    result.excel_path = export_excel.export(kept, out_dir, stats=stats)
    log.info("wrote %s (%d jobs)", result.excel_path, len(kept))

    result.notified = notify_module.dispatch(kept, config)
    if result.notified:
        log.info("notified via %s", ", ".join(result.notified))
    return result
