# Freelance Job Researcher — Completed Features

**Project:** Automated daily scanner for Flutter / mobile-development freelance jobs
**Language:** Python 3.14 (Windows, venv)
**Size:** 1,062 lines · 18 source files · 5 modules
**Tests:** 21 passed in 0.67s
**Date:** 2026-07-24

---

## 1. Job Collection (Scrapers)

### Freelancer.com — Official REST API
- Connects to `projects/active` endpoint with free OAuth token authentication.
- Single request with `user_details=true` embeds all employer data — no extra
  request per project (avoids N+1 lookups).
- Configurable search query, result limit, full descriptions, and job details.
- Extracts: title, description, budget range + type, skills, client name,
  client country, client rating, jobs posted, hire rate, direct project URL.
- `parse_response()` is a pure function (payload → job objects) — no network
  needed to test it.

### Mostaql (مستقل) — HTML Listing Scraper
- Parses the public projects listing with BeautifulSoup + lxml.
- Server-side category filter (`category=development`) so only programming /
  web / app-development projects are fetched.
- Multi-page pagination with a configurable politeness delay between requests.
- Real browser User-Agent and Arabic-first `Accept-Language` headers.
- **Arabic relative-time parser** — converts `منذ ٤ ساعات`, `منذ يوم`,
  `منذ ساعتين` into exact UTC timestamps:
  - Arabic-Indic digit translation (٠١٢٣٤٥٦٧٨٩ → 0123456789)
  - Singular, plural, and dual Arabic forms (ساعة / ساعات / ساعتين)
  - Full unit range: seconds → minutes → hours → days → weeks → months → years
- Primary timestamp source is the precise `<time datetime>` attribute, with the
  Arabic parser as automatic fallback.
- Configurable timezone offset correction for Mostaql's timezone-less timestamps.
- Extracts: title, URL, client name, brief summary, exact posting time.

### Shared Scraper Architecture
- `BaseScraper` abstract contract — every platform implements the same
  `fetch(config) -> list[JobPosting]` signature.
- Adding a new platform = one new class + one line in the registry.
- Every scraper splits **I/O (fetch)** from **parsing (pure function)**, which is
  what makes the whole test suite run offline in under a second.

---

## 2. Unified Data Model

`JobPosting` dataclass — one shared schema so the pipeline never needs to know
whether a row came from Freelancer JSON or Mostaql HTML.

**16 fields, mapped 1:1 to Excel columns:**

| # | Field | # | Field |
|---|-------|---|-------|
| 1 | platform | 9 | client_name |
| 2 | job_title | 10 | client_country |
| 3 | description_summary | 11 | client_rating |
| 4 | posted_date | 12 | client_jobs_posted |
| 5 | posted_time_ago | 13 | hire_rate |
| 6 | budget_type | 14 | contact_info |
| 7 | budget_amount | 15 | match_score |
| 8 | required_skills | 16 | job_url |

Only `platform`, `job_title` and `job_url` are required — every other field
defaults to empty, so a partially-populated row is still a valid record.
`posted_date` is always timezone-aware UTC.

---

## 3. Normalization Engine

Shared, pure helpers used identically by every scraper:

- **`epoch_to_utc()`** — Unix timestamp → timezone-aware UTC datetime, with
  safe handling of invalid/overflow values.
- **`humanize_age()`** — renders `just now` / `12m ago` / `5h ago` / `3d ago`.
- **`summarize()`** — collapses whitespace and truncates to 280 characters,
  cutting on a word boundary (never mid-word) and appending an ellipsis.
- **`clean_skills()`** — trims, drops blanks, and de-duplicates case-insensitively
  while preserving the original order.
- **`format_budget()`** — renders `250-750 USD`, `15/hr USD`, collapses identical
  min/max to a single value, drops trailing `.0`.

---

## 4. Filtering & Scoring Pipeline

### Recency gate (24-hour freshness)
- Configurable lookback window (default 24 hours).
- Inclusive boundary — a job exactly 24h old is kept.
- **Fail-closed design:** a job whose posting date can't be determined is
  dropped, because the core promise of the spreadsheet is "posted in the last
  24 hours" and an unprovable date would break that guarantee.

### Relevance gate (keyword matching)
- Searches across job title + description + skills, case-insensitive.
- 10 configurable keywords shipped: `flutter`, `dart`, `getx`,
  `clean architecture`, `mobile app`, `mobile application`, `ios`, `android`,
  `cross-platform`, `cross platform`.
- Keywords are editable in `config.yaml` — no code change needed.

### Match scoring
- 5 high-value keywords (`flutter`, `getx`, `clean architecture`, `dart`,
  `mobile`) each add 1 point to `match_score`.
- Gives an at-a-glance fit signal so the best-fit jobs are obvious in the sheet.

### Sorting
- Platform ascending, then newest first inside each platform — so the sheet
  always opens grouped and chronologically ordered.

---

## 5. Excel Export

Built directly on openpyxl (not `pandas.to_excel`) for full styling control:

- One sheet named **Jobs**, 16 columns in a fixed, guaranteed order.
- Dark-blue header row (`#1F4E78`) with bold white text.
- **Frozen header row** — column names stay visible while scrolling.
- **Autofilter** across the full used range — filter by platform, score, budget.
- **Auto-sized columns** with per-column width caps so long descriptions
  (60 chars) and URLs (55 chars) never blow out the layout.
