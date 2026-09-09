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
            "domain": "learning", "minutes": 15,
            "ritual": None, "ritual_conflict": None} in day["blocks"]
    assert {"start": "08:30", "end": "10:00", "label": "🔴 P1 · สาย C",
            "domain": "personal", "minutes": 90,
            "ritual": None, "ritual_conflict": None} in day["blocks"]


def test_stops_at_the_first_table_under_the_heading(tmp_path: Path):
    _write_day(tmp_path, "2026-09-06", DAY_FILE)

    day = daily_calendar.window_schedule(tmp_path, date(2026, 9, 6), before=0, after=0)[0]

    assert not any(b["start"] == "09:00" for b in day["blocks"])


def test_missing_day_file_is_an_empty_bar_not_an_error(tmp_path: Path):
    day = daily_calendar.window_schedule(tmp_path, date(2026, 9, 6), before=0, after=0)[0]

    assert day == {"date": "2026-09-06", "present": False, "blocks": [], "unmapped": []}


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

    assert day == {"date": "2026-09-06", "present": True, "blocks": [], "unmapped": []}


# ── ADR-0036 — a bar that carries a ritual is a bar that can be pressed ──

RITUALS = [
    {"key": "morning reconcile-delta", "name": "morning reconcile-delta",
     "role": "Product Owner", "client": "internal", "office": "business",
     "assignment": "internal/business/product-owner/morning-reconcile-delta",
     "reads": "docs/runbooks/runbook-daily-plan-generation.md ขั้น 9",
     "dispatchable": True, "missing": []},
    {"key": "comm-window", "name": "comm-window ×3",
     "role": "Product Owner", "client": "internal", "office": "business",
     "assignment": "internal/business/product-owner/comm-window",
     "reads": "meta/daily/plan.md § จังหวะวันทำงาน",
     "dispatchable": True, "missing": []},
    {"key": "EOD checkpoint", "name": "EOD checkpoint",
     "role": "Product Owner", "client": "internal", "office": "business",
     "assignment": "internal/business/product-owner/eod-checkpoint",
     "reads": "meta/daily/plan.md § จังหวะวันทำงาน",
     "dispatchable": True, "missing": []},
    {"key": "news brief", "name": "news brief",
     "role": "Product Owner", "client": "internal", "office": "business",
     "assignment": "internal/business/product-owner/news-brief",
     "reads": "docs/runbooks/runbook-daily-news-update.md",
     "dispatchable": True, "missing": []},
]

RITUAL_DAY = """---
title: "day"
---

# 2026-09-08

## ⏱️ ตารางเวลา — 09-08

| เวลา | บล็อก | domain | นาที |
| :-- | :-- | :--: | --: |
| `08:30–08:40` | 🔄 **morning reconcile-delta** | work | 10 |
| `11:40–12:00` | 📨 comm-window #1 | work | 20 |
| `13:00–13:20` | 📨 comm-window #2 | work | 20 |
| `16:45–17:00` | ✅ **EOD checkpoint** (สองครึ่ง + stamp cutoff) | work | 15 |
| `19:45–20:15` | 🇬🇧 อังกฤษ | learning | 30 |
"""


def _blocks(root: Path, iso: str = "2026-09-08"):
    day = daily_calendar.window_schedule(
        root, date.fromisoformat(iso), before=0, after=0, rituals=RITUALS
    )[0]
    return day, {b["start"]: b for b in day["blocks"]}


def test_a_bar_carries_the_ritual_its_label_names(tmp_path: Path):
    """§SD2 — matched by key-as-substring of the label, not by the clock: the
    times in rituals.md are defaults (measured 09-06: registered 08:30, ran
    08:00), and the label is free-text that changes every day."""
    _write_day(tmp_path, "2026-09-08", RITUAL_DAY)

    _, by_start = _blocks(tmp_path)

    assert by_start["16:45"]["ritual"]["key"] == "EOD checkpoint"
    assert (
        by_start["16:45"]["ritual"]["assignment"]
        == "internal/business/product-owner/eod-checkpoint"
    )
    # bold markers and the trailing parenthetical do not get in the way
    assert by_start["08:30"]["ritual"]["key"] == "morning reconcile-delta"
    # a bar that names no ritual stays a reserved slot and nothing more
    assert by_start["19:45"]["ritual"] is None


def test_one_key_may_own_several_bars_in_a_day(tmp_path: Path):
    """comm-window is one register row and three bars — each is pressable, and
    all three carry the same id, because the id names the ritual not the run."""
    _write_day(tmp_path, "2026-09-08", RITUAL_DAY)

    _, by_start = _blocks(tmp_path)

    ids = [by_start[t]["ritual"]["assignment"] for t in ("11:40", "13:00")]
    assert ids == ["internal/business/product-owner/comm-window"] * 2


def test_two_keys_on_one_label_leaves_the_bar_without_a_ritual(tmp_path: Path):
    """Choosing between them would be a guess, and guessing from text is what
    risks.md S-01 is. Measured on real day files: this happens (09-02, 09-03)."""
    _write_day(
        tmp_path,
        "2026-09-08",
        RITUAL_DAY.replace(
            "| `16:45–17:00` | ✅ **EOD checkpoint** (สองครึ่ง + stamp cutoff) |",
            "| `16:45–17:00` | ✅ EOD checkpoint + news brief |",
        ),
    )

    _, by_start = _blocks(tmp_path)

    assert by_start["16:45"]["ritual"] is None
    assert sorted(by_start["16:45"]["ritual_conflict"]) == [
        "EOD checkpoint",
        "news brief",
    ]


def test_a_declared_key_with_no_bar_today_is_reported_not_silent(tmp_path: Path):
    """§SD6 — the same failure shape as risks.md S-08: a gate nobody fills is
    as quiet as no gate. `news brief` is in the register and not in this plan."""
    _write_day(tmp_path, "2026-09-08", RITUAL_DAY)

    day, _ = _blocks(tmp_path)

    assert [r["key"] for r in day["unmapped"]] == ["news brief"]


def test_a_key_claimed_only_by_an_ambiguous_bar_is_not_called_unmapped(tmp_path: Path):
    """It is in the plan — it just cannot be pressed. Reporting it as missing
    from the day would send the reader to fix the wrong file."""
    _write_day(
        tmp_path,
        "2026-09-08",
        RITUAL_DAY.replace(
            "| `16:45–17:00` | ✅ **EOD checkpoint** (สองครึ่ง + stamp cutoff) |",
            "| `16:45–17:00` | ✅ EOD checkpoint + news brief |",
        ),
    )

    day, _ = _blocks(tmp_path)

    assert day["unmapped"] == []


def test_without_a_register_the_calendar_behaves_exactly_as_before(tmp_path: Path):
    """S9's view must not depend on ADR-0036 landing: no register, no buttons,
    no §SD6 line, same bars."""
    _write_day(tmp_path, "2026-09-08", RITUAL_DAY)

    day = daily_calendar.window_schedule(
        tmp_path, date(2026, 9, 8), before=0, after=0
    )[0]

    assert len(day["blocks"]) == 5
    assert all(b["ritual"] is None for b in day["blocks"])
    assert day["unmapped"] == []
