#!/usr/bin/env python3
"""control_plane.workspace — the board's second data source (ADR-0029).

These tests pin the parts that would fail silently in production: a slices.md
whose wording drifts, a row the owner must decide being counted as actionable
work, and the HEAD-keyed cache returning a stale tree after a commit.

Run:
    pytest tests/test_workspace.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_plane import workspace  # noqa: E402

SLICES = """---
title: "demo — งานแบ่งเป็นชิ้น"
---

# Slices

| # | ชิ้น | วัน | สถานะ | ใช้งานได้จริงว่า |
| :-: | --- | --- | :-: | --- |
| **M0** | ทำ[บัตรพูด](x.md) | ศ. 09-04 | ✅ | เสร็จแล้ว |
| — | _ไม่มีงาน_ | ส. 09-05 | ⬛ | วันหยุด |
| **M1** | เคาะเกณฑ์ + เขียน **ADR** | จ. 09-07 | 🔜 | มีเกณฑ์แล้ว |
| **M2** | ติดหมวด `407` แถว | อ. 09-08 | ⬜ | ค้นได้ |
| **M3** | รันอยู่ | พ. 09-09 | 🔄 | กำลังทำ |
| **M4** | โต๊ะเคาะ | พฤ. 09-17 | ⬜ | 🖐️ **คนเคาะ ไม่ใช่ agent** |

## เพดานเวลา

| ไม่ใช่ตารางแรก | ต้องไม่ถูกอ่าน |
| --- | --- |
| **M9** | ⬜ |
"""

GAPS = """# GAPS

| # | ช่อง | ถาม | อุด | รอบ | สถานะ |
| :-- | --- | --- | --- | :-: | --- |
| `G-01` | a | b | c | 3 | เปิด |
| `G-02` | d | e | f | 3 | 🟠 **ลดแล้ว** |
| `G-03` | g | h | i | 3 | ✅ **ปิดแล้ว** |

## บทเรียน

| `G-02` ประเมินไว้ | ❌ ของจริง |
| --- | --- |
"""


def _repo(
    tmp_path: Path, *, slices: str | None = SLICES, gaps: str | None = GAPS
) -> Path:
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    if slices is not None:
        (proj / "slices.md").write_text(slices, encoding="utf-8")
    (proj / "scope.md").write_text("# scope", encoding="utf-8")
    if gaps is not None:
        teamos = tmp_path / "team-os"
        teamos.mkdir(parents=True)
        (teamos / "GAPS.md").write_text(gaps, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_cache():
    workspace.invalidate_cache()
    yield
    workspace.invalidate_cache()


def test_parses_slices_into_board_columns(tmp_path):
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    assert out["totals"]["projects_with_slices"] == 1
    cols = out["projects"][0]["columns"]
    assert cols["done"] == 1
    assert cols["next"] == 1
    assert cols["running"] == 1
    assert cols["off"] == 1
    # M2 only — M4 carries the owner mark and must not land in todo
    assert cols["todo"] == 1
    assert cols["owner"] == 1


def test_owner_mark_beats_status_glyph(tmp_path):
    """A row the owner must decide is not actionable work, whatever its status."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    m4 = [s for s in out["projects"][0]["slices"] if s["id"] == "M4"][0]
    assert m4["column"] == "owner"


def test_reads_first_table_only(tmp_path):
    """A second table further down the file is other content, not more slices."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    ids = [s["id"] for s in out["projects"][0]["slices"]]
    assert "M9" not in ids
    assert len(ids) == 6


def test_strips_markdown_from_cells(tmp_path):
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    by_id = {s["id"]: s for s in out["projects"][0]["slices"]}
    assert by_id["M0"]["title"] == "ทำบัตรพูด"  # link label kept, target dropped
    assert by_id["M1"]["title"] == "เคาะเกณฑ์ + เขียน ADR"  # bold markers gone
    assert by_id["M2"]["title"] == "ติดหมวด 407 แถว"  # backticks gone


def test_project_without_slices_is_skipped(tmp_path):
    repo = _repo(tmp_path, slices=None)
    out = workspace.workspace_overview(str(repo), use_cache=False)
    assert out["projects"] == []
    assert out["totals"]["projects_with_slices"] == 0


def test_reports_what_each_project_is_missing(tmp_path):
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    has = out["projects"][0]["has"]
    assert has["scope"] is True
    assert has["risks"] is False
    assert has["hld"] is False


def test_gap_register_counts_each_row_once(tmp_path):
    """The commentary table at the bottom repeats ids — it must not double-count."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    assert out["gaps"] == {
        "present": True,
        "total": 3,
        "closed": 1,
        "reduced": 1,
        "open": 1,
    }