- **Clickable `job_url` hyperlinks**, styled blue + underlined — click straight
  through to the job posting.
- Dates rendered as readable `2026-07-24 14:30 UTC`.
- Skills rendered as a comma-separated list.
- Filename is `freelance_jobs_YYYY-MM-DD.xlsx` (UTC date), so re-running on the
  same day overwrites instead of piling up duplicates.
- Output directory is created automatically if missing.

---

## 6. Command-Line Interface

```powershell
py main.py                      # full run -> output\freelance_jobs_<date>.xlsx
py main.py --dry-run            # fetch + filter, print counts, write nothing
py main.py --platform mostaql   # run one source only
py main.py --platform freelancer
py main.py --out D:\jobs        # custom output directory
```

- **Per-source failure isolation** — each scraper runs inside its own
  try/except. A dead API, broken markup, or missing token is logged as
  `FAILED` and the remaining sources still produce the spreadsheet.
- **Run report** printed every time: raw fetched count and kept count per
  platform, plus the total after filtering.

```
mostaql: fetched 50 jobs
kept 4 of 50 fetched jobs after 24h + keyword filter
  freelancer  raw=FAILED  kept=0
  mostaql     raw=50      kept=4
wrote output\freelance_jobs_2026-07-18.xlsx (4 jobs)
```

- Logging goes to **stdout, not stderr**, which keeps the scheduled task's exit
  code clean and meaningful.

---

## 7. Configuration System

Everything tunable lives in `config.yaml` — the search can be re-aimed without
touching a single line of code:

| Setting | Purpose |
|---------|---------|
| `lookback_hours` | Freshness window (default 24) |
| `output_dir` | Where the .xlsx lands |
| `request_delay_seconds` | Politeness delay between requests (2.5s) |
| `keywords` | Relevance gate — which jobs are kept |
| `high_value_keywords` | Which terms feed `match_score` |
| `platforms.*.enabled` | Turn a source on/off |
| `platforms.freelancer.query` / `limit` | API search terms and result cap |
| `platforms.mostaql.category` / `max_pages` | Category filter and page depth |
| `platforms.mostaql.tz_offset_hours` | Timezone correction |

Secrets are kept separately in `.env` (git-ignored), loaded via python-dotenv,
with a committed `.env.example` template.

---

## 8. Daily Automation

`register_task.ps1` — one command registers a Windows Scheduled Task:

```powershell
powershell -ExecutionPolicy Bypass -File .\register_task.ps1          # 08:00 default
powershell -ExecutionPolicy Bypass -File .\register_task.ps1 -At 07:30
```

- Current-user scope — **no administrator rights required**.
- Paths resolved relative to the script itself, so the folder can be moved or
  renamed and the task still works.
- Handles the spaces in the project folder name correctly (single-quoted paths).
- Runs the venv Python directly — no dependence on system PATH.
- Appends **all output streams** to `logs\run.log` for a permanent run history.
- Propagates Python's real exit code so the task reports genuine success/failure.
- Validates the venv exists before registering, with a clear error if it doesn't.
- Documented commands for running it immediately, checking status, and removing it.

---

## 9. Test Suite — 21 tests, all passing

Run with `.\.venv\Scripts\python.exe -m pytest -q` → **21 passed in 0.67s**.

| Suite | Tests | Coverage |
|-------|-------|----------|
| `test_filter.py` | 6 | 24h boundary (inclusive), unknown-date drop, keyword gate, match scoring, sort order |
| `test_normalize.py` | 5 | Epoch conversion, age labels, word-boundary truncation, skill dedupe, budget formatting |
| `test_freelancer.py` | 4 | JSON fixture → JobPosting mapping, client fields, hire-rate percentage |
| `test_mostaql.py` | 4 | HTML fixture → rows, Arabic time parsing, timezone offset |
| `test_export_excel.py` | 2 | Column order, header styling / freeze / hyperlinks |

- Scraper tests run against **saved fixtures** (`tests/fixtures/`) — no network,
  so they are fast, deterministic, and work offline.
- Time-dependent logic is tested with injected `now` values, so tests never
  break as the clock moves.

---

## 10. Documentation

`README.md` covers, in order: what the tool produces, one-time setup, how to get
the Freelancer API token, all run commands, scheduling, every configuration
option, how to run the tests, the file layout, and the known v1 limits.

Every module carries a docstring explaining not just *what* it does but *why*
the design choice was made.

---

## Architecture at a Glance

```
models/job_posting.py     Shared schema — the contract between layers
scrapers/                 base_scraper + freelancer (API) + mostaql (HTML)
pipeline/                 normalize -> filter (24h + keywords) -> export_excel
main.py                   Orchestrate: scrape -> filter -> export
config.yaml / .env        Tuning / secrets
register_task.ps1         Daily Scheduled Task
tests/                    21 fixture-based tests
```

Scrapers are **adapters** that each know one platform's quirks. `JobPosting` is
the **port** they all normalize into. The pipeline only ever touches the port —
which is why one platform failing costs zero functionality on the others, and
why adding a third platform touches exactly two files.
