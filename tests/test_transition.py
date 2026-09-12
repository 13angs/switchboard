#!/usr/bin/env python3
"""control_plane.transition — the gate for moving a card between stations.

These tests pin the two things that would be dangerous to get wrong in a module
that will soon gate writes: it must fail **dark** when it cannot read the rules,
and it must refuse anything the rules do not declare — including a pair the file
declares that the code has no check for.

Run:
    pytest tests/test_transition.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_plane import transition  # noqa: E402

# Every pair `_CHECKS` implements, so `drift()` is clean — the shape the real
# row-status.md has. Column wording is deliberately NOT the real file's: the
# reader anchors on the heading, and this fixture proves headers stay free.
TABLE = """# กติกาของแถว

## สายพาน

### ตารางการส่งต่อ — ใครกดได้

| ต้นทาง | ปลายทาง | ใครกด | ต้องผ่านอะไร |
| --- | --- | --- | --- |
| `readydev` | `inprogress` | `developer` · `senior-developer` | `blocked-by` ต้องว่าง |
| `inprogress` | `review` | `developer` · `senior-developer` | — |
| `review` | `readyqa` | `senior-developer` | ฟอร์มส่งงานครบ |
| `readyqa` | `readydeploy` | `qa` | แถวลูกทุกแถวต้องปิด |
| `readyqa` | `inprogress` | `qa` | ต้องมีเหตุผล |
| `readydeploy` | `deployed` | `devops` | ต้องระบุ release / tag |
| `readydeploy` | `inprogress` | `devops` | ต้องมีเหตุผล |
| `deployed` | `done` | `product-owner` | แถวลูกทุกแถวต้องปิด |
| `deployed` | `inprogress` | `product-owner` | ต้องมีเหตุผล |
| `done` | `inprogress` | `product-owner` | ต้องมีเหตุผล |

## หัวข้อถัดไป

