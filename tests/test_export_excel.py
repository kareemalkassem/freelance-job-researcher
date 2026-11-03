from __future__ import annotations

from datetime import datetime, timezone

from openpyxl import Workbook, load_workbook

from models.job_posting import EXCEL_COLUMNS, JobPosting
from pipeline.export_excel import export

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _sample_jobs():
    strong = JobPosting(
        platform="freelancer",
        job_title="Flutter app",
        job_url="https://www.freelancer.com/projects/flutter-app",
        posted_date=NOW,
        required_skills=["Flutter", "Dart"],
        budget_type="fixed",
        budget_amount="250-750 USD",
        match_score=70,
    )
    strong.fit_score = 92
    strong.fit_reason = "Direct Flutter build"
    strong.ai_summary = "Client needs a delivery app"
    strong.red_flags = "vague timeline"
    strong.is_new = True

    weak = JobPosting(
        platform="mostaql",
        job_title="تطبيق بسيط",
        job_url="https://mostaql.com/project/1",
        posted_date=NOW,
        match_score=20,
    )
    weak.is_new = False
    return [strong, weak]


def _col(name):
    return EXCEL_COLUMNS.index(name) + 1


def test_export_writes_dated_file(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    assert path.name == "freelance_jobs_2026-07-18.xlsx"
    assert path.exists()


def test_export_structure_and_styling(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path).active

    headers = [c.value for c in ws[1]]
    assert len(headers) == len(EXCEL_COLUMNS)
    assert headers[_col("is_new") - 1] == "New"
    assert headers[_col("final_score") - 1] == "Score"

    # Frozen after the identity columns so New + Score stay visible when
    # scrolling right through the AI and client columns.
    assert ws.freeze_panes == "C2"
    assert ws.auto_filter.ref is not None

    assert ws.cell(row=2, column=_col("required_skills")).value == "Flutter, Dart"

    url_cell = ws.cell(row=2, column=_col("job_url"))
    assert url_cell.hyperlink is not None
    assert url_cell.value == "https://www.freelancer.com/projects/flutter-app"


def test_ai_columns_are_written(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path).active
    assert ws.cell(row=2, column=_col("fit_score")).value == 92
    assert ws.cell(row=2, column=_col("fit_reason")).value == "Direct Flutter build"
    assert ws.cell(row=2, column=_col("ai_summary")).value == "Client needs a delivery app"
    assert ws.cell(row=2, column=_col("red_flags")).value == "vague timeline"


def test_final_score_prefers_ai_fit(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path).active
    assert ws.cell(row=2, column=_col("final_score")).value == 92  # AI fit, not 70


def test_new_flag_rendered_only_for_new_jobs(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path).active
    assert ws.cell(row=2, column=_col("is_new")).value == "NEW"
    # openpyxl stores an empty string as an empty cell (None) on read-back.
    assert not ws.cell(row=3, column=_col("is_new")).value


def test_score_cells_are_colour_banded(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path).active
    high = ws.cell(row=2, column=_col("final_score")).fill.fgColor.rgb
    low = ws.cell(row=3, column=_col("final_score")).fill.fgColor.rgb
    assert high != low  # 92 and 20 must not look the same at a glance
    assert "C6EFCE" in str(high)  # strong green band


def test_empty_job_list_still_writes_a_valid_sheet(tmp_path):
    path = export([], tmp_path, now=NOW)
    ws = load_workbook(path)["Jobs"]
    assert [c.value for c in ws[1]][0] == "New"
    assert ws.max_row == 1


# --- presentation -----------------------------------------------------

def test_workbook_has_jobs_and_summary_sheets(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    assert load_workbook(path).sheetnames == ["Jobs", "Summary"]


def test_summary_reports_run_stats(tmp_path):
    stats = {
        "fetched": 125,
        "deduped": 1,
        "enriched": 8,
        "ai_scored": 2,
        "per_platform": {"mostaql": {"raw": 100, "kept": 8}},
    }
    path = export(_sample_jobs(), tmp_path, now=NOW, stats=stats)
    ws = load_workbook(path)["Summary"]
    text = "\n".join(
        str(c.value) for row in ws.iter_rows() for c in row if c.value is not None
    )
    assert "Freelance Job Researcher" in text
    assert "125" in text          # total fetched
    assert "100 fetched → 8 kept" in text
    assert "Score distribution" in text
    assert "Legend" in text


def test_summary_works_without_stats(tmp_path):
    """The stats argument is optional — an older caller must not break."""
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path)["Summary"]
    assert ws.cell(row=1, column=1).value == "Freelance Job Researcher"


def test_title_is_hyperlinked_and_url_stays_copyable(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path)["Jobs"]
    title = ws.cell(row=2, column=_col("job_title"))
    assert title.hyperlink is not None          # click the title to open it
    assert title.value == "Flutter app"
    url = ws.cell(row=2, column=_col("job_url"))
    assert url.value.startswith("https://")     # raw URL still selectable


def test_prose_columns_wrap_and_rows_have_height(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path)["Jobs"]
    assert ws.cell(row=2, column=_col("ai_summary")).alignment.wrap_text is True
    assert ws.cell(row=2, column=_col("job_url")).alignment.wrap_text in (False, None)
    assert ws.row_dimensions[2].height == 32


def test_cells_are_bordered(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path)["Jobs"]
    assert ws.cell(row=2, column=1).border.left.style == "thin"


def test_locked_target_falls_back_to_timestamped_file(tmp_path, monkeypatch):
    """If yesterday's sheet is open in Excel the daily run must still deliver."""
    real_save = Workbook.save
    calls = []

    def flaky_save(self, filename):
        calls.append(str(filename))
        if len(calls) == 1:
            raise PermissionError(13, "locked by Excel")
        return real_save(self, filename)

    monkeypatch.setattr(Workbook, "save", flaky_save)
    path = export(_sample_jobs(), tmp_path, now=NOW)

    assert path.name == "freelance_jobs_2026-07-18_1200.xlsx"
    assert path.exists()
    assert len(calls) == 2


def test_print_setup_is_landscape_fit_to_width(tmp_path):
    path = export(_sample_jobs(), tmp_path, now=NOW)
    ws = load_workbook(path)["Jobs"]
    assert ws.page_setup.orientation == "landscape"
    assert ws.print_title_rows == "$1:$1"  # openpyxl normalises "1:1" on save
