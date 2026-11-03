"""Command-line frontend for the researcher.

The pipeline itself lives in :mod:`runner`; this file only turns arguments into
a :class:`runner.RunOptions`, sets up logging, and turns the result into a
process exit code. The GUI (:mod:`gui`) is the other frontend and does the same
job with widgets instead of argv.

    py main.py                      # full run, writes output/freelance_jobs_<date>.xlsx
    py main.py --dry-run            # fetch + score, print counts, write nothing
    py main.py --platform mostaql   # run a single source
    py main.py --out C:\\some\\dir    # override output directory
    py main.py --no-ai              # skip the AI pass for this run only
    py main.py --top 10             # print the top N matches to the console
    py main.py --lookback-hours 168 # widen the freshness window to a week
    py main.py --queries "Flutter,React,Python"   # override the search terms

Exit code: 0 when the run exported at least one job, 1 when it exported none.
A scheduled task that goes green on an empty workbook hides an outage, so a
total washout is reported as a failure even though the file is still written.

Each scraper is isolated: if one platform's fetch fails (bad markup, network,
missing token), it's logged with the reason and the others still export.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

import runner
from runner import SCRAPERS, RunOptions, parse_terms

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("researcher")


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freelance job researcher")
    parser.add_argument("--dry-run", action="store_true", help="fetch+score, write nothing")
    parser.add_argument("--platform", choices=list(SCRAPERS), help="run a single source")
    parser.add_argument("--out", help="output directory override")
    parser.add_argument("--no-ai", action="store_true", help="skip the AI pass this run")
    parser.add_argument(
        "--no-enrich", action="store_true", help="skip detail-page enrichment this run"
    )
    parser.add_argument("--top", type=int, default=0, help="print the top N matches")
    parser.add_argument(
        "--lookback-hours", type=int, help="override how far back a job may have been posted"
    )
    parser.add_argument(
        "--queries", help="comma-separated Freelancer search terms, overriding config.yaml"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,  # stdout, not stderr: keeps the scheduled task's exit code clean
    )
    load_dotenv(ROOT / ".env")
    config = load_config(ROOT / "config.yaml")

    result = runner.execute(
        config,
        RunOptions(
            platform=args.platform,
            dry_run=args.dry_run,
            no_ai=args.no_ai,
            no_enrich=args.no_enrich,
            top=args.top,
            out_dir=args.out,
            lookback_hours=args.lookback_hours,
            queries=parse_terms(args.queries),
        ),
        root=ROOT,
    )
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
