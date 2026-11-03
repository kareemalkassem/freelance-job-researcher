"""Write a styled, review-friendly workbook from a list of JobPosting objects.

Two sheets:

* **Jobs** — one row per opportunity, best match first. Built to be triaged
  top-down in under a minute: the score is colour-banded, NEW marks what you
  haven't already reviewed, red flags are tinted, long text wraps instead of
  spilling, and the identity columns stay frozen while you scroll right.
* **Summary** — what this run actually did: per-platform counts, the score
  distribution, a colour legend, and the top matches. It answers "is the tool
  healthy and is today worth my time?" without reading 20 rows.

Uses openpyxl directly (not ``pandas.to_excel``) for full control over styling.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from models.job_posting import COLUMN_LABELS, EXCEL_COLUMNS, JobPosting

log = logging.getLogger("researcher.export")

# Header labels: explicit override where it reads better, else Title Case.
_HEADER_LABELS = {
    col: COLUMN_LABELS.get(col, col.replace("_", " ").title()) for col in EXCEL_COLUMNS
}

# Per-column max width in characters, so description_summary doesn't blow out.
_WIDTH_CAP = {
    "description_summary": 55,
    "ai_summary": 45,
    "fit_reason": 40,
    "red_flags": 26,
    "job_url": 48,
    "job_title": 42,
    "required_skills": 32,
    "is_new": 7,
    "final_score": 8,
    "fit_score": 8,
    "match_score": 9,
    "semantic_score": 10,
}
_DEFAULT_CAP = 28
_MIN_WIDTH = 7

# Columns whose content is prose and must wrap rather than spill.
_WRAP_COLUMNS = {
    "job_title", "description_summary", "ai_summary",
    "fit_reason", "red_flags", "required_skills",
}
_SCORE_COLUMNS = {"final_score", "fit_score", "match_score", "semantic_score"}
_CENTER_COLUMNS = _SCORE_COLUMNS | {"is_new", "posted_time_ago", "budget_type", "hire_rate"}

# --- palette ---------------------------------------------------------------
_INK = "1F4E78"        # dark blue
_HEADER_FILL = PatternFill("solid", fgColor=_INK)
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_LINK_FONT = Font(color="0563C1", underline="single")
_TITLE_FONT = Font(bold=True, color=_INK, size=16)
_SUBTLE = Font(color="808080", size=9)
_BOLD = Font(bold=True)

_ZEBRA_FILL = PatternFill("solid", fgColor="F7F9FC")
_NEW_FILL = PatternFill("solid", fgColor="FFF2CC")
_NEW_FONT = Font(bold=True, color="7F6000")
_FLAG_FILL = PatternFill("solid", fgColor="FFC7CE")
_FLAG_FONT = Font(color="9C0006")

_HAIRLINE = Side(style="thin", color="D9D9D9")
_CELL_BORDER = Border(left=_HAIRLINE, right=_HAIRLINE, top=_HAIRLINE, bottom=_HAIRLINE)

# Score bands, checked high to low. Green = worth opening now.
_SCORE_BANDS: list[tuple[int, str, str]] = [
    (80, "C6EFCE", "Strong — open this first"),
    (60, "E2EFDA", "Good — worth a proposal"),
    (40, "FFEB9C", "Marginal — read before deciding"),
    (0, "F2F2F2", "Weak — passed the gates but barely"),
]

_ROW_HEIGHT = 32
_HEADER_HEIGHT = 26
# Identity columns stay visible when scrolling right: New | Score.
_FREEZE_AT = "C2"


def _cell_value(job: JobPosting, column: str):
    """Map a column name to its display value for a job."""
    if column == "required_skills":
        return ", ".join(job.required_skills)
    if column == "posted_date":
        dt = job.posted_date
        return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else ""
    if column == "is_new":
        return "NEW" if job.is_new else ""
    value = getattr(job, column)
    return "" if value is None else value


def _band_for(score) -> tuple[str, str] | None:
    """(colour, meaning) for a score, or None when there's no score to band."""
    if not isinstance(score, int):
        return None
    for threshold, colour, meaning in _SCORE_BANDS:
        if score >= threshold:
            return colour, meaning
    return None


