#!/usr/bin/env python3
"""control_plane.role_activity — who actually shipped (ADR-0039).

These tests pin the parts that would produce a *plausible wrong number* rather
than an error: a repo counted twice, a pre-cutover id folded in silently, a
role that disappears because it shipped nothing, and a commit with no id at all
vanishing from the denominator.

Run:
    pytest tests/test_role_activity.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_plane import role_activity, workspace  # noqa: E402


ROLES_MD = """---
title: roles
---

# Roles

## แกนความเป็นเจ้าของ — role → office · discipline

| role | office | เปิด discipline leaf | บันทึกผลลงที่ |
| --- | :-: | --- | --- |
| `cto` | `build` | `arch` · `security` | meta/adr-*.md |
| `tech-lead` | `build` | `software-design` · `ux-ui` | docs/adr/ |
| `senior-developer` | `build` | `dev` · `devex` | commit body |
| `developer` | `build` | `dev` | commit body |
| `devops` | `run` | `infra` | runbook |
| `qa` | `run` | — | PR review |
| `product-owner` | `business` | `forge` · `ops` | day file |

## โมเดลต่อ role

| Role | default | effort |
| --- | :-: | :-: |
| **CTO** | heavy | `xhigh` |
| **Tech Lead** | heavy | `high` |
| **Senior Developer** | standard | `high` |
| **Developer** | standard | `medium` |
| **DevOps** | standard | `high` |
| **QA** | standard | `high` |
| **Product Owner** | standard | `medium` |
"""

SOP_MD = """# orchestration

## Model Routing Economics

light = `claude-haiku-4-5`, standard = `claude-sonnet-5`, heavy = `claude-opus-5`
"""

SLICES_MD = """---
title: demo
client: internal
team: dev
---

# Slices

| # | ชิ้น | วัน | สถานะ | ใช้งานได้จริงว่า | role |
| :-: | --- | --- | :-: | --- | :-: |
| **D1** | เสร็จแล้ว | — | ✅ | จบ | |
| **D2** | ค้างของ developer | — | ⬜ | รอ | |
| **D3** | ค้างของ tech-lead | — | ⬜ | รอ | tech-lead |
| **D4** | รอคนเคาะ | — | ⬜ | 🖐️ คนเคาะ | tech-lead |
| **D5** | กำลังทำ | — | 🔄 | เดินอยู่ | tech-lead |
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


