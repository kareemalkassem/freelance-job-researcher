# Freelance Job Researcher

Scans freelance platforms daily for **Flutter / mobile-development jobs posted in
the last 24 hours**, ranks them 0-100 by how well they actually fit you, and
writes one styled Excel file you can triage in a minute.

**v2 sources:** Freelancer.com (official API) + Mostaql (HTML) + Khamsat buyer
requests (HTML). Ranking is a weighted keyword + client-quality score, with an
optional AI pass that reads each job and scores real fit.

> **Reality check:** client email/phone are hidden by the platforms until a
> contract exists — no scraper changes that. The deliverable is a decision
> spreadsheet plus a `job_url` you apply through. `contact_info` is filled only on
> the rare occasion a client puts contact details directly in the job text.

## What you get

`output/freelance_jobs_YYYY-MM-DD.xlsx` — one row per job, **best match first**,
with a frozen header, autofilter, sized columns and clickable `job_url`.

| Column | What it tells you |
|--------|-------------------|
| **New** | `NEW` if you haven't seen this job in a previous run |
| **Score** | The number to sort by: AI fit when available, else keyword score |
| **AI Fit** / **Why It Fits** | 0-100 AI judgement + one-line reason (if AI enabled) |
| **Keyword Score** | Weighted keyword + client-quality score, 0-100 |
| **Red Flags** | AI-detected warnings — vague scope, unrealistic budget, rush jobs |
| **AI Summary** | One line on what the client actually wants |
| platform, title, description, posted date/age, budget, skills, client name/country/rating/jobs-posted/hire-rate, contact, semantic, url | as before |

Score cells are colour-banded (green ≥80, pale green ≥60, amber ≥40, grey below),
`NEW` is highlighted, and red flags are tinted so you can't skim past them.

## Setup (once)

```powershell
cd "C:\Users\LENOVO\Desktop\freelance job researcher"
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Then get a **free Freelancer API token**: register an app at
<https://www.freelancer.com/developers>, generate an OAuth token, and paste it
into `.env`. Without the token the Freelancer source is skipped (the two Arabic
marketplaces still run).

## Run

### The window (easiest)

Double-click **`Run Researcher.bat`**. Set the lookback hours and search terms,
press **RUN SEARCH**, watch the progress, then click **Open Excel**.

The window reads its defaults from `config.yaml` on every launch, so that file
stays the single source of truth — anything you type in the window applies to
that run only and is never written back.

### The command line

```powershell
py main.py                      # full run -> output\freelance_jobs_<date>.xlsx
py main.py --dry-run            # fetch + score, print counts, write nothing
py main.py --top 10             # also print the top 10 matches to the console
py main.py --platform khamsat   # one source only
py main.py --no-ai              # skip the AI pass for this run
py main.py --out D:\jobs        # custom output directory
py main.py --lookback-hours 168 # widen the freshness window to a week
py main.py --queries "Flutter,React,Python"   # override the search terms
```

Exit code is 0 when at least one job was exported and 1 when none were, so a
scheduled task goes red on an outage instead of quietly reporting success.

Both frontends are thin wrappers over `runner.execute()`, which is where the
pipeline actually lives.

## Pipeline

```
scrape ─► 24h gate ─► keyword gate ─► hard-exclude ─► weighted score
   └─► enrich detail pages ─► re-score ─► dedupe (keep best copy)
       └─► semantic (opt) ─► AI fit (opt)
           └─► mark NEW ─► sort ─► Excel ─► notify (opt)