def export(
    jobs: list[JobPosting],
    output_dir: str | Path,
    now: datetime | None = None,
    stats: dict | None = None,
) -> Path:
    """Write the workbook and return its path.

    Filename is ``freelance_jobs_YYYY-MM-DD.xlsx`` (UTC date), so a re-run on the
    same day overwrites rather than piling up duplicates. ``stats`` is the
    optional run report shown on the Summary sheet.

    One exception to that overwrite rule: a run that found **nothing** never
    replaces a workbook that already holds results. Sources fail intermittently,
    and a total-outage run in the evening must not erase the jobs the morning
    run actually delivered. The empty workbook is still written — it carries the
    Summary sheet explaining the outage — just under a sibling name.
    """
    now = now or datetime.now(timezone.utc)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"freelance_jobs_{now:%Y-%m-%d}.xlsx"

    if not jobs and _has_job_rows(path):
        path = path.with_name(f"{path.stem}_empty_{now:%H%M}{path.suffix}")
        log.warning(
            "run produced 0 jobs but %s already holds results — refusing to "
            "overwrite it; writing %s instead",
            f"freelance_jobs_{now:%Y-%m-%d}.xlsx",
            path.name,
        )

    wb = Workbook()
    jobs_ws = wb.active
    jobs_ws.title = "Jobs"
    jobs_ws.sheet_properties.tabColor = _INK
    _write_jobs_sheet(jobs_ws, jobs)

    summary_ws = wb.create_sheet("Summary")
    summary_ws.sheet_properties.tabColor = "7F7F7F"
    _write_summary_sheet(summary_ws, jobs, now, stats or {})

    return _save(wb, path, now)


def _has_job_rows(path: Path) -> bool:
    """True if an existing workbook's Jobs sheet holds at least one data row.

    Used only to decide whether an empty run may overwrite. Any problem reading
    the file (missing, locked, corrupt, not ours) answers False: the guard is a
    safety net, and a net that can throw would defeat the point.
    """
    if not path.exists():
        return False
    wb = None
    try:
        wb = load_workbook(path, read_only=True)
        ws = wb["Jobs"] if "Jobs" in wb.sheetnames else wb.worksheets[0]
        # Row 1 is the header; openpyxl reports max_row >= 1 for a header-only sheet.
        return any(
            any(cell is not None and str(cell).strip() for cell in row)
            for row in ws.iter_rows(min_row=2, values_only=True)
        )
    except Exception as exc:  # noqa: BLE001 - never let the guard break the export
        log.debug("could not inspect %s for existing rows — %s", path.name, exc)
        return False
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:  # noqa: BLE001
                pass


def _save(wb: Workbook, path: Path, now: datetime) -> Path:
    """Save, falling back to a timestamped name if the target file is locked.

    Excel holds an exclusive lock on an open workbook, so if yesterday's sheet
    is still open the scheduled run would crash with PermissionError and you'd
    get nothing at all. Writing a sibling file instead means the run always
    delivers — the same rule every other optional stage follows.
    """
    try:
        wb.save(path)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{now:%H%M}{path.suffix}")
        wb.save(alt)
        log.warning(
            "%s is locked (open in Excel?) — wrote %s instead", path.name, alt.name
        )
        return alt


def _write_jobs_sheet(ws: Worksheet, jobs: list[JobPosting]) -> None:
    """The main triage table."""
    for col_idx, column in enumerate(EXCEL_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=_HEADER_LABELS[column])
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        cell.border = _CELL_BORDER
    ws.row_dimensions[1].height = _HEADER_HEIGHT

    for row_idx, job in enumerate(jobs, start=2):
        zebra = (row_idx % 2) == 1
        for col_idx, column in enumerate(EXCEL_COLUMNS, start=1):
            value = _cell_value(job, column)
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = _CELL_BORDER
            cell.alignment = Alignment(
                vertical="top",
                horizontal="center" if column in _CENTER_COLUMNS else "left",
                wrap_text=column in _WRAP_COLUMNS,
            )

            styled = False
            if column in _SCORE_COLUMNS:
                band = _band_for(value)
                if band:
                    cell.fill = PatternFill("solid", fgColor=band[0])
                    cell.font = _BOLD if column == "final_score" else Font()
                    styled = True
            elif column == "is_new" and value:
                cell.fill, cell.font, styled = _NEW_FILL, _NEW_FONT, True
            elif column == "red_flags" and value:
                cell.fill, cell.font, styled = _FLAG_FILL, _FLAG_FONT, True
            elif column == "job_title" and job.job_url:
                # The title itself opens the posting; the URL column keeps the
                # raw text so it stays copy-pasteable.
                cell.hyperlink = job.job_url
                cell.font = _LINK_FONT
            elif column == "job_url" and job.job_url:
                cell.hyperlink = job.job_url
                cell.font = _LINK_FONT
            elif column == "client_rating" and isinstance(value, (int, float)):
                cell.number_format = "0.00"

            if zebra and not styled:
                cell.fill = _ZEBRA_FILL
        ws.row_dimensions[row_idx].height = _ROW_HEIGHT

    ws.freeze_panes = _FREEZE_AT
    last_col = get_column_letter(len(EXCEL_COLUMNS))
    ws.auto_filter.ref = f"A1:{last_col}{max(ws.max_row, 1)}"
    _autosize_columns(ws)

    # Print setup: landscape, one page wide, header repeated on every page.
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"


