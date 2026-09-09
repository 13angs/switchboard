#!/usr/bin/env python3
"""The ritual register — the board's second dispatchable table (ADR-0036).

The cases here are the ones that fail *quietly*: a register row that resolves a
role it should not have, a client the board fills in for itself (which misfiles
the work permanently in `git log`), and a definition pointer joined on prose
that drifted. §SD3 is explicit that no field has a default, so most of this file
is about what happens when a cell is missing.

Run:
    pytest tests/test_rituals.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_plane import workspace  # noqa: E402


SOP = """# Agent orchestration

light = `claude-haiku-4-5`, standard = `claude-sonnet-5`, heavy = `claude-opus-5`
"""

ROLES = """# Roles

## โมเดลต่อ role — tier ที่แต่ละบทบาททำงานด้วย

| Role | default | effort | ขึ้น **heavy** เมื่อ |
| --- | :-: | :-: | --- |
| **CTO** | **heavy** | `xhigh` | — |
| **Developer** | standard | `medium` | — |
| **Product Owner** | standard | `medium` | — |

## แกนความเป็นเจ้าของ — role → office · discipline · ที่บันทึก

| role | office | เปิด discipline leaf ใน `team/` | บันทึกผลลงที่ |
| --- | :-: | --- | --- |
| `cto` | `build` | `arch` · `security` | `meta/adr-*.md` |
| `developer` | `build` | `dev` | commit body |
| `product-owner` | `business` | `forge` · `ops` | day file |
"""

RITUALS = """---
title: "Rituals"
---

# Rituals

ตารางแรกของไฟล์ **ไม่ใช่** ทะเบียน — ต้องไม่ถูกอ่านเป็นจังหวะ

| อยากรู้ | เปิด |
| --- | --- |
| จังหวะมีอะไรบ้าง | ข้างล่าง |

## เจ้าของของแต่ละจังหวะ

| จังหวะ | `key` | `role` | `client` | `Assignment:` |
| --- | --- | --- | --- | --- |
| morning reconcile-delta | `morning reconcile-delta` | `product-owner` | `internal` | `internal/business/product-owner/morning-reconcile-delta` |
| comm-window ×3 | `comm-window` | `product-owner` | `internal` | `internal/business/product-owner/comm-window` |
| EOD-prep + forge pre-check | `EOD-prep` | `product-owner` | `internal` | `internal/business/product-owner/eod-prep` |
| สรุปรายสัปดาห์ | `weekly` | `product-owner` | `winona` | `winona/business/product-owner/weekly` |
| หยิบงานต่อจากวันก่อน | — | `product-owner` | `internal` | `internal/business/product-owner/pickup` |

## จังหวะทำอะไร เมื่อไหร่

| จังหวะ | เมื่อไหร่ | ออกมาเป็นอะไร | นิยามอยู่ที่ |
| --- | --- | --- | --- |
| morning reconcile-delta | `08:30–08:40` | เก็บงานหลัง cutoff | [`../../docs/runbooks/runbook-daily-plan-generation.md`](../../docs/runbooks/runbook-daily-plan-generation.md) ขั้น 9 |
| comm-window ×3 | `11:40` · `13:00` | รอบสื่อสาร | [`../../meta/daily/plan.md`](../../meta/daily/plan.md) § จังหวะวันทำงาน |
| EOD-prep + **forge pre-check F1–F5** | `17:00–17:30` | แผนพรุ่งนี้ | [`runbook-daily-plan-generation`](../../docs/runbooks/runbook-daily-plan-generation.md) ขั้น 0–7 |
| สรุปรายสัปดาห์ | รายสัปดาห์ | digest | [`../../docs/sops/sop-weekly-digest.md`](../../docs/sops/sop-weekly-digest.md) |
| หยิบงานต่อจากวันก่อน | ต้นเซสชัน | บริบทกลับมาครบ | [`../../docs/sops/sop-pickup-from-today.md`](../../docs/sops/sop-pickup-from-today.md) |
"""


def _repo(tmp_path: Path, *, rituals: str | None = RITUALS, roles: str = ROLES) -> str:
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    (proj / "slices.md").write_text(
        "---\nteam: dev\nclient: internal\n---\n\n"
        "| # | ชิ้น | วัน | สถานะ | ใช้ได้ว่า |\n| :-: | --- | --- | :-: | --- |\n"
        "| **M1** | ทำของ | — | ⬜ | ได้ |\n",
        encoding="utf-8",
    )
    sops = tmp_path / "docs" / "sops"
    sops.mkdir(parents=True)
    (sops / "sop-agent-orchestration.md").write_text(SOP, encoding="utf-8")
    people = tmp_path / "team-os" / "people"
    people.mkdir(parents=True)
    (people / "roles.md").write_text(roles, encoding="utf-8")
    if rituals is not None:
        wow = tmp_path / "team-os" / "ways-of-working"
        wow.mkdir(parents=True)
        (wow / "rituals.md").write_text(rituals, encoding="utf-8")
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _clear_cache():
    workspace.invalidate_cache()
    yield


def _by_key(payload: dict) -> dict[str, dict]:
    return {r["key"]: r for r in payload["rituals"]}


def test_registry_reads_the_pinned_columns_not_the_first_table(tmp_path: Path):
    """The table is found by its `key`/`role`/`client` headers.

    rituals.md opens with a routing table that has nothing to do with rituals,
    and reserves the right to reword every Thai heading around the register —
    so the header cells, not the heading, are the contract (ADR-0036 §SD2).
    """
    payload = workspace.ritual_registry(_repo(tmp_path), use_cache=False)

    assert payload["present"] is True
    keys = _by_key(payload)
    assert "จังหวะมีอะไรบ้าง" not in keys  # the routing table is not the register
    assert set(keys) == {"morning reconcile-delta", "comm-window", "EOD-prep", "weekly"}


def test_a_row_without_a_key_is_not_returned(tmp_path: Path):
    """§SD3 note 3 — three rituals have no clock, so they are never drawn as a
    bar and must not be reported as "declared but unmapped" every single day."""
    keys = _by_key(workspace.ritual_registry(_repo(tmp_path), use_cache=False))
    assert "—" not in keys
    assert all(r["key"] for r in keys.values())


def test_assignment_is_composed_with_four_full_segments(tmp_path: Path):
    """The office resolves out of roles.md, so nothing is left as `-`, and the
    id carries no date: it names the ritual, not today's run of it."""
    keys = _by_key(workspace.ritual_registry(_repo(tmp_path), use_cache=False))

    assert (
        keys["EOD-prep"]["assignment"] == "internal/business/product-owner/eod-prep"
    )
    assert keys["EOD-prep"]["office"] == "business"
    # The client is read per row, never assumed: this one is billed elsewhere.
    assert keys["weekly"]["assignment"] == "winona/business/product-owner/weekly"
    assert all("/-/" not in r["assignment"] for r in keys.values())