def _commit(repo: Path, message: str, *, date: str | None = None, n: list = []) -> None:
    n.append(1)
    (repo / f"f{len(n)}.txt").write_text(str(len(n)), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", message, date=date)


def _body(summary: str, assignment: str | None) -> str:
    trailer = f"\n\nAssignment: {assignment}" if assignment else ""
    return f"{summary}\n\nverification: none{trailer}"


@pytest.fixture(autouse=True)
def _clear_cache():
    workspace.invalidate_cache()
    yield
    workspace.invalidate_cache()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A workspace with the two tables the module reads, plus one slices.md."""
    (tmp_path / "team-os" / "people").mkdir(parents=True)
    (tmp_path / "team-os" / "people" / "roles.md").write_text(ROLES_MD, encoding="utf-8")
    (tmp_path / "docs" / "sops").mkdir(parents=True)
    (tmp_path / "docs" / "sops" / "sop-agent-orchestration.md").write_text(
        SOP_MD, encoding="utf-8"
    )
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    (proj / "slices.md").write_text(SLICES_MD, encoding="utf-8")
    _init(tmp_path)
    # Dated far in the past on purpose: `git log --since` stops walking once it
    # meets a commit older than the cutoff, so every fixture commit after this
    # one must be *newer* than it or later commits become unreachable. It also
    # keeps the seed out of every window, so the divisor counts only what the
    # test itself committed.
    _commit(tmp_path, _body("chore: seed", None), date="2026-01-01 00:00:00 +0700")
    return tmp_path


def _by_role(out: dict) -> dict[str, dict]:
    return {r["role"]: r for r in out["roles"]}


# ── the count itself ─────────────────────────────────────────────────────────


def test_direct_ids_count_against_their_role(repo):
    _commit(repo, _body("feat: a", "internal/build/developer/x"))
    _commit(repo, _body("feat: b", "internal/build/developer/y"))
    _commit(repo, _body("feat: c", "internal/build/cto/z"))

    roles = _by_role(role_activity.role_activity(str(repo)))
    assert roles["developer"]["commits"] == 2
    assert roles["developer"]["direct"] == 2
    assert roles["developer"]["legacy"] == 0
    assert roles["cto"]["commits"] == 1


def test_pre_cutover_discipline_id_resolves_but_is_reported_as_such(repo):
    """`ops` is product-owner's discipline — countable, but not what the id said."""
    _commit(repo, _body("docs: a", "internal/business/ops/x"))
    _commit(repo, _body("docs: b", "internal/build/arch/y"))
    _commit(repo, _body("docs: c", "internal/business/product-owner/z"))

    roles = _by_role(role_activity.role_activity(str(repo)))
    assert roles["product-owner"]["commits"] == 2
    assert roles["product-owner"]["direct"] == 1
    assert roles["product-owner"]["legacy"] == 1
    assert roles["product-owner"]["legacy_slugs"] == {"ops": 1}
    # `arch` belongs to cto, and the payload says the number was translated
    assert roles["cto"]["legacy_slugs"] == {"arch": 1}


def test_a_role_that_shipped_nothing_is_a_zero_row_not_an_absent_one(repo):
    """§SD2 — the whole point of S26: `0 / 0` has to be readable on screen."""
    _commit(repo, _body("feat: a", "internal/build/developer/x"))

    out = role_activity.role_activity(str(repo))
    assert [r["role"] for r in out["roles"]] == [
        "cto",
        "tech-lead",
        "senior-developer",
        "developer",
        "devops",
        "qa",
        "product-owner",
    ]
    qa = _by_role(out)["qa"]
    assert qa["commits"] == 0 and qa["open_total"] == 0
    assert qa["silent"] is True
    assert _by_role(out)["developer"]["silent"] is False


def test_one_commit_naming_two_roles_credits_each_once(repo):
    _commit(
        repo,
        "feat: two\n\nverification: none\n\n"
        "Assignment: internal/build/developer/x\n"
        "Assignment: internal/build/developer/y\n"
        "Assignment: internal/build/qa/z",
    )
    roles = _by_role(role_activity.role_activity(str(repo)))
    assert roles["developer"]["commits"] == 1  # same role twice = one commit
    assert roles["qa"]["commits"] == 1


def test_unresolvable_third_slot_is_reported_not_dropped(repo):
    _commit(repo, _body("feat: a", "internal/build/wizard/x"))
    _commit(repo, _body("feat: b", "internal/build/-/y"))
    _commit(repo, _body("feat: c", "internal/build/developer"))  # 3 segments

    out = role_activity.role_activity(str(repo))
    assert out["unresolved"]["count"] == 3
    assert {s["assignment"] for s in out["unresolved"]["samples"]} == {
        "internal/build/wizard/x",
        "internal/build/-/y",
        "internal/build/developer",
    }
    assert sum(r["commits"] for r in out["roles"]) == 0


# ── the denominator (§SD5) ───────────────────────────────────────────────────


def test_commits_with_no_assignment_stay_visible_in_the_divisor(repo):
    _commit(repo, _body("feat: a", "internal/build/developer/x"))
    _commit(repo, "chore(deps): bump something\n\nno trailer at all")

    out = role_activity.role_activity(str(repo))
    root = [r for r in out["repos"] if r["path"] == "."][0]
    assert root["commits"] == 2  # the seed is dated outside every window
    assert root["with_assignment"] == 1
    assert out["totals"]["commits"] == 2
    assert out["totals"]["with_assignment"] == 1


# ── which repos count (§SD4) ─────────────────────────────────────────────────


def test_a_plain_directory_inside_the_workspace_is_not_a_second_repo(repo):
    """`projects/<x>/repo/` that is just a folder returns the *workspace's* log."""
    plain = repo / "projects" / "webapp" / "repo"
    plain.mkdir(parents=True)
    (plain / "app.py").write_text("x", encoding="utf-8")
    _commit(repo, _body("feat: a", "internal/build/developer/x"))

    out = role_activity.role_activity(str(repo))
    assert [r["path"] for r in out["repos"]] == ["."]
    assert _by_role(out)["developer"]["commits"] == 1  # not 2


def test_a_real_nested_repo_is_counted_once_and_named(repo):
    nested = repo / "projects" / "demo" / "repos" / "demo"
    _init(nested)
    _commit(nested, _body("feat: app", "internal/build/senior-developer/x"))

    out = role_activity.role_activity(str(repo))
    assert [r["path"] for r in out["repos"]] == [".", "projects/demo/repos/demo"]
    assert _by_role(out)["senior-developer"]["commits"] == 1


def test_a_linked_worktree_is_not_counted_as_a_second_repo(repo):
    """A worktree answers `--show-toplevel` with itself but shares every commit."""
    nested = repo / "projects" / "demo" / "repos" / "demo"
    _init(nested)
    _commit(nested, _body("feat: app", "internal/build/senior-developer/x"))
    _git(
        nested,
        "worktree",
        "add",
        str(repo / "projects" / "demo" / "repos" / "demo.worktrees"),
        "-b",
        "side",
    )

    out = role_activity.role_activity(str(repo))
    assert [r["path"] for r in out["repos"]] == [".", "projects/demo/repos/demo"]
    assert _by_role(out)["senior-developer"]["commits"] == 1


# ── the window (§SD7) ────────────────────────────────────────────────────────


def test_window_edge_is_the_owners_day_not_the_containers(repo):
    """The discriminating case: 03:00 Bangkok is still *yesterday* in UTC.

    Asia/Bangkok midnight comes seven hours before UTC midnight of the same
    date, so the owner's window is the wider one — a commit inside that gap is
    counted here and would be dropped by a UTC cutoff. Nothing can be inside a
    UTC window and outside the Bangkok one, so that direction has no test.
    """
    since = role_activity._since_date(1)  # today, Asia/Bangkok
    yesterday = date.fromisoformat(since) - timedelta(days=1)
    # oldest first — see the seed commit's note about --since and traversal
    _commit(
        repo,
        _body("feat: out", "internal/build/cto/y"),
        date=f"{yesterday} 12:00:00 +0700",
    )
    _commit(
        repo,
        _body("feat: in", "internal/build/developer/x"),
        date=f"{since} 03:00:00 +0700",  # = {yesterday} 20:00 UTC
    )
    out = role_activity.role_activity(str(repo), days=1)
    assert out["window"] == {"days": 1, "since": since, "tz": "+07:00"}
    assert _by_role(out)["developer"]["commits"] == 1
    assert _by_role(out)["cto"]["commits"] == 0


@pytest.mark.parametrize("days", [0, -1, 366])
def test_out_of_range_window_is_refused_not_clamped(repo, days):
    with pytest.raises(ValueError):
        role_activity.role_activity(str(repo), days=days)


# ── rows still open (§SD6) ───────────────────────────────────────────────────


def test_open_rows_split_by_column_and_keep_the_owner_mark_out_of_todo(repo):
    roles = _by_role(role_activity.role_activity(str(repo)))
    # D3 ⬜ + D4 ⬜-with-owner-mark + D5 🔄, all role `tech-lead`
    assert roles["tech-lead"]["open"] == {
        "todo": 1,
        "running": 1,
        "next": 0,
        "owner": 1,
    }
    assert roles["tech-lead"]["open_total"] == 3
    # D2 has no role cell, so it falls to the file's `team: dev` default
    assert roles["developer"]["open"]["todo"] == 1
    # D1 is ✅ — done rows are nobody's backlog
    assert roles["qa"]["open_total"] == 0


def test_rows_whose_role_does_not_resolve_are_not_pushed_onto_a_role(repo):
    (repo / "docs" / "sops" / "sop-agent-orchestration.md").write_text(
        "# no tier sentence here", encoding="utf-8"
    )
    out = role_activity.role_activity(str(repo))
    assert out["open_unassigned"]["todo"] == 2  # D2 + D3, unresolved without dispatch
    assert all(r["open_total"] == 0 for r in out["roles"])


# ── failure modes ────────────────────────────────────────────────────────────


def test_missing_ownership_table_reports_instead_of_inventing_roles(repo):
    (repo / "team-os" / "people" / "roles.md").write_text("# nothing", encoding="utf-8")
    out = role_activity.role_activity(str(repo))
    assert out["present"] is False
    assert "roles.md" in out["reason"]
    assert "roles" not in out


def test_bad_repo_root_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        role_activity.role_activity(str(tmp_path / "nope"))


def test_non_git_workspace_is_not_fatal(tmp_path):
    (tmp_path / "team-os" / "people").mkdir(parents=True)
    (tmp_path / "team-os" / "people" / "roles.md").write_text(
        ROLES_MD, encoding="utf-8"
    )
    out = role_activity.role_activity(str(tmp_path))
    assert out["present"] is True
    assert out["repos"] == []
    assert all(r["commits"] == 0 for r in out["roles"])