def test_missing_gap_register_is_reported_not_fatal(tmp_path):
    repo = _repo(tmp_path, gaps=None)
    out = workspace.workspace_overview(str(repo), use_cache=False)
    assert out["gaps"] == {"present": False}


def test_non_git_directory_still_works(tmp_path):
    """Running the board against a plain folder is legitimate — no crash, no cache."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=True)
    assert out["head"] == ""
    assert out["projects"]


def test_bad_repo_root_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        workspace.workspace_overview(str(tmp_path / "nope"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def test_cache_serves_same_head_and_refreshes_after_commit(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")

    first = workspace.workspace_overview(str(repo))
    assert first["head"]
    assert workspace.workspace_overview(str(repo)) is first  # same object = cache hit

    # a new commit must move HEAD and therefore the payload
    (repo / "projects" / "demo" / "risks.md").write_text("# risks", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two")

    second = workspace.workspace_overview(str(repo))
    assert second is not first
    assert second["head"] != first["head"]
    assert second["projects"][0]["has"]["risks"] is True


# ── `blocked-by` column (S38, row-status.md § ลำดับก่อนหลัง, W22) ────────────

BLOCKED_SLICES = """---
title: "demo — งานแบ่งเป็นชิ้น"
---

# Slices

| # | ชิ้น | วัน | สถานะ | ใช้งานได้จริงว่า | blocked-by |
| :-: | --- | --- | :-: | --- | :-: |
| **B0** | ตัวบล็อกยังเปิดอยู่ | จ. | ⬜ | ยังไม่เริ่ม | |
| **B1** | รอ B0 | อ. | ⬜ | เริ่มไม่ได้จน B0 ปิด | B0 |
| **B2** | บล็อกปิดแล้ว | พ. | ✅ | เสร็จแล้ว | |
| **B3** | รอ B2 ที่ปิดแล้ว | พฤ. | ⬜ | ต้องล้างค่านี้ออก แต่ยังไม่ได้ล้าง | B2 |
| **B4** | รอสองแถว | ศ. | ⬜ | รอทั้งคู่ | B0, B2 |
| **B5** | ชี้ไปแถวที่ไม่มีจริง | ส. | ⬜ | id หลอน | B9 |
| **B6** | ไม่มีตัวบล็อก | อา. | ⬜ | เขียนว่าง | — |
"""


def _repo_blocked(tmp_path: Path) -> Path:
    return _repo(tmp_path, slices=BLOCKED_SLICES, gaps=None)


def test_row_waiting_on_an_open_blocker_names_it(tmp_path):
    out = workspace.workspace_overview(str(_repo_blocked(tmp_path)), use_cache=False)
    by_id = {s["id"]: s for s in out["projects"][0]["slices"]}
    assert by_id["B1"]["blocked_by"] == [{"id": "B0", "title": "ตัวบล็อกยังเปิดอยู่"}]
    # blocked-by is not a 9th status — the row keeps its own glyph's column
    assert by_id["B1"]["column"] == "todo"


def test_a_closed_blocker_no_longer_counts_even_if_the_cell_was_not_cleared(tmp_path):
    """row-status.md: the column reads *still open*, not *ever named* — a
    stale id left in the cell after its blocker closed must not hold the row."""
    out = workspace.workspace_overview(str(_repo_blocked(tmp_path)), use_cache=False)
    by_id = {s["id"]: s for s in out["projects"][0]["slices"]}
    assert by_id["B3"]["blocked_by"] == []


def test_multiple_blockers_report_only_the_still_open_ones(tmp_path):
    out = workspace.workspace_overview(str(_repo_blocked(tmp_path)), use_cache=False)
    by_id = {s["id"]: s for s in out["projects"][0]["slices"]}
    # B4 names both B0 (open) and B2 (closed) — only B0 keeps it blocked.
    assert {b["id"] for b in by_id["B4"]["blocked_by"]} == {"B0"}


def test_a_dangling_blocker_id_is_dropped_not_fabricated(tmp_path):
    """An id the cell names but the file does not contain is a route-lint
    finding (Check 7), not something this reader invents a blocker for."""
    out = workspace.workspace_overview(str(_repo_blocked(tmp_path)), use_cache=False)
    by_id = {s["id"]: s for s in out["projects"][0]["slices"]}
    assert by_id["B5"]["blocked_by"] == []


def test_empty_spellings_all_mean_no_blocker(tmp_path):
    out = workspace.workspace_overview(str(_repo_blocked(tmp_path)), use_cache=False)
    by_id = {s["id"]: s for s in out["projects"][0]["slices"]}
    assert by_id["B0"]["blocked_by"] == []  # blank cell
    assert by_id["B6"]["blocked_by"] == []  # "—" cell


def test_file_without_a_blocked_by_column_reports_no_blockers(tmp_path):
    """Backward compatibility — the column is opt-in per file (ADR-0035's rule,
    applied here): a file that never adopts it must keep working unchanged."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    slices = out["projects"][0]["slices"]
    assert slices and all(s["blocked_by"] == [] for s in slices)


