"""ADR-0041 — the three belt surfaces the /work panel draws per role.

RED-first: every case below describes the payload before the reader exists.

The fixtures write a miniature workspace rather than pointing at the real one:
the whole point of §SD2 is that the station list is *not* pinned in code, so a
test that asserts against today's real SOP would pass for the wrong reason the
day the belt grows a station.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import workspace  # noqa: E402


SOP = """---
title: "SOP: Pipeline Handoff"
---

# SOP: Pipeline Handoff

## Scope — the pipeline and its batons

| From stage (discipline) | Baton artifact | To stage (discipline) |
| --- | --- | --- |
| `forge` — settle plan | plan | `software-design` |
| `software-design` — design | HLD | `ux-ui` (UI surface) · else `dev` |
| `ux-ui` — design the look | preview | `dev` |
| `dev` — implement | merged code | `infra` |
| `infra` — deploy | release ref | `close` **เมื่อตัวกระตุ้นติด** |
| `close` — ปิดรอบ **(ด่าน ไม่ใช่ discipline)** | เจ็ดลายเซ็น | — (closed) |

### 7.2 เจ็ดลายเซ็น

| role | สิ่งที่ต้องปิด | ลงที่ไหน |
| --- | --- | --- |
| `product-owner` | เกณฑ์ถูกวัดแล้ว | `scope.md` · `slices.md` |
| `tech-lead` | HLD ตรง | `docs/design/*` |
| `devops` | ปล่อยของแล้ว | `rollout.md` **หรือใบที่ทำหน้าที่นั้นใต้ชื่ออื่น** (§ 7.3) |
| `qa` | รอบตรวจระดับโปรเจกต์ | **thread ของ PR ใบนี้** — ไม่เซ็นในเอกสารของ `product-owner` |
| `cto` | ไม่มีของค้าง | `meta/adr-*.md` |
| `senior-developer` | ADR ครบ | commit body · ADR |
| `developer` | commit body ครบ | commit body |

⚠️ **5 ใน 7 บรรทัดไม่มีเครื่องบังคับ** — มีแค่ `senior-developer`/`developer` ที่
[`../../.githooks/commit-msg`](../../.githooks/commit-msg) ครอบ · อย่าวางแผนโดยคิดว่ามีตาข่ายรับ
([`../../team-os/ways-of-working/definition-of-done.md`](../../team-os/ways-of-working/definition-of-done.md) § บังคับด้วยอะไร)

## Decision flow
"""

ROLES = """# roles

## แกนความเป็นเจ้าของ — role → office

| role | office | เปิด discipline leaf | บันทึกผลลงที่ |
| --- | :-: | --- | --- |
| `cto` | `build` | `arch` · `security` | `meta/adr-*.md` |
| `tech-lead` | `build` | `software-design` · `ux-ui` | HLD |
| `senior-developer` | `build` | `dev` · `devex` | commit body |
| `developer` | `build` | `dev` | commit body |
| `devops` | `run` | `infra` | runbook |
| `qa` | `run` | — | PR thread |
| `product-owner` | `business` | `forge` · `ops` | slices |

