#!/usr/bin/env python3
"""The slots team-os declares, and per-project scoping of the role count.

Covers ADR-0040 §SD2–§SD5. The failure this file exists to prevent is the quiet
one: a reworded heading upstream turning "this project is missing rollout.md"
into "this project is complete", or a project-scoped count silently answering
with the whole workspace's history.

Run:
    pytest tests/test_project_gaps.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_plane import role_activity, workspace  # noqa: E402

from test_role_activity import ROLES_MD, SOP_MD  # noqa: E402


# The shape of team-os/projects/README.md § ช่องที่ต้นแบบมี …: a first cell that
# names a file, one row that names the *router's* table instead, and a count
# column the board must ignore because it is maintained by hand.
PROJECTS_README = """---
title: Projects
---

# Projects

| อยากรู้ | เปิด |
| --- | --- |
| โปรเจกต์หนึ่ง ๆ ทำอะไร | `../../projects/<name>/context.md` |

## ช่องที่ต้นแบบมี แต่ workspace ยังไม่มี

| ช่อง | ตอบคำถามอะไร | มีกี่โปรเจกต์ |
| --- | --- | --- |
| ตาราง **สถานะ + เจ้าของ** ที่ router | โปรเจกต์นี้ถึงไหนแล้ว | ⬜ router มีแค่ 2 คอลัมน์ |
| `scope.md` | อะไรอยู่ในขอบเขต | 🟠 **1/31** — `ai-chatbot` |
| `slices.md` | งานแบ่งเป็นชิ้น | 🟠 **1/31** |
| `risks.md` | อะไรที่รู้ว่าเสี่ยง | 🟠 **1/31** |
| `rollout.md` | แผนปล่อยของ | ⬜ 0/31 |
| `retro.md` | จบแล้วได้บทเรียนอะไร | ⬜ 0/31 |
| `hld.md` (living) | โครงระบบปัจจุบัน | 🟠 6/31 |

## 🥇 โปรเจกต์นำร่อง

| ไฟล์ | ตอบคำถาม |
| --- | --- |
| [`scope.md`](../../projects/ai-chatbot/scope.md) | ต้องไม่ถูกอ่านเป็นช่อง |
"""

SLICES_MD = """---
title: demo
client: internal
team: dev
---

# Slices

| # | ชิ้น | วัน | สถานะ | ใช้งานได้จริงว่า | role |
| :-: | --- | --- | :-: | --- | :-: |
| **D1** | จบแล้ว | — | ✅ | เสร็จ | tech-lead |
| **D2** | ค้าง | — | ⬜ | รอ | |
"""


def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, env=env
    )


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo: Path, path: str, message: str, *, date: str | None = None) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(len(message)), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", message, date=date)


def _body(summary: str, assignment: str) -> str:
    return f"{summary}\n\nverification: none\n\nAssignment: {assignment}"


@pytest.fixture(autouse=True)
def _clear_cache():
    workspace.invalidate_cache()
    yield
    workspace.invalidate_cache()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "team-os" / "people").mkdir(parents=True)
    (tmp_path / "team-os" / "people" / "roles.md").write_text(ROLES_MD, encoding="utf-8")
    (tmp_path / "team-os" / "projects").mkdir(parents=True)
    (tmp_path / "team-os" / "projects" / "README.md").write_text(
        PROJECTS_README, encoding="utf-8"
    )
    (tmp_path / "docs" / "sops").mkdir(parents=True)
    (tmp_path / "docs" / "sops" / "sop-agent-orchestration.md").write_text(
        SOP_MD, encoding="utf-8"
    )
    for name in ("alpha", "beta"):
        proj = tmp_path / "projects" / name
        proj.mkdir(parents=True)
        (proj / "slices.md").write_text(SLICES_MD, encoding="utf-8")
    # alpha is further along: scope + a living HLD folder; beta has neither
    (tmp_path / "projects" / "alpha" / "scope.md").write_text("# scope", encoding="utf-8")
    (tmp_path / "projects" / "alpha" / "docs" / "design").mkdir(parents=True)
    (tmp_path / "projects" / "alpha" / "docs" / "design" / "hld.md").write_text(
        "# hld", encoding="utf-8"
    )
    _init(tmp_path)
    _commit(
        tmp_path,
        "seed.txt",
        _body("chore: seed", "internal/build/developer/seed"),
        date="2026-01-01 00:00:00 +0700",
    )
    return tmp_path


# ── the declared slot list (§SD2 · §SD3) ─────────────────────────────────────


def test_slots_come_from_the_declared_table_in_declared_order(repo):
    slots = workspace.project_slots(repo)
    assert slots["source"] == "declared"
    assert [s["key"] for s in slots["slots"]] == [
        "scope",
        "slices",
        "risks",
        "rollout",
        "retro",
        "hld",
    ]


def test_hld_is_the_one_slot_whose_name_is_not_its_location(repo):
    by_key = {s["key"]: s for s in workspace.project_slots(repo)["slots"]}
    assert by_key["hld"] == {"key": "hld", "kind": "dir", "where": "docs/design"}
    assert by_key["rollout"] == {
        "key": "rollout",
        "kind": "file",
        "where": "rollout.md",
    }


def test_a_template_row_naming_no_file_is_reported_not_dropped(repo):
    """The router's own status table is a declared slot the board cannot check."""
    slots = workspace.project_slots(repo)
    assert slots["unmapped"] == ["ตาราง สถานะ + เจ้าของ ที่ router"]


