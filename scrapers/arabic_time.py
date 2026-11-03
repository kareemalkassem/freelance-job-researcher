"""Arabic date/time parsing shared by the Arabic-marketplace scrapers.

Mostaql and Khamsat both render posting times in Arabic relative form
("منذ 4 ساعات") and both sometimes expose a precise machine timestamp in an
attribute. Parsing lives here so the two adapters can't drift apart — a bug in
the plural handling would otherwise have to be fixed twice.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Arabic-Indic digits -> ASCII, so "٤ ساعات" parses like "4 ساعات".
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# Seconds per Arabic time unit. Arabic pluralizes by changing internal letters
# (ساعة -> ساعات, دقيقة -> دقائق), so singular and plural are listed explicitly
# rather than matched by a shared stem. Dual forms ("ساعتين" = 2h) carry no digit
# and are approximated as 1 unit — negligible inside a 24h window.
ARABIC_UNIT_SECONDS: list[tuple[str, int]] = [
    ("ثانية", 1), ("ثواني", 1),
    ("دقيقة", 60), ("دقائق", 60), ("دقيقتين", 60),
    ("ساعة", 3600), ("ساعات", 3600), ("ساعتين", 3600),
    ("أيام", 86_400), ("يومين", 86_400), ("يوم", 86_400),
    ("أسبوع", 604_800), ("اسبوع", 604_800), ("أسابيع", 604_800), ("اسابيع", 604_800),
    ("أشهر", 2_592_000), ("شهور", 2_592_000), ("شهر", 2_592_000),
    ("سنوات", 31_536_000), ("سنة", 31_536_000), ("أعوام", 31_536_000), ("عام", 31_536_000),
]


def parse_arabic_relative_time(
    text: str, now: datetime | None = None
) -> datetime | None:
    """Convert 'منذ 4 ساعات' / 'منذ يوم' → an absolute UTC datetime.

    Returns None if no recognizable unit is found. A missing count defaults to 1
    ('منذ ساعة' = one hour ago). Compound forms ("منذ 4 ساعات و26 دقيقة") use the
    largest unit only — the remainder never changes a 24h in/out decision.
    """
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    normalized = text.translate(ARABIC_DIGITS)
    num_match = re.search(r"\d+", normalized)
    count = int(num_match.group()) if num_match else 1
    for stem, seconds in ARABIC_UNIT_SECONDS:
        if stem in normalized:
            return now - timedelta(seconds=count * seconds)
    return None


def parse_gmt_title(value: str) -> datetime | None:
    """Parse Khamsat's tooltip stamp '24/07/2026 13:02:45 GMT' → aware UTC.

    Day-first, and explicitly GMT — no timezone guessing needed, which makes it
    strictly better than the relative text when both are present.
    """
    if not value:
        return None
    cleaned = value.replace("GMT", "").strip()
    try:
        naive = datetime.strptime(cleaned, "%d/%m/%Y %H:%M:%S")
    except (ValueError, AttributeError):
        return None
    return naive.replace(tzinfo=timezone.utc)