## โมเดลต่อ role
"""


def _workspace(tmp_path: Path, sop: str = SOP, roles: str = ROLES) -> Path:
    (tmp_path / "docs" / "sops").mkdir(parents=True)
    (tmp_path / "docs" / "sops" / "sop-pipeline-handoff.md").write_text(
        sop, encoding="utf-8"
    )
    (tmp_path / "team-os" / "people").mkdir(parents=True)
    (tmp_path / "team-os" / "people" / "roles.md").write_text(roles, encoding="utf-8")

    # Three projects: one carries everything, one only a scope, one nothing.
    for name in ("alpha", "beta", "gamma"):
        (tmp_path / "projects" / name).mkdir(parents=True)
    (tmp_path / "projects" / "alpha" / "scope.md").write_text("x", encoding="utf-8")
    (tmp_path / "projects" / "alpha" / "slices.md").write_text("x", encoding="utf-8")
    (tmp_path / "projects" / "alpha" / "docs" / "design").mkdir(parents=True)
    (tmp_path / "projects" / "alpha" / "docs" / "design" / "hld.md").write_text(
        "x", encoding="utf-8"
    )
    (tmp_path / "projects" / "beta" / "scope.md").write_text("x", encoding="utf-8")

    # A workspace-level target for the `cto` row to resolve against.
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "adr-one.md").write_text("x", encoding="utf-8")
    (tmp_path / "meta" / "adr-two.md").write_text("x", encoding="utf-8")

    (tmp_path / ".githooks").mkdir()
    (tmp_path / ".githooks" / "commit-msg").write_text("#!/bin/sh\n", encoding="utf-8")
    return tmp_path


def _stations(payload: dict) -> dict[str, list[str]]:
    return {s["stage"]: s["roles"] for s in payload["stations"]["stages"]}


def _rows(payload: dict) -> dict[str, dict]:
    return {r["role"]: r for r in payload["signatures"]["rows"]}


# ── ① stations ───────────────────────────────────────────────────────────────


def test_stations_come_from_both_from_and_to_columns(tmp_path):
    """`close` reaches the board through the `To` cell of the `infra` row one
    revision before it has a row of its own — reading only `From` would miss
    exactly the station that was added yesterday (§SD2)."""
    payload = workspace.pipeline_surfaces(_workspace(tmp_path))
    assert payload["stations"]["present"] is True
    assert [s["stage"] for s in payload["stations"]["stages"]] == [
        "forge",
        "software-design",
        "ux-ui",
        "dev",
        "infra",
        "close",
    ]


def test_a_new_station_appears_without_touching_code(tmp_path):
    """The whole reason §SD2 forbids a hardcoded list."""
    sop = SOP.replace(
        "| `close` — ปิดรอบ",
        "| `handover` — ส่งมอบ | เอกสาร | `close` |\n| `close` — ปิดรอบ",
    )
    payload = workspace.pipeline_surfaces(_workspace(tmp_path, sop=sop))
    assert "handover" in _stations(payload)


def test_dev_is_a_station_of_both_roles_that_declare_it(tmp_path):
    """§SD3 — the register says both hold `dev`; the dispatch tie-break is a
    different question and must not narrow this one."""
    assert _stations(workspace.pipeline_surfaces(_workspace(tmp_path)))["dev"] == [
        "senior-developer",
        "developer",
    ]


def test_close_is_a_station_nobody_holds_and_still_shows(tmp_path):
    """§SD4 — a station filtered off screen because it maps to no role is the
    same silence ADR-0039 §SD2 refused for a role with zero commits."""
    assert _stations(workspace.pipeline_surfaces(_workspace(tmp_path)))["close"] == []


def test_roles_without_a_station_are_reported_as_empty_not_absent(tmp_path):
    payload = workspace.pipeline_surfaces(_workspace(tmp_path))
    per_role = payload["stations"]["per_role"]
    assert per_role["qa"] == []
    assert per_role["cto"] == []
    assert per_role["tech-lead"] == ["software-design", "ux-ui"]


def test_renamed_stage_column_fails_dark(tmp_path):
    sop = SOP.replace("From stage (discipline)", "ต้นทาง")
    payload = workspace.pipeline_surfaces(_workspace(tmp_path, sop=sop))
    assert payload["stations"]["present"] is False
    assert payload["stations"]["reason"]
    # …and the other half keeps working (§SD7 — degrade per column).
    assert payload["signatures"]["present"] is True


# ── ③ closing surfaces ───────────────────────────────────────────────────────


def test_project_level_targets_count_every_project_folder(tmp_path):
    """§SD6 — the denominator is every folder under projects/, not the ones
    that happen to carry a slices.md: the close gate is every project's."""
    rows = _rows(workspace.pipeline_surfaces(_workspace(tmp_path)))
    targets = {t["token"]: t for t in rows["product-owner"]["targets"]}
    assert targets["scope.md"]["level"] == "project"
    assert (targets["scope.md"]["have"], targets["scope.md"]["total"]) == (2, 3)
    assert (targets["slices.md"]["have"], targets["slices.md"]["total"]) == (1, 3)


def test_a_directory_glob_resolves_per_project(tmp_path):
    rows = _rows(workspace.pipeline_surfaces(_workspace(tmp_path)))
    target = rows["tech-lead"]["targets"][0]
    assert target["token"] == "docs/design/*"
    assert (target["level"], target["have"], target["total"]) == ("project", 1, 3)


def test_a_workspace_level_target_counts_files_not_projects(tmp_path):
    rows = _rows(workspace.pipeline_surfaces(_workspace(tmp_path)))
    target = rows["cto"]["targets"][0]
    assert target["token"] == "meta/adr-*.md"
    assert target["level"] == "workspace"
    assert target["have"] == 2
    assert target["total"] is None  # a file count has no denominator


def test_a_target_nobody_has_still_reports_its_denominator(tmp_path):
    rows = _rows(workspace.pipeline_surfaces(_workspace(tmp_path)))
    target = rows["devops"]["targets"][0]
    assert (target["token"], target["have"], target["total"]) == ("rollout.md", 0, 3)


def test_prose_left_after_the_tokens_is_carried_as_the_rows_note(tmp_path):
    """§7.3 lets another file answer a line — that caveat must reach the screen
    from the SOP itself, not from a sentence retyped in the app."""
    rows = _rows(workspace.pipeline_surfaces(_workspace(tmp_path)))
    assert "ใต้ชื่ออื่น" in rows["devops"]["note"]


