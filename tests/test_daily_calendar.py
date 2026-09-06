#!/usr/bin/env python3
"""control_plane.daily_calendar — the Work board's calendar view (slices.md S9).

Pins the part S9's done-when actually asks for: a row with a time *range*
becomes a block (not bookable), a row with only a point-in-time marker does
not (no duration to claim), and a day with no file at all renders as an empty
bar rather than an error.

Run:
    pytest tests/test_daily_calendar.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_plane import daily_calendar  # noqa: E402

DAY_FILE = """---
title: "day"
---

# 2026-09-06

## ⏱️ ตารางเวลา — 09-06 (อา. · กริดวันหยุด v2)

| เวลา | บล็อก | domain | นาที |
| :-- | :-- | :--: | --: |
| `~04:57` | 🕌 **Fajr** | — | — |
| `05:00–05:15` | 🕌 อาหรับ (streak · optional) | learning | 15 |
| `08:30–10:00` | 🔴 **`P1` · สาย `C`** | `personal` | 90 |

## เพดานเวลา

| ไม่ใช่ตารางแรก | ต้องไม่ถูกอ่าน |
| --- | --- |
| `09:00–09:05` | ควรถูกข้าม |
"""


def _write_day(root: Path, iso_date: str, text: str) -> None:
    daily = root / "meta" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / f"{iso_date}.md").write_text(text, encoding="utf-8")


def test_range_rows_become_blocks_point_marker_does_not(tmp_path: Path):
    _write_day(tmp_path, "2026-09-06", DAY_FILE)

    day = daily_calendar.window_schedule(tmp_path, date(2026, 9, 6), before=0, after=0)[0]

    assert day["present"] is True
    starts = [b["start"] for b in day["blocks"]]
    assert "04:57" not in starts  # point marker, no range — not a block
    assert {"start": "05:00", "end": "05:15", "label": "🕌 อาหรับ (streak · optional)",
            "domain": "learning", "minutes": 15} in day["blocks"]
    assert {"start": "08:30", "end": "10:00", "label": "🔴 P1 · สาย C",
            "domain": "personal", "minutes": 90} in day["blocks"]


def test_stops_at_the_first_table_under_the_heading(tmp_path: Path):
    _write_day(tmp_path, "2026-09-06", DAY_FILE)

    day = daily_calendar.window_schedule(tmp_path, date(2026, 9, 6), before=0, after=0)[0]

    assert not any(b["start"] == "09:00" for b in day["blocks"])


def test_missing_day_file_is_an_empty_bar_not_an_error(tmp_path: Path):
    day = daily_calendar.window_schedule(tmp_path, date(2026, 9, 6), before=0, after=0)[0]

    assert day == {"date": "2026-09-06", "present": False, "blocks": []}


def test_window_spans_before_and_after_oldest_first(tmp_path: Path):
    days = daily_calendar.window_schedule(tmp_path, date(2026, 9, 6), before=2, after=1)

    assert [d["date"] for d in days] == [
        "2026-09-04",
        "2026-09-05",
        "2026-09-06",
        "2026-09-07",
    ]


def test_day_with_no_schedule_heading_is_present_with_no_blocks(tmp_path: Path):
    _write_day(tmp_path, "2026-09-06", "---\ntitle: x\n---\n\n# day\n\nno schedule here\n")

    day = daily_calendar.window_schedule(tmp_path, date(2026, 9, 6), before=0, after=0)[0]

    assert day == {"date": "2026-09-06", "present": True, "blocks": []}