```

Stage order is deliberate. Enrichment and dedupe both run **after** the gates,
so they only touch the dozen jobs that survived rather than the hundred that
were fetched — and dedupe runs **before** the AI pass so you never pay to judge
the same job twice. Every optional stage degrades to "skipped" on failure; the
run always produces a spreadsheet.

### Detail-page enrichment

Mostaql's listing has no budget, skills or client hire rate — those live on each
project's own page. Fetching all 100 listings' detail pages would take four
minutes and hammer the site; fetching only the ~10 that passed the filter takes
20 seconds. So enrichment runs after the gates, then the affected jobs are
re-scored with the client-quality data that only just became available.

The difference is real: on 2026-07-24 a Flutter OTP job ranked joint-first at 64
on title alone, then dropped to 53 once its detail page revealed a **$50-100**
budget — while a code-review job with a **$100-250** budget and a 50% hire-rate
client rose above it. Disable with `platforms.mostaql.enrich_details: false` or
`py main.py --no-enrich`.

## Scoring

The 0-100 score blends two components (weights in `config.yaml`):

**Keywords (70%)** — each term carries its own weight, so `flutter: 4` outranks
`android: 1`. Negative keywords subtract points (`wordpress: 20`,
`revenue share: 35`), and `exclude_keywords` drop a job entirely.

**Client quality (30%)** — budget, rating, hire rate and jobs-posted, each
compared against a "good" threshold you set. Budgets are converted to USD via
`scoring.currency_rates` so a 3000 EGP job doesn't outrank a 400 USD one on digit
count.

> **The neutral-client rule.** Mostaql and Khamsat expose no rating, budget or
> hire rate on their listings. Scoring that absence as zero would bury every
> Arabic job under every Freelancer job — punishing a source for what the
> *scraper* can't see. So a job with no client signals scores neutral (0.5) on
> that component. Flip `scoring.client.unknown_is_neutral: false` to score
> absence as zero instead.

Because of that rule, Arabic-source jobs realistically land in the 30-70 band.
Tune `notify.min_score` to what you actually see rather than assuming 80+.

## AI fit scoring (optional)

Set `ai.provider` in `config.yaml`:

| Provider | Cost | Setup |
|----------|------|-------|
| `none` (default) | free | nothing — keyword score only |
| `anthropic` | ~cents/month | `ANTHROPIC_API_KEY` in `.env` |
| `ollama` | free | local Ollama running, `model:` set to a pulled model |

One request per job returns fit score, reason, summary and red flags together.
Costs are bounded three ways: only jobs that already passed the keyword gate are
sent, `min_match_score` skips weak ones, `max_jobs_per_run` is a hard ceiling,
and every judgement is cached by URL in `cache/ai_scores.json` forever. Editing
the rubric bumps `PROMPT_VERSION` in `pipeline/ai.py`, which invalidates the
cache cleanly rather than mixing answers to different questions.

If the call fails for any reason — no key, no network, a model that ignored the
JSON schema — the job keeps its keyword score and the run continues.

### Semantic matching (stretch, off by default)

`ai.semantic.enabled: true` embeds each job and compares it to a profile of your
ideal work, catching posts that describe your skills without using your keywords
("cross-platform app in Dart"). Needs a local Ollama with an embedding model
(`nomic-embed-text`). Set `blend_weight` above 0 to fold it into the keyword
score; leave it at 0 to just see the number.

## Alerts (optional)

```yaml
notify:
  enabled: true
  channels: ["telegram"]
  min_score: 60