def test_a_backticked_non_path_is_not_mistaken_for_a_file(tmp_path):
    """`qa`'s cell names `product-owner` mid-sentence (§SD6)."""
    rows = _rows(workspace.pipeline_surfaces(_workspace(tmp_path)))
    assert rows["qa"]["targets"] == []
    assert "thread ของ PR" in rows["qa"]["note"]


def test_rows_with_no_file_target_are_marked_non_file(tmp_path):
    rows = _rows(workspace.pipeline_surfaces(_workspace(tmp_path)))
    for role in ("qa", "senior-developer", "developer"):
        assert rows[role]["targets"] == []


def test_a_target_that_tries_to_escape_the_workspace_is_refused(tmp_path):
    sop = SOP.replace("`meta/adr-*.md`", "`../../../etc/passwd`")
    rows = _rows(workspace.pipeline_surfaces(_workspace(tmp_path, sop=sop)))
    assert rows["cto"]["targets"] == []
    assert rows["cto"]["rejected"] == ["../../../etc/passwd"]


# ── enforcement note ─────────────────────────────────────────────────────────


def test_enforcement_is_read_from_the_sops_own_warning_line(tmp_path):
    payload = workspace.pipeline_surfaces(_workspace(tmp_path))
    enforcement = payload["signatures"]["enforcement"]
    assert enforcement["declared"] is True
    assert enforcement["roles"] == ["senior-developer", "developer"]
    assert enforcement["mechanism"] == ".githooks/commit-msg"
    assert enforcement["mechanism_exists"] is True


def test_the_five_of_seven_claim_is_computed_not_quoted(tmp_path):
    """§SD6 — if the hook ever covers another line, the screen moves because the
    named roles moved, not because someone edited a number in a sentence."""
    sop = SOP.replace("`senior-developer`/`developer`", "`senior-developer`/`developer`/`qa`")
    payload = workspace.pipeline_surfaces(_workspace(tmp_path, sop=sop))
    enforcement = payload["signatures"]["enforcement"]
    # Reported in the §7.2 table's own row order, not the sentence's.
    assert enforcement["roles"] == ["qa", "senior-developer", "developer"]
    assert enforcement["unenforced"] == 4
    assert enforcement["total"] == 7


def test_a_mechanism_that_is_not_in_the_tree_says_so(tmp_path):
    root = _workspace(tmp_path)
    (root / ".githooks" / "commit-msg").unlink()
    enforcement = workspace.pipeline_surfaces(root)["signatures"]["enforcement"]
    assert enforcement["mechanism"] == ".githooks/commit-msg"
    assert enforcement["mechanism_exists"] is False


def test_a_missing_warning_line_is_not_guessed(tmp_path):
    sop = SOP[: SOP.index("⚠️")] + "\n## Decision flow\n"
    enforcement = workspace.pipeline_surfaces(_workspace(tmp_path, sop=sop))[
        "signatures"
    ]["enforcement"]
    assert enforcement["declared"] is False
    assert enforcement["roles"] == []


# ── whole-file failure ───────────────────────────────────────────────────────


def test_a_missing_sop_blanks_both_columns_with_a_reason(tmp_path):
    root = _workspace(tmp_path)
    (root / "docs" / "sops" / "sop-pipeline-handoff.md").unlink()
    payload = workspace.pipeline_surfaces(root)
    assert payload["stations"]["present"] is False
    assert payload["signatures"]["present"] is False
    assert payload["stations"]["reason"] == payload["signatures"]["reason"]
    assert payload["source"] == "docs/sops/sop-pipeline-handoff.md"


def test_an_unreadable_roles_table_leaves_stations_unowned(tmp_path):
    """The stage list still reads; there is simply nobody to attribute it to —
    the register never gets invented from the other file (ADR-0039 §SD2)."""
    payload = workspace.pipeline_surfaces(_workspace(tmp_path, roles="# roles\n"))
    assert _stations(payload)["forge"] == []
    assert payload["stations"]["per_role"] == {}


# ── wiring into /workspace ───────────────────────────────────────────────────


def test_the_overview_carries_the_pipeline_block(tmp_path):
    """§SD1 — both new columns are functions of HEAD, so they ride /workspace
    and never the windowed /roles/activity payload."""
    payload = workspace.workspace_overview(str(_workspace(tmp_path)), use_cache=False)
    assert payload["pipeline"]["stations"]["present"] is True
    assert payload["pipeline"]["signatures"]["present"] is True


def test_the_overview_survives_a_workspace_without_the_sop(tmp_path):
    root = _workspace(tmp_path)
    (root / "docs" / "sops" / "sop-pipeline-handoff.md").unlink()
    payload = workspace.workspace_overview(str(root), use_cache=False)
    assert payload["pipeline"]["stations"]["present"] is False
    # …and the rest of the overview is untouched (§SD7 — the SOP feeds two
    # columns, not the board).
    assert [p["name"] for p in payload["projects"]] == ["alpha"]