def _write_summary_sheet(
    ws: Worksheet, jobs: list[JobPosting], now: datetime, stats: dict
) -> None:
    """Run metadata, distribution, legend and the shortlist of the shortlist."""
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 46

    row = 1
    ws.cell(row=row, column=1, value="Freelance Job Researcher").font = _TITLE_FONT
    row += 1
    ws.cell(row=row, column=1, value=f"Generated {now:%Y-%m-%d %H:%M} UTC").font = _SUBTLE
    row += 2

    new_count = sum(1 for j in jobs if j.is_new)
    overview = [
        ("Jobs in this sheet", len(jobs)),
        ("New since last run", new_count),
        ("Total fetched", stats.get("fetched", "")),
        ("Duplicates removed", stats.get("deduped", "")),
        ("Detail pages enriched", stats.get("enriched", "")),
        ("AI-scored", stats.get("ai_scored", "")),
    ]
    row = _section(ws, row, "Overview", overview)

    per_platform = stats.get("per_platform") or {}
    if per_platform:
        rows = []
        for name, data in per_platform.items():
            raw = data.get("raw", "?")
            rows.append(
                (name, "fetch failed" if raw == "failed"
                 else f"{raw} fetched → {data.get('kept', 0)} kept")
            )
        row = _section(ws, row, "Sources", rows)

    buckets = [("80-100", 0), ("60-79", 0), ("40-59", 0), ("0-39", 0)]
    counts = dict(buckets)
    for job in jobs:
        score = job.final_score
        key = "80-100" if score >= 80 else "60-79" if score >= 60 else "40-59" if score >= 40 else "0-39"
        counts[key] += 1
    row = _section(ws, row, "Score distribution", [(k, counts[k]) for k, _ in buckets])

    ws.cell(row=row, column=1, value="Legend").font = _BOLD
    row += 1
    for threshold, colour, meaning in _SCORE_BANDS:
        label = ws.cell(row=row, column=1, value=f"{threshold}+" if threshold else "below 40")
        label.fill = PatternFill("solid", fgColor=colour)
        label.border = _CELL_BORDER
        label.alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=3, value=meaning)
        row += 1
    flag = ws.cell(row=row, column=1, value="red flag")
    flag.fill, flag.font, flag.border = _FLAG_FILL, _FLAG_FONT, _CELL_BORDER
    flag.alignment = Alignment(horizontal="center")
    ws.cell(row=row, column=3, value="AI spotted a warning sign — read before applying")
    row += 1
    new_cell = ws.cell(row=row, column=1, value="NEW")
    new_cell.fill, new_cell.font, new_cell.border = _NEW_FILL, _NEW_FONT, _CELL_BORDER
    new_cell.alignment = Alignment(horizontal="center")
    ws.cell(row=row, column=3, value="Not present in any previous run")
    row += 2

    if jobs:
        ws.cell(row=row, column=1, value="Top matches").font = _BOLD
        row += 1
        for job in jobs[:5]:
            score_cell = ws.cell(row=row, column=1, value=job.final_score)
            band = _band_for(job.final_score)
            if band:
                score_cell.fill = PatternFill("solid", fgColor=band[0])
            score_cell.alignment = Alignment(horizontal="center")
            score_cell.border = _CELL_BORDER
            ws.cell(row=row, column=2, value=job.platform)
            title = ws.cell(row=row, column=3, value=job.job_title)
            if job.job_url:
                title.hyperlink = job.job_url
                title.font = _LINK_FONT
            row += 1


def _section(ws: Worksheet, row: int, heading: str, rows: list[tuple]) -> int:
    """Write a labelled block of key/value rows; returns the next free row."""
    ws.cell(row=row, column=1, value=heading).font = _BOLD
    row += 1
    for label, value in rows:
        if value == "":
            continue
        ws.cell(row=row, column=1, value=label)
        cell = ws.cell(row=row, column=2, value=value)
        cell.alignment = Alignment(horizontal="center")
        row += 1
    return row + 1


def _autosize_columns(ws: Worksheet) -> None:
    """Size each column to its longest cell, capped per column.

    Wrapping columns are measured against their cap rather than their content,
    since a 280-character description should occupy its cap width and wrap, not
    demand a 280-character column.
    """
    for col_idx, column in enumerate(EXCEL_COLUMNS, start=1):
        letter = get_column_letter(col_idx)
        cap = _WIDTH_CAP.get(column, _DEFAULT_CAP)
        if column in _WRAP_COLUMNS:
            ws.column_dimensions[letter].width = cap
            continue
        longest = len(_HEADER_LABELS[column])
        for row in range(2, ws.max_row + 1):
            value = ws.cell(row=row, column=col_idx).value
            if value is not None:
                longest = max(longest, len(str(value)))
        ws.column_dimensions[letter].width = max(_MIN_WIDTH, min(longest + 2, cap))
