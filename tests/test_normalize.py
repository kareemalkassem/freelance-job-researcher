from __future__ import annotations

from datetime import datetime, timezone

from pipeline.normalize import (
    clean_skills,
    epoch_to_utc,
    format_budget,
    summarize,
)


def test_epoch_to_utc_valid():
    dt = epoch_to_utc(1_752_840_000)  # a fixed epoch
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_epoch_to_utc_none_and_garbage():
    assert epoch_to_utc(None) is None
    assert epoch_to_utc("not-a-number") is None


def test_summarize_collapses_and_truncates():
    assert summarize("  hello   world \n\t done ") == "hello world done"
    long = "word " * 100
    out = summarize(long, max_chars=20)
    assert len(out) <= 21  # 20 + ellipsis char
    assert out.endswith("…")
    assert summarize(None) == ""


def test_clean_skills_dedupes_case_insensitively_preserving_order():
    assert clean_skills([" Flutter ", "flutter", "Dart", "", None, "GetX"]) == [
        "Flutter",
        "Dart",
        "GetX",
    ]
    assert clean_skills(None) == []


def test_format_budget_variants():
    assert format_budget(250, 750, "USD", "fixed") == "250-750 USD"
    assert format_budget(500, 500, "USD", "fixed") == "500 USD"
    assert format_budget(15, None, "USD", "hourly") == "15/hr USD"
    assert format_budget(None, None, "USD", "fixed") == ""