```

Sends a compact digest of new, high-scoring jobs over Telegram and/or email.
Threshold-gated and `new_only` by default, so it can't turn into a daily ping
about the same jobs. Credentials go in `.env`, never in `config.yaml`.

## Schedule the daily run

```powershell
powershell -ExecutionPolicy Bypass -File .\register_task.ps1          # 08:00 default
powershell -ExecutionPolicy Bypass -File .\register_task.ps1 -At 07:30
```

The task runs the venv Python daily and appends output to `logs\run.log`.
It registers with `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
-StartWhenAvailable`, because Task Scheduler's defaults refuse to start on
battery (error `0x800710E0`) and skip the run entirely if the laptop was asleep —
which silently produces no spreadsheet for days.

`Start-ScheduledTask -TaskName FreelanceJobResearcher` runs it immediately;
`Unregister-ScheduledTask -TaskName FreelanceJobResearcher -Confirm:$false` removes it.

## Reliability

- **Retries** — transient failures (network, 429, 5xx) retry with exponential
  backoff and honour `Retry-After`. A 403/404 fails fast: a block is a real
  answer, not something a retry fixes.
- **Snapshots** — when a fetch succeeds but parses to zero rows (the signature of
  a site changing its markup), the raw HTML is saved to `logs/snapshots/` so you
  can debug offline instead of re-reverse-engineering the page.
- **Health tracking** — `cache/scraper_health.json` counts consecutive empty runs
  per platform and the run log prints a `HEALTH` warning after 3. A broken
  scraper doesn't crash; it quietly returns nothing forever, and this is what
  catches that.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

**99 tests, ~0.6s.** Scraper tests run against saved fixtures (`tests/fixtures/`),
AI/embedding/notify tests inject fakes, and HTTP retry tests use a mock
transport — so the whole suite is offline and deterministic. Live parsing is
exercised by `py main.py --dry-run`.

## Configure

Everything lives in `config.yaml`: `lookback_hours`, `keywords` (relevance gate,
English + Arabic), the whole `scoring` block, `ai` provider and cost caps,
`history` retention, `health` thresholds, `notify` channels, and per-platform
`enabled`/`query`/`limit`/`max_pages`.

## Layout

```
models/job_posting.py      common schema (Excel columns + scoring fields)
scrapers/http.py           shared retry/backoff/snapshot helper
scrapers/arabic_time.py    Arabic relative + GMT timestamp parsing
scrapers/                  base_scraper + freelancer (API) + mostaql + khamsat
pipeline/scoring.py        weighted keywords + client quality -> 0-100
pipeline/filter.py         24h + keyword + exclusion gates, sorting
pipeline/dedupe.py         cross-platform duplicate collapsing
pipeline/ai.py             AI fit scoring (anthropic | ollama | none)
pipeline/embeddings.py     optional semantic similarity
pipeline/history.py        seen-jobs store -> the NEW column
pipeline/health.py         per-platform breakage detection
pipeline/notify.py         Telegram / email digests
pipeline/export_excel.py   styled, colour-banded workbook
main.py                    orchestrate the whole pipeline
config.yaml / .env         tuning / secrets
register_task.ps1          daily Scheduled Task
```

## Known limits

- **Mostaql budget, skills and hire rate** come from detail-page enrichment, so
  they're populated only for jobs that passed the filter and only up to
  `detail_max_jobs`. Clients with too little history show no hire rate at all
  (Mostaql renders "لم يحسب بعد" — not calculated yet), which the scorer treats
  as a genuine absence rather than a zero.
- **Khamsat requests carry title only** — no brief, budget or skills on the board,
  so relevance for that source matches on the title alone. The board also serves
  a fixed 25 newest requests; `?page=`, `?p=` and `?offset=` are all ignored by
  the server (verified), so there is no pagination to walk.
- **Mostaql timestamps** carry no timezone; treated as UTC. If they look off,
  set `platforms.mostaql.tz_offset_hours` in `config.yaml`.
- **Client contact info** is platform-hidden by design; expect it blank.

### Platforms evaluated and not added

| Platform | Why not |
|----------|---------|
| **Upwork** | The RSS job feeds return **HTTP 410 Gone** — Upwork retired them. The official API remains approval-gated. |
| **Bahr (بحر)** | Client-side rendered: the served HTML contains only `animate-pulse` skeleton loaders, no job data. No public JSON API found. Would need a headless browser, which breaks the "no new infrastructure" rule. |
| **Wuzzuf** | Returns **HTTP 403** to non-browser clients, including with a full Chrome header set. Bot-protected. |
| **LinkedIn** | ToS-sensitive and structurally fragile; lowest priority in the roadmap and not worth the maintenance. |