def test_definition_pointer_joins_on_the_key_and_is_root_relative(tmp_path: Path):
    """The two tables word the same ritual differently ("EOD-prep + forge
    pre-check" vs "…F1–F5"), so the join is on the key. And the pointer is
    rewritten from rituals.md-relative to workspace-relative, because that is
    where the dispatched session opens files from."""
    keys = _by_key(workspace.ritual_registry(_repo(tmp_path), use_cache=False))

    assert (
        keys["EOD-prep"]["reads"]
        == "docs/runbooks/runbook-daily-plan-generation.md ขั้น 0–7"
    )
    assert (
        keys["morning reconcile-delta"]["reads"]
        == "docs/runbooks/runbook-daily-plan-generation.md ขั้น 9"
    )
    assert keys["comm-window"]["reads"] == "meta/daily/plan.md § จังหวะวันทำงาน"


def test_a_missing_client_blocks_the_button_it_is_never_defaulted(tmp_path: Path):
    """§SD3 — filling `internal` in for a blank cell files the work under the
    wrong account permanently in `git log`, which is worse than no button."""
    text = RITUALS.replace(
        "| comm-window ×3 | `comm-window` | `product-owner` | `internal` |",
        "| comm-window ×3 | `comm-window` | `product-owner` |  |",
    )
    keys = _by_key(
        workspace.ritual_registry(_repo(tmp_path, rituals=text), use_cache=False)
    )

    row = keys["comm-window"]
    assert row["dispatchable"] is False
    assert row["missing"] == ["client"]
    assert row["assignment"] is None
    # …and the rest of the register still works: one bad row is not an outage.
    assert keys["EOD-prep"]["dispatchable"] is True


def test_an_unknown_role_resolves_to_nothing_rather_than_a_neighbour(tmp_path: Path):
    """Resolution runs through the same resolver slices.md rows use (§SD3,
    ADR-0035 §SD2). A ritual has no project default to fall back to, so an
    unrecognised value means no button — not the first role in the table."""
    text = RITUALS.replace(
        "| comm-window ×3 | `comm-window` | `product-owner` |",
        "| comm-window ×3 | `comm-window` | `wizard` |",
    )
    row = _by_key(
        workspace.ritual_registry(_repo(tmp_path, rituals=text), use_cache=False)
    )["comm-window"]

    assert row["role"] is None
    assert row["dispatchable"] is False
    assert "role" in row["missing"]


def test_a_discipline_in_the_role_cell_resolves_like_a_slices_row(tmp_path: Path):
    """One resolver for both registers: `ops` is a discipline product-owner
    owns, and it lands there rather than being rejected."""
    text = RITUALS.replace(
        "| comm-window ×3 | `comm-window` | `product-owner` |",
        "| comm-window ×3 | `comm-window` | `ops` |",
    )
    row = _by_key(
        workspace.ritual_registry(_repo(tmp_path, rituals=text), use_cache=False)
    )["comm-window"]

    assert row["role"] == "Product Owner"
    assert row["assignment"] == "internal/business/product-owner/comm-window"


def test_a_renamed_column_turns_the_register_off_and_says_so(tmp_path: Path):
    """The three headers are pinned English keywords precisely so this is a
    clean, visible off rather than a half-read table (S-01's chosen direction:
    dark, not guessed)."""
    text = RITUALS.replace("| `client` |", "| `ลูกค้า` |")
    payload = workspace.ritual_registry(_repo(tmp_path, rituals=text), use_cache=False)

    assert payload["present"] is False
    assert "client" in payload["reason"]
    assert payload["rituals"] == []


def test_no_rituals_file_at_all(tmp_path: Path):
    payload = workspace.ritual_registry(_repo(tmp_path, rituals=None), use_cache=False)

    assert payload["present"] is False
    assert payload["rituals"] == []


def test_no_dispatch_map_means_no_button_but_the_rows_still_report(tmp_path: Path):
    """With roles.md unreadable there is no tier to pin, so nothing is
    dispatchable — but §SD6 still has to be able to say which keys were
    declared and went unmatched."""
    root = _repo(tmp_path)
    (Path(root) / "team-os" / "people" / "roles.md").unlink()

    payload = workspace.ritual_registry(root, use_cache=False)

    assert payload["present"] is True
    assert len(payload["rituals"]) == 4
    assert all(not r["dispatchable"] for r in payload["rituals"])