def test_later_sections_are_not_read_as_slots(repo):
    """The pilot table links to scope.md too — anchoring on the heading is why
    that does not turn into a second, duplicate slot."""
    keys = [s["key"] for s in workspace.project_slots(repo)["slots"]]
    assert keys.count("scope") == 1


def test_missing_declaration_falls_back_and_says_so(repo):
    (repo / "team-os" / "projects" / "README.md").write_text(
        "# Projects\n\nno table here\n", encoding="utf-8"
    )
    slots = workspace.project_slots(repo)
    assert slots["source"] == "fallback"
    assert slots["reason"]
    assert [s["key"] for s in slots["slots"]] == list(workspace.FALLBACK_SLOTS)


def test_has_follows_the_declared_slots(repo):
    out = workspace.workspace_overview(str(repo), use_cache=False)
    by_name = {p["name"]: p for p in out["projects"]}
    assert by_name["alpha"]["has"] == {
        "scope": True,
        "slices": True,
        "risks": False,
        "rollout": False,
        "retro": False,
        "hld": True,
    }
    assert by_name["beta"]["has"]["scope"] is False
    assert by_name["beta"]["has"]["hld"] is False
    assert out["slots"]["source"] == "declared"


def test_has_keeps_the_original_three_keys_when_the_table_is_unreadable(repo):
    """The card badge is a shipped feature; a reworded heading must not empty it."""
    (repo / "team-os" / "projects" / "README.md").unlink()
    out = workspace.workspace_overview(str(repo), use_cache=False)
    alpha = [p for p in out["projects"] if p["name"] == "alpha"][0]
    assert set(alpha["has"]) == {"scope", "risks", "hld"}
    assert out["slots"]["source"] == "fallback"


# ── scoping the count to one project (§SD5) ──────────────────────────────────


def test_workspace_commits_are_scoped_by_path(repo):
    _commit(repo, "projects/alpha/notes.md", _body("docs: a", "internal/build/cto/x"))
    _commit(
        repo, "projects/beta/notes.md", _body("docs: b", "internal/build/devops/y")
    )

    scoped = role_activity.role_activity(str(repo), project="alpha")
    by_role = {r["role"]: r["commits"] for r in scoped["roles"]}
    assert by_role["cto"] == 1
    assert by_role["devops"] == 0  # beta's commit is not alpha's
    assert scoped["project"] == "alpha"

    whole = role_activity.role_activity(str(repo))
    assert {r["role"]: r["commits"] for r in whole["roles"]}["devops"] == 1


def test_a_commit_touching_two_projects_counts_for_both(repo):
    target = repo / "projects" / "alpha" / "shared.md"
    target.write_text("x", encoding="utf-8")
    (repo / "projects" / "beta" / "shared.md").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", _body("docs: both", "internal/build/qa/x"))

    for name in ("alpha", "beta"):
        out = role_activity.role_activity(str(repo), project=name)
        assert {r["role"]: r["commits"] for r in out["roles"]}["qa"] == 1


def test_only_the_scoped_projects_own_repos_are_walked(repo):
    for name in ("alpha", "beta"):
        nested = repo / "projects" / name / "repos" / name
        _init(nested)
        _commit(
            nested,
            "app.py",
            _body(f"feat: {name}", "internal/build/senior-developer/x"),
        )

    out = role_activity.role_activity(str(repo), project="alpha")
    assert [r["path"] for r in out["repos"]] == [".", "projects/alpha/repos/alpha"]
    assert {r["role"]: r["commits"] for r in out["roles"]}["senior-developer"] == 1


def test_open_rows_are_scoped_too(repo):
    whole = role_activity.role_activity(str(repo))
    scoped = role_activity.role_activity(str(repo), project="alpha")
    assert {r["role"]: r["open_total"] for r in whole["roles"]}["developer"] == 2
    assert {r["role"]: r["open_total"] for r in scoped["roles"]}["developer"] == 1


def test_unknown_project_is_refused_not_answered_with_zeros(repo):
    with pytest.raises(ValueError):
        role_activity.role_activity(str(repo), project="gamma")
    with pytest.raises(ValueError):
        role_activity.role_activity(str(repo), project="../etc")


def test_a_directory_without_slices_md_is_not_a_project(repo):
    (repo / "projects" / "draft").mkdir()
    with pytest.raises(ValueError):
        role_activity.role_activity(str(repo), project="draft")
