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