# ── `role` handoff (S40) ─────────────────────────────────────────────────────
#
# A row's `role` cell changing value is a handoff between stations, and it
# happens on the same file the board already reads — this checks it can spot
# one from git history alone, no second store.

from test_role_activity import ROLES_MD, SOP_MD  # noqa: E402


def _slices_with_role(role_cell: str, note: str = "ทดสอบ") -> str:
    return f"""---
title: "demo — งานแบ่งเป็นชิ้น"
client: internal
team: dev
---

# Slices

| # | ชิ้น | วัน | สถานะ | ใช้งานได้จริงว่า | role |
| :-: | --- | --- | :-: | --- | :-: |
| **H1** | ส่งไม้ | จ. | ⬜ | {note} | {role_cell} |
"""


def _repo_for_handoff(tmp_path: Path) -> Path:
    (tmp_path / "team-os" / "people").mkdir(parents=True)
    (tmp_path / "team-os" / "people" / "roles.md").write_text(ROLES_MD, encoding="utf-8")
    (tmp_path / "docs" / "sops").mkdir(parents=True)
    (tmp_path / "docs" / "sops" / "sop-agent-orchestration.md").write_text(
        SOP_MD, encoding="utf-8"
    )
    return tmp_path


def _commit_role(repo: Path, role_cell: str, message: str, *, note: str = "ทดสอบ") -> None:
    proj = repo / "projects" / "demo"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "slices.md").write_text(_slices_with_role(role_cell, note), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def test_a_changed_role_cell_is_reported_as_a_handoff(tmp_path):
    repo = _repo_for_handoff(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit_role(repo, "developer", "one")
    _commit_role(repo, "tech-lead", "two")

    out = workspace.workspace_overview(str(repo), use_cache=False)
    h1 = [s for s in out["projects"][0]["slices"] if s["id"] == "H1"][0]
    assert h1["handoff"] == {"from": "Developer", "to": "Tech Lead"}
    assert out["handoffs"] == [
        {
            "project": "demo",
            "id": "H1",
            "title": "ส่งไม้",
            "from": "Developer",
            "to": "Tech Lead",
        }
    ]


def test_an_unchanged_role_cell_reports_no_handoff(tmp_path):
    repo = _repo_for_handoff(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit_role(repo, "developer", "one")
    _commit_role(
        repo, "developer", "two — unrelated edit, same role", note="แก้ note เฉย ๆ"
    )

    out = workspace.workspace_overview(str(repo), use_cache=False)
    h1 = [s for s in out["projects"][0]["slices"] if s["id"] == "H1"][0]
    assert h1["handoff"] is None
    assert out["handoffs"] == []


def test_a_brand_new_row_is_not_a_handoff(tmp_path):
    """The row did not exist in the file's own previous commit at all — being
    assigned an owner for the first time is not "changing hands"."""
    repo = _repo_for_handoff(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit_role(repo, "developer", "one")

    out = workspace.workspace_overview(str(repo), use_cache=False)
    h1 = [s for s in out["projects"][0]["slices"] if s["id"] == "H1"][0]
    assert h1["handoff"] is None


def test_non_git_directory_reports_no_handoffs(tmp_path):
    repo = _repo_for_handoff(tmp_path)
    (repo / "projects" / "demo").mkdir(parents=True)
    (repo / "projects" / "demo" / "slices.md").write_text(
        _slices_with_role("tech-lead"), encoding="utf-8"
    )
    out = workspace.workspace_overview(str(repo), use_cache=False)
    h1 = [s for s in out["projects"][0]["slices"] if s["id"] == "H1"][0]
    assert h1["handoff"] is None


# ── the belt: `stage` and `part-of` (row-status.md § สายพาน) ────────────────

BELT_SLICES = """---
title: "belt — งานแบ่งเป็นชิ้น"
---

# Slices

| # | ชิ้น | วัน | สถานะ | ใช้งานได้จริงว่า | stage | part-of |
| :-: | --- | --- | :-: | --- | :-: | :-: |
| **T0** | การ์ดแม่ที่มีเกณฑ์สองข้อ | จ. | ⬜ | ทำได้จริงว่า… | review | — |
| **T0a** | เกณฑ์ข้อ 1 | จ. | ✅ | รันแล้ว | — | T0 |
| **T0b** | เกณฑ์ข้อ 2 | จ. | ⬜ | ยังไม่รัน | — | T0 |
| **T1** | ค้างไม่ปิด | อ. | ⬜ | สายพานจบแล้วแต่ไม่มีใครปิดแถว | done | — |
| **T2** | ปิดโดยข้ามด่าน | พ. | ✅ | กา ✅ ทั้งที่ยังอยู่กลางสายพาน | inprogress | — |
| **T3** | สองแกนตรงกัน | พฤ. | ✅ | ปิดถูกที่ | done | — |
| **T4** | สถานีที่ไม่มีจริง | ศ. | ⬜ | route-lint Check 9 จะฟ้องใบนี้ | shipping | — |
| **T5** | ชี้ไปแถวที่ไม่มี | ส. | ⬜ | route-lint Check 10 จะฟ้องใบนี้ | — | T9 |
| **T6** | ชี้ตัวเอง | อา. | ⬜ | วงความยาว 1 | — | T6 |
"""


def _repo_belt(tmp_path: Path) -> Path:
    return _repo(tmp_path, slices=BELT_SLICES, gaps=None)


def _belt(tmp_path: Path) -> dict:
    out = workspace.workspace_overview(str(_repo_belt(tmp_path)), use_cache=False)
    return {s["id"]: s for s in out["projects"][0]["slices"]}


def test_stage_is_read_as_a_second_axis_not_a_ninth_glyph(tmp_path):
    """The row keeps the column its own glyph puts it in — `stage` rides
    alongside it (row-status.md § สายพาน)."""
    by_id = _belt(tmp_path)
    assert by_id["T0"]["stage"] == "review"
    assert by_id["T0"]["column"] == "todo"  # ⬜ — unchanged by the stage cell


def test_a_station_nobody_declared_is_dropped_not_guessed(tmp_path):
    """An undeclared value is a route-lint Check 9 finding; the reader must not
    move a card to a station that does not exist because of one typo."""
    assert _belt(tmp_path)["T4"]["stage"] == ""


def test_parent_row_counts_its_criteria_from_the_child_rows(tmp_path):
    """The checklist is assembled at display time — the file still holds one
    criterion per row, so `S22` cannot happen again."""
    by_id = _belt(tmp_path)
    assert by_id["T0"]["criteria"] == {"done": 1, "total": 2}
    # the children are cards of their own and carry the pointer, not a count
    assert by_id["T0a"]["part_of"] == "T0"
    assert by_id["T0a"]["criteria"] is None


def test_a_row_nobody_points_at_has_no_count_rather_than_zero_of_zero(tmp_path):
    """`None` and `0/0` are different answers: one means "not a parent", the
    other would claim a parent with no criteria written yet."""
    assert _belt(tmp_path)["T3"]["criteria"] is None


def test_a_dangling_or_self_referential_parent_is_dropped(tmp_path):
    """Both are route-lint Check 10 findings. The reader keeps the cell as
    written — it is the file's text — but refuses to count against it."""
    by_id = _belt(tmp_path)
    assert "T9" not in by_id  # the row T5 points at does not exist
    assert by_id["T5"]["part_of"] == "T9"  # the cell survives verbatim
    assert by_id["T6"]["part_of"] == "T6"
    # neither produces a parent tally anywhere in the file
    assert all(s["criteria"] is None for s in (by_id["T5"], by_id["T6"]))


def test_the_two_axes_disagreeing_is_reported_not_corrected(tmp_path):
    by_id = _belt(tmp_path)
    assert by_id["T1"]["axis_conflict"] == "stuck-open"
    assert by_id["T2"]["axis_conflict"] == "skipped-gate"
    assert by_id["T3"]["axis_conflict"] is None
    # and the rows keep exactly the status they were written with
    assert by_id["T1"]["column"] == "todo" and by_id["T2"]["column"] == "done"


def test_file_without_the_belt_columns_is_unchanged(tmp_path):
    """Opt-in per file, same contract as `role` and `blocked-by`."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    slices = out["projects"][0]["slices"]
    assert slices and all(
        s["stage"] == ""
        and s["part_of"] == ""
        and s["criteria"] is None
        and s["axis_conflict"] is None
        for s in slices
    )