| ตารางอื่น | ต้องไม่ถูกอ่าน |
| --- | --- |
| `x` | `y` |
"""


def _root(tmp_path: Path, table: str | None = TABLE) -> Path:
    if table is not None:
        d = tmp_path / "team-os" / "ways-of-working"
        d.mkdir(parents=True)
        (d / "row-status.md").write_text(table, encoding="utf-8")
    return tmp_path


def _row(**over) -> dict:
    row = {"id": "T1", "stage": "readydev", "blocked_by": [], "criteria": None}
    row.update(over)
    return row


# ── reading the table ───────────────────────────────────────────────────────


def test_reads_every_declared_move(tmp_path):
    t = transition.transitions(_root(tmp_path))
    assert t["present"] is True
    assert len(t["moves"]) == 10
    assert t["moves"][("review", "readyqa")]["roles"] == ["senior-developer"]


def test_reads_both_roles_of_a_two_role_cell(tmp_path):
    """Split on the backticks, not on `·`: a reworded separator must not turn
    two roles into one long string that matches nobody."""
    t = transition.transitions(_root(tmp_path))
    assert t["moves"][("inprogress", "review")]["roles"] == [
        "developer",
        "senior-developer",
    ]


def test_stops_at_the_next_heading(tmp_path):
    """A table further down the file is other rules, not more transitions."""
    t = transition.transitions(_root(tmp_path))
    assert ("x", "y") not in t["moves"]


def test_a_missing_rules_file_fails_dark(tmp_path):
    t = transition.transitions(_root(tmp_path, table=None))
    assert t["present"] is False and t["moves"] == {}


def test_a_reworded_heading_fails_dark_rather_than_open(tmp_path):
    """The dangerous default is "cannot read the rules, so allow it"."""
    t = transition.transitions(_root(tmp_path, table="# กติกา\n\n## อย่างอื่น\n"))
    assert t["present"] is False


# ── the gate ────────────────────────────────────────────────────────────────


def test_the_happy_path_is_allowed(tmp_path):
    out = transition.evaluate(
        _root(tmp_path), row=_row(), to_stage="inprogress", actor_role="developer"
    )
    assert out["allowed"] is True and out["reason"] is None


def test_everything_is_refused_when_the_rules_cannot_be_read(tmp_path):
    out = transition.evaluate(
        _root(tmp_path, table=None),
        row=_row(),
        to_stage="inprogress",
        actor_role="developer",
    )
    assert out["allowed"] is False


def test_a_move_the_table_does_not_declare_is_refused(tmp_path):
    """Includes skipping a station: the table is a closed set."""
    out = transition.evaluate(
        _root(tmp_path), row=_row(stage="backlog"), to_stage="done", actor_role="cto"
    )
    assert out["allowed"] is False and "ไม่มีการส่งต่อ" in out["reason"]


def test_the_wrong_role_is_refused_and_told_whose_it_is(tmp_path):
    out = transition.evaluate(
        _root(tmp_path), row=_row(), to_stage="inprogress", actor_role="qa"
    )
    assert out["allowed"] is False
    assert "developer" in out["reason"] and "senior-developer" in out["reason"]


def test_the_rule_it_was_judged_against_comes_back_with_the_answer(tmp_path):
    """The operator sees the rule, not a paraphrase of it."""
    out = transition.evaluate(
        _root(tmp_path), row=_row(), to_stage="inprogress", actor_role="qa"
    )
    assert out["requires"] == "blocked-by ต้องว่าง"


# ── each condition ──────────────────────────────────────────────────────────


def test_an_open_blocker_stops_the_pickup_and_is_named(tmp_path):
    out = transition.evaluate(
        _root(tmp_path),
        row=_row(blocked_by=[{"id": "T9", "title": "อีกแถว"}]),
        to_stage="inprogress",
        actor_role="developer",
    )
    assert out["allowed"] is False and "T9" in out["reason"]


def test_the_handoff_form_must_be_complete(tmp_path):
    root = _root(tmp_path)
    part = transition.evaluate(
        root,
        row=_row(stage="review"),
        to_stage="readyqa",
        actor_role="senior-developer",
        form={"env": "local"},
    )
    assert part["allowed"] is False and "risk" in part["reason"]
    whole = transition.evaluate(
        root,
        row=_row(stage="review"),
        to_stage="readyqa",
        actor_role="senior-developer",
        form={"env": "local", "risk": "เปลี่ยน schema"},
    )
    assert whole["allowed"] is True


def test_criteria_must_all_be_closed(tmp_path):
    root = _root(tmp_path)
    short = transition.evaluate(
        root,
        row=_row(stage="readyqa", criteria={"done": 1, "total": 3}),
        to_stage="readydeploy",
        actor_role="qa",
    )
    assert short["allowed"] is False and "1/3" in short["reason"]
    full = transition.evaluate(
        root,
        row=_row(stage="readyqa", criteria={"done": 3, "total": 3}),
        to_stage="readydeploy",
        actor_role="qa",
    )
    assert full["allowed"] is True


def test_a_row_with_no_criteria_at_all_cannot_pass_qa(tmp_path):
    """`None` is not "zero of zero passed" — nobody wrote a criterion yet."""
    out = transition.evaluate(
        _root(tmp_path),
        row=_row(stage="readyqa", criteria=None),
        to_stage="readydeploy",
        actor_role="qa",
    )
    assert out["allowed"] is False


def test_a_reject_needs_a_reason(tmp_path):
    root = _root(tmp_path)
    bare = transition.evaluate(
        root, row=_row(stage="readyqa"), to_stage="inprogress", actor_role="qa"
    )
    assert bare["allowed"] is False
    given = transition.evaluate(
        root,
        row=_row(stage="readyqa"),
        to_stage="inprogress",
        actor_role="qa",
        form={"reason": "ผิด AC ข้อ 2"},
    )
    assert given["allowed"] is True


def test_a_deploy_needs_a_release_tag(tmp_path):
    root = _root(tmp_path)
    bare = transition.evaluate(
        root, row=_row(stage="readydeploy"), to_stage="deployed", actor_role="devops"
    )
    assert bare["allowed"] is False
    given = transition.evaluate(
        root,
        row=_row(stage="readydeploy"),
        to_stage="deployed",
        actor_role="devops",
        form={"release": "v1.8.2"},
    )
    assert given["allowed"] is True


# ── the law-drift guard ─────────────────────────────────────────────────────


def test_a_table_matching_the_code_reports_no_drift(tmp_path):
    assert transition.drift(_root(tmp_path)) == []


def test_a_move_the_code_cannot_check_is_reported_and_refused(tmp_path):
    """The file wins: an undeclarable pair must not pass with no condition."""
    extra = TABLE.replace(
        "| `done` | `inprogress` |",
        "| `done` | `deployed` | `cto` | — |\n| `done` | `inprogress` |",
    )
    root = _root(tmp_path, table=extra)
    assert any("ตารางประกาศไว้ แต่โค้ดไม่มีตัวตรวจ" in d for d in transition.drift(root))
    out = transition.evaluate(
        root, row=_row(stage="done"), to_stage="deployed", actor_role="cto"
    )
    assert out["allowed"] is False


def test_a_check_the_table_no_longer_declares_is_reported(tmp_path):
    fewer = TABLE.replace("| `done` | `inprogress` | `product-owner` | ต้องมีเหตุผล |\n", "")
    assert any(
        "ตารางไม่ประกาศแล้ว" in d for d in transition.drift(_root(tmp_path, table=fewer))
    )
