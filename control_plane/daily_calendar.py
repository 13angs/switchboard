"""Daily-schedule bars for the Work board's calendar view (slices.md S9).

Single public entry: window_schedule() — reads `meta/daily/<date>.md`
§ ⏱️ ตารางเวลา (the workspace's own day-plan section) and turns each row that
carries a time *range* into a block. The point (per S9's done-when) is seeing
which slot is **not bookable**, not merely empty, before a session gets
dispatched on top of it — so a row with no range (a point-in-time prayer-time
marker, say) carries no duration and is not returned as a block at all.

Design constraints, mirroring control_plane/workspace.py (ADR-0029 §SD1):
  - Standard library only — tests/test_stdlib_purity.py enforces this.
  - Reads committed files only; not cached by HEAD like workspace_overview()
    because "today" moves with the wall clock independently of any commit —
    baking a date into a HEAD-keyed cache would serve yesterday's window
    after midnight with no new commit to invalidate it. The read itself is a
    handful of small markdown files, cheap enough to redo per request.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# Owner is Asia/Bangkok (docs/workspace-operating-rules.md § Time / clock) —
# "today" for this view must follow the owner's day, not the container's UTC.
_OWNER_TZ = ZoneInfo("Asia/Bangkok")

_HEADING_RE = re.compile(r"^#{1,6}\s*.*⏱️.*ตารางเวลา")
_TIME_RANGE_RE = re.compile(r"~?(\d{1,2}:\d{2})\s*[–—-]\s*~?(\d{1,2}:\d{2})")


@dataclass
class ScheduleBlock:
    start: str  # "HH:MM"
    end: str  # "HH:MM"
    label: str
    domain: str
    minutes: Optional[int]
    # ADR-0036 §SD2 — the ritual this bar carries, or None when the label names
    # no declared key. `ritual_conflict` names the keys when it matched more
    # than one: two keys on one bar means the board has no way to choose, so it
    # offers nothing rather than picking (fail dark, same direction as
    # ADR-0030 §SD1).
    ritual: Optional[dict] = None
    ritual_conflict: Optional[list] = None


def today_bangkok() -> Date:
    return datetime.now(_OWNER_TZ).date()


def window_schedule(
    root: Path,
    center: Date,
    before: int = 2,
    after: int = 2,
    rituals: Optional[list[dict]] = None,
) -> list[dict]:
    """One entry per day from `center - before` to `center + after`, oldest first.

    Each entry: {date, present, blocks, unmapped}. `present=False` means no day
    file exists yet (future day, or one never written) — the caller draws it as
    an empty bar, not an error.

    `rituals` is the registry from workspace.ritual_registry(); passing it turns
    bars into dispatchable ones (ADR-0036) and fills `unmapped`. Passed in
    rather than read here so this module keeps reading exactly one file family
    — the day plan — and the register's own reader stays the single place role,
    client and office are resolved.
    """
    return [
        _day_schedule(root, center + timedelta(days=offset), rituals or [])
        for offset in range(-before, after + 1)
    ]


def _day_schedule(root: Path, day: Date, rituals: list[dict]) -> dict:
    path = root / "meta" / "daily" / f"{day.isoformat()}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"date": day.isoformat(), "present": False, "blocks": [], "unmapped": []}

    blocks = [asdict(b) for b in _parse_schedule_table(text)]
    for block in blocks:
        _attach_ritual(block, rituals)

    # ADR-0036 §SD6 — a key that matched nothing today is the failure shape of
    # risks.md S-08: a gate nobody fills is as quiet as no gate at all. So the
    # silence gets a line under the agenda instead of just no button. It reads
    # the *plan* only: "not in today's plan", never "not run yet" (that half is
    # slices.md S15).
    matched = {b["ritual"]["key"] for b in blocks if b.get("ritual")}
    matched |= {k for b in blocks for k in (b.get("ritual_conflict") or [])}
    return {
        "date": day.isoformat(),
        "present": True,
        "blocks": blocks,
        "unmapped": [r for r in rituals if r["key"] not in matched],
    }


def _attach_ritual(block: dict, rituals: list[dict]) -> None:
    """Match one bar to at most one ritual (ADR-0036 §SD2).

    By key-as-substring of the label, case-insensitively — not by time. The
    times in rituals.md are defaults, not identity (measured 09-06: morning
    reconcile-delta is registered 08:30 and ran 08:00), and three of the eight
    rituals have no clock at all.
    """
    label = block["label"].lower()
    hits = [r for r in rituals if r["key"].lower() in label]
    if len(hits) == 1:
        block["ritual"] = hits[0]
    elif len(hits) > 1:
        block["ritual_conflict"] = [r["key"] for r in hits]


def _parse_schedule_table(text: str) -> list[ScheduleBlock]:
    in_section = False
    in_table = False
    blocks: list[ScheduleBlock] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if in_section:
                break  # section ended; a later heading is a different topic
            in_section = bool(_HEADING_RE.match(stripped))
            in_table = False
            continue
        if not in_section:
            continue
        if not stripped.startswith("|"):
            if in_table and blocks:
                break  # first table under the heading only
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if set("".join(cells)) <= set("-: "):
            in_table = True  # separator row
            continue
        if not in_table:
            continue  # header row

        time_cell, label_cell = cells[0], cells[1]
        domain_cell = cells[2] if len(cells) > 2 else ""
        minutes_cell = cells[3] if len(cells) > 3 else ""

        m = _TIME_RANGE_RE.search(time_cell)
        if not m:
            continue  # a point-in-time marker has no duration — not a block

        try:
            minutes = int(re.sub(r"[^0-9]", "", minutes_cell))
        except ValueError:
            minutes = None

        blocks.append(
            ScheduleBlock(
                start=m.group(1),
                end=m.group(2),
                label=_strip_md(label_cell),
                domain=_strip_md(domain_cell),
                minutes=minutes,
            )
        )
    return blocks


def _strip_md(cell: str) -> str:
    """Plain text from a markdown table cell — links keep their label.

    Duplicated from control_plane/workspace.py rather than imported: that
    function is module-private, and this reads a different file family for a
    different reason (dev-time judgment call, not a shared contract worth
    coupling two modules over).
    """
    out = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    out = out.replace("**", "").replace("`", "")
    return out.strip()
