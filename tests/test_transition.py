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

import os
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
    assert any(
        "ตารางประกาศไว้ แต่โค้ดไม่มีตัวตรวจ" in d for d in transition.drift(root)
    )
    out = transition.evaluate(
        root, row=_row(stage="done"), to_stage="deployed", actor_role="cto"
    )
    assert out["allowed"] is False


def test_a_check_the_table_no_longer_declares_is_reported(tmp_path):
    fewer = TABLE.replace(
        "| `done` | `inprogress` | `product-owner` | ต้องมีเหตุผล |\n", ""
    )
    assert any(
        "ตารางไม่ประกาศแล้ว" in d
        for d in transition.drift(_root(tmp_path, table=fewer))
    )


# ── the button list a screen renders from (S43b) ────────────────────────────


def test_candidates_lists_every_declared_move_out_of_the_current_stage(tmp_path):
    out = transition.candidates(
        _root(tmp_path), row=_row(stage="readyqa"), actor_role="qa"
    )
    assert [c["to_stage"] for c in out] == ["inprogress", "readydeploy"]


def test_candidates_orders_by_belt_station_not_table_line_order(tmp_path):
    """`readyqa → inprogress` sits *after* `readyqa → readydeploy` in TABLE —
    the belt puts `inprogress` first, and the list must follow the belt."""
    out = transition.candidates(
        _root(tmp_path),
        row=_row(stage="readyqa", criteria={"done": 1, "total": 1}),
        actor_role="qa",
    )
    assert out[0]["to_stage"] == "inprogress"
    assert out[1]["to_stage"] == "readydeploy"


def test_candidates_is_empty_off_a_stage_with_no_declared_move(tmp_path):
    """`backlog`/`techdesign` have no row in the table (ADR-0044 §SD4) — the
    card skips itself, so there is nothing for a button to offer."""
    out = transition.candidates(
        _root(tmp_path), row=_row(stage="backlog"), actor_role="cto"
    )
    assert out == []


def test_candidates_fails_dark_the_same_way_evaluate_does(tmp_path):
    out = transition.candidates(
        _root(tmp_path, table=None), row=_row(stage="readydev"), actor_role="developer"
    )
    assert out == []


def test_candidates_reports_the_wrong_role_as_not_allowed(tmp_path):
    out = transition.candidates(
        _root(tmp_path), row=_row(stage="readydev"), actor_role="qa"
    )
    assert len(out) == 1 and out[0]["allowed"] is False
    assert "developer" in out[0]["reason"]


def test_candidates_probes_a_form_gated_move_as_allowed(tmp_path):
    """`review → readyqa` needs `env`/`risk` — fields that only exist once a
    dialog is open to type them. The list answers "would this role clear the
    gate", not "is the form filled", so it must not read this move as refused
    just because nobody has typed anything yet."""
    out = transition.candidates(
        _root(tmp_path), row=_row(stage="review"), actor_role="senior-developer"
    )
    assert out == [
        {
            "to_stage": "readyqa",
            "allowed": True,
            "reason": None,
            "roles": ["senior-developer"],
            "requires": "ฟอร์มส่งงานครบ",
        }
    ]


def test_candidates_still_refuses_an_open_blocker_despite_the_form_probe(tmp_path):
    """The probe fills form fields; it must never paper over a row fact like
    an open blocker or an unmet criterion — those still fail for real."""
    out = transition.candidates(
        _root(tmp_path),
        row=_row(stage="readydev", blocked_by=[{"id": "T0", "title": "x"}]),
        actor_role="developer",
    )
    assert out == [
        {
            "to_stage": "inprogress",
            "allowed": False,
            "reason": "ยังมีตัวบล็อกที่ไม่ปิด: T0",
            "roles": ["developer", "senior-developer"],
            "requires": "blocked-by ต้องว่าง",
        }
    ]


# ── the write path, against a real git repo ─────────────────────────────────
#
# No mock of git here on purpose: this is the first thing in the board that
# writes the register, and a fake `git` would prove that the fake works.

SLICES = """---
title: "demo — งานแบ่งเป็นชิ้น"
---

# Slices

| # | ชิ้น | วัน | สถานะ | ใช้งานได้จริงว่า | stage | part-of |
| :-: | --- | --- | :-: | --- | :--: | :--: |
| **T1** | ชิ้นแรก | จ. | ⬜ | ทำได้จริงว่า… | readydev | — |
| **T2** | ชิ้นสอง | อ. | ⬜ | เกณฑ์ของ T1 | — | T1 |
"""


def _git(cwd, *args):
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path):
    """A real workspace: git repo · the rules file · one project register."""
    root = _root(tmp_path)
    proj = root / "projects" / "demo"
    proj.mkdir(parents=True)
    (proj / "slices.md").write_text(SLICES, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _row(**over) -> dict:
    row = {"id": "T1", "stage": "readydev", "blocked_by": [], "criteria": None}
    row.update(over)
    return row


def test_a_refused_transition_writes_nothing(repo):
    before = (repo / "projects" / "demo" / "slices.md").read_text(encoding="utf-8")
    out = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="qa"
    )
    assert out["ok"] is False
    assert (repo / "projects" / "demo" / "slices.md").read_text(
        encoding="utf-8"
    ) == before
    assert not (repo / ".claude" / "worktrees").exists()


def test_an_allowed_transition_commits_in_the_cards_own_worktree(repo):
    out = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    assert out["ok"] is True, out["reason"]
    assert out["branch"] == "worktree-internal-developer-demo-t1"
    # the main checkout is untouched — Worktree-Per-Task is the mechanism here
    assert "readydev" in (repo / "projects" / "demo" / "slices.md").read_text(
        encoding="utf-8"
    )
    written = (Path(out["worktree"]) / "projects" / "demo" / "slices.md").read_text(
        encoding="utf-8"
    )
    assert "| inprogress |" in written


def test_only_the_named_row_and_only_its_stage_cell_move(repo):
    out = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    written = (Path(out["worktree"]) / "projects" / "demo" / "slices.md").read_text(
        encoding="utf-8"
    )
    t1, t2 = [l for l in written.split("\n") if l.startswith("| **T")]
    assert "inprogress" in t1 and "ชิ้นแรก" in t1 and "⬜" in t1
    assert t2 == "| **T2** | ชิ้นสอง | อ. | ⬜ | เกณฑ์ของ T1 | — | T1 |"


def test_the_commit_carries_a_well_formed_assignment_id(repo):
    out = transition.apply(
        repo,
        project="demo",
        row=_row(),
        to_stage="inprogress",
        actor_role="developer",
        office="build",
    )
    body = _git(Path(out["worktree"]), "log", "-1", "--pretty=%B").stdout
    assert "Assignment: internal/build/developer/demo-t1" in body


def test_the_form_lands_in_the_commit_not_in_a_cell(repo):
    """ADR-0046 §SD2 — the file holds state, the commit holds what happened."""
    out = transition.apply(
        repo,
        project="demo",
        row=_row(stage="review"),
        to_stage="readyqa",
        actor_role="senior-developer",
        form={"env": "local :8787", "risk": "เปลี่ยน schema"},
    )
    assert out["ok"] is True, out["reason"]
    tree = Path(out["worktree"])
    body = _git(tree, "log", "-1", "--pretty=%B").stdout
    assert "local :8787" in body and "เปลี่ยน schema" in body
    written = (tree / "projects" / "demo" / "slices.md").read_text(encoding="utf-8")
    assert "local :8787" not in written


def test_a_second_transition_reuses_the_same_worktree_and_branch(repo):
    """หนึ่งการ์ด = หนึ่ง branch = หนึ่ง PR (ADR-0044 §SD2)."""
    first = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    second = transition.apply(
        repo,
        project="demo",
        row=_row(stage="inprogress"),
        to_stage="review",
        actor_role="developer",
    )
    assert second["ok"] is True, second["reason"]
    assert second["branch"] == first["branch"]
    assert second["worktree"] == first["worktree"]
    assert second["commit"] != first["commit"]
    count = _git(Path(second["worktree"]), "rev-list", "--count", "HEAD").stdout.strip()
    assert count == "3"  # seed + two transitions


def test_a_row_the_register_does_not_have_writes_nothing(repo):
    out = transition.apply(
        repo,
        project="demo",
        row=_row(id="T9"),
        to_stage="inprogress",
        actor_role="developer",
    )
    assert out["ok"] is False and "T9" in out["reason"]


def test_moving_to_the_station_it_is_already_in_is_refused(repo):
    """Not an error to report as success: an empty commit would say a handoff
    happened when nothing moved."""
    out = transition.apply(
        repo,
        project="demo",
        row=_row(),
        to_stage="readydev",
        actor_role="developer",
    )
    assert out["ok"] is False


def test_a_blocked_commit_leaves_the_worktree_clean_for_a_retry(repo):
    """Found on first real use (2026-09-12): a `.githooks/commit-msg`-style
    rejection left the write staged but uncommitted, and the *next* call read
    that stray write off the same worktree as "already there" — refusing a
    move that had, in fact, never landed. The write must roll back with the
    failed commit, not survive it."""
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\necho blocked >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    blocked = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    assert blocked["ok"] is False and "blocked" in blocked["reason"]

    tree = repo / ".claude" / "worktrees" / "demo-t1"
    assert _git(tree, "status", "--porcelain").stdout == ""
    assert (tree / "projects" / "demo" / "slices.md").read_text(
        encoding="utf-8"
    ) == SLICES

    hook.unlink()  # the underlying problem (e.g. a missing office) is fixed
    retried = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    assert retried["ok"] is True, retried["reason"]


# ── the notice ──────────────────────────────────────────────────────────────


def test_the_payload_carries_the_form_fields_not_the_table_cell():
    body = transition.payload(
        project="demo",
        row=_row(),
        to_stage="inprogress",
        actor_role="developer",
        form={"env": "local", "risk": "ระวัง", "junk": "ไม่ควรหลุดออกไป"},
    )
    assert body["event"] == "card.stage_changed"
    assert body["from"] == "readydev" and body["to"] == "inprogress"
    assert body["handoff"] == {"env": "local", "risk": "ระวัง"}


def test_no_webhook_url_is_not_an_error(monkeypatch):
    """Telling the next station is passing the word on, not part of passing the
    work (ADR-0046 §SD3)."""
    monkeypatch.delenv("WORK_WEBHOOK_URL", raising=False)
    assert transition.notify({"a": 1})["sent"] is False


def test_an_unreachable_webhook_is_swallowed_not_raised(monkeypatch):
    """The write already happened; a dead endpoint must not undo it."""
    monkeypatch.setenv("WORK_WEBHOOK_URL", "http://127.0.0.1:9/none")
    out = transition.notify({"a": 1})
    assert out["sent"] is False and out["reason"]


# ── the publish leg (S46 · ADR-0048) ────────────────────────────────────────
#
# What these pin: the board carries the press out of the machine itself, and it
# does it with exactly two verbs. `gh` is stubbed with a script that RECORDS ITS
# ARGV — the same discipline test_dispatch.py takes with `claude`, and for the
# same reason: the thing worth testing is the command that would have been run,
# not GitHub's answer to it. The push half is real git against a real bare repo,
# because a fake push would prove nothing about the refspec.


@pytest.fixture
def remote(repo, tmp_path):
    """`repo`, with a real bare `origin` it can actually push to."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    return bare


def _stub_gh(tmp_path, monkeypatch, *, listed: str = "[]", created: str = "") -> Path:
    """A `gh` on PATH that appends its argv to a log and answers canned JSON."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh-argv.log"
    (bin_dir / "gh").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        'case "$1 $2" in\n'
        f'  "pr list") printf %s {listed!r} ;;\n'
        f'  "pr create") printf %s {created!r} ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


def test_a_press_pushes_the_branch_and_opens_the_cards_pr(
    remote, repo, tmp_path, monkeypatch
):
    log = _stub_gh(tmp_path, monkeypatch, created="https://github.com/o/r/pull/77")
    out = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    assert out["ok"] is True, out["reason"]
    assert out["published"]["pushed"] is True, out["published"]["reason"]
    assert out["published"]["pr"] == 77
    assert out["published"]["url"].endswith("/pull/77")
    # the commit really is on the remote, on the card's own branch
    assert (
        _git(remote, "rev-parse", out["branch"]).stdout.strip()
        == _git(Path(out["worktree"]), "rev-parse", "HEAD").stdout.strip()
    )
    argv = log.read_text(encoding="utf-8")
    assert "pr list --head worktree-internal-developer-demo-t1" in argv
    assert "pr create --base main --head worktree-internal-developer-demo-t1" in argv


def test_a_second_press_grows_the_same_pr_instead_of_opening_another(
    remote, repo, tmp_path, monkeypatch
):
    """One card is one PR (ADR-0044 §SD1) ⇒ the verb is *ensure* (§SD2). The
    second press must find the open PR and stop, or every station on the belt
    would leave a PR behind."""
    log = _stub_gh(
        tmp_path,
        monkeypatch,
        listed='[{"number":77,"url":"https://github.com/o/r/pull/77"}]',
    )
    first = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    second = transition.apply(
        repo,
        project="demo",
        row=_row(stage="inprogress"),
        to_stage="review",
        actor_role="developer",
    )
    assert first["published"]["pr"] == 77 and second["published"]["pr"] == 77
    assert "pr create" not in log.read_text(encoding="utf-8")
    assert second["branch"] == first["branch"]


def test_a_push_that_fails_reports_but_does_not_undo_the_commit(
    repo, tmp_path, monkeypatch
):
    """ADR-0048 §SD4 — the commit holds the words the operator typed into the
    handoff form. A network failure is a report, not a reason to throw them
    away; the next press pushes it, because the ensure is idempotent."""
    _stub_gh(tmp_path, monkeypatch)
    _git(repo, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    out = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    assert out["ok"] is True
    assert out["published"]["pushed"] is False and out["published"]["reason"]
    tree = Path(out["worktree"])
    assert (
        _git(tree, "log", "-1", "--pretty=%s")
        .stdout.strip()
        .startswith("docs(slices): T1")
    )
    assert _git(tree, "status", "--porcelain").stdout == ""


def test_no_remote_at_all_is_not_a_failed_press(repo, tmp_path, monkeypatch):
    """A clone with nowhere to push is a legitimate state — and the press that
    moved the row still happened."""
    _stub_gh(tmp_path, monkeypatch)
    out = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    assert out["ok"] is True
    assert out["published"] == {
        "pushed": False,
        "pr": 0,
        "url": "",
        "reason": "ไม่มี remote origin",
    }


def test_a_refused_press_still_answers_the_published_shape(repo):
    """Every answer carries the same keys — a caller reading `published` off a
    refusal gets "nothing was published", not a KeyError."""
    out = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="qa"
    )
    assert out["ok"] is False
    assert out["published"] == {"pushed": False, "pr": 0, "url": "", "reason": ""}


def test_publish_refuses_a_branch_that_is_not_a_card_branch(remote, repo):
    """The guard exists for one value: `main`. The refspec is built from the
    branch name, so a name that did not come from `apply()` never reaches git
    (ADR-0048 §SD3)."""
    import subprocess

    out = transition.publish(repo, "main", title="t", body="b")
    assert out["pushed"] is False and "branch ของการ์ด" in out["reason"]
    # nothing reached the remote: `main` does not exist there
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "main"],
        cwd=str(remote),
        capture_output=True,
        text=True,
    )
    assert probe.returncode != 0


def test_the_pr_body_names_the_card_and_the_branch_it_grows_on():
    body = transition.pr_body(
        project="demo", row=_row(), branch="worktree-internal-developer-demo-t1"
    )
    assert "**T1**" in body and "projects/demo/slices.md" in body
    assert "worktree-internal-developer-demo-t1" in body
    assert "merge commit" in body  # the >1-id warning pr-check will echo


# ── the check-reading leg (S44b · ADR-0049) ─────────────────────────────────
#
# `read_checks` never pushes or creates anything, so these tests only need a
# `gh` stub that answers `pr view` — no bare repo, no `apply()`. The four rows
# below are the exact job names pr-check.yml emits (verified 2026-09-13
# against the two real PRs `S44b`'s row cites — my-projects#1381/#1382).

import json as _json  # noqa: E402


def _stub_gh_view(tmp_path, monkeypatch, *, viewed: str, exit_code: int = 0) -> Path:
    """A `gh` on PATH whose `pr view` answers canned JSON (or fails)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh-argv.log"
    (bin_dir / "gh").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        'case "$1 $2" in\n'
        f'  "pr view") printf %s {viewed!r}; exit {exit_code} ;;\n'
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


_FOUR_GREEN = [
    {"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"}
    for n in (
        "route-lint (links, orphans, model-id drift)",
        "stop-list (does this need the owner?)",
        "assignment (one id per PR, merge method)",
        "GitGuardian Security Checks",
    )
]


def test_four_green_rows_reads_green(tmp_path, monkeypatch):
    """my-projects#1381 shape: all four known checks COMPLETED/SUCCESS,
    `mergeStateStatus: CLEAN` — the criterion S44b names by number."""
    body = {
        "state": "OPEN",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": _FOUR_GREEN,
    }
    log = _stub_gh_view(tmp_path, monkeypatch, viewed=_json.dumps(body))
    out = transition.read_checks(tmp_path, 1381)
    assert out == {"ok": True, "green": True, "reason": ""}
    assert "view 1381" in log.read_text(encoding="utf-8")
    assert "statusCheckRollup" in log.read_text(encoding="utf-8")


def test_a_red_known_check_names_itself_not_a_generic_failure(tmp_path, monkeypatch):
    """my-projects#1382 shape: `stop-list` FAILURE, the other three SUCCESS,
    and GitHub answers `mergeStateStatus: UNSTABLE` — not `BLOCKED` — because
    this repo has no branch protection (the exact evidence ADR-0049 §SD2
    stands on). The stop must name `stop-list`, not say "เช็กไม่ผ่าน"."""
    rows = [dict(r) for r in _FOUR_GREEN]
    for r in rows:
        if r["name"] == "stop-list (does this need the owner?)":
            r["conclusion"] = "FAILURE"
    body = {
        "state": "OPEN",
        "mergeStateStatus": "UNSTABLE",
        "statusCheckRollup": rows,
    }
    _stub_gh_view(tmp_path, monkeypatch, viewed=_json.dumps(body))
    out = transition.read_checks(tmp_path, 1382)
    assert out["ok"] is True
    assert out["green"] is False
    assert "stop-list" in out["reason"]
    assert "FAILURE" in out["reason"]
    assert out["reason"] != "เช็กไม่ผ่าน"


def test_unstable_merge_state_is_not_read_as_a_stop_by_itself(tmp_path, monkeypatch):
    """§SD2 in one assertion: a green rollup under `mergeStateStatus: UNSTABLE`
    is still green — `UNSTABLE` answers "can GitHub merge this", not "did the
    checks pass", and this repo has no required check to make them the same
    question."""
    body = {
        "state": "OPEN",
        "mergeStateStatus": "UNSTABLE",
        "statusCheckRollup": _FOUR_GREEN,
    }
    _stub_gh_view(tmp_path, monkeypatch, viewed=_json.dumps(body))
    out = transition.read_checks(tmp_path, 1)
    assert out == {"ok": True, "green": True, "reason": ""}


def test_assignment_red_alone_does_not_stop(tmp_path, monkeypatch):
    """§SD3/§SD7 — `assignment` red means "merge with `--merge`, not
    `--squash`", never "stop"."""
    rows = [dict(r) for r in _FOUR_GREEN]
    for r in rows:
        if r["name"] == "assignment (one id per PR, merge method)":
            r["conclusion"] = "FAILURE"
    body = {"state": "OPEN", "mergeStateStatus": "CLEAN", "statusCheckRollup": rows}
    _stub_gh_view(tmp_path, monkeypatch, viewed=_json.dumps(body))
    out = transition.read_checks(tmp_path, 1)
    assert out == {"ok": True, "green": True, "reason": ""}


def test_dirty_merge_state_stops_even_with_a_green_rollup(tmp_path, monkeypatch):
    """§SD2 — `DIRTY` is the one value of `mergeStateStatus` this function
    still reads, because it is a real conflict, not a check."""
    body = {
        "state": "OPEN",
        "mergeStateStatus": "DIRTY",
        "statusCheckRollup": _FOUR_GREEN,
    }
    _stub_gh_view(tmp_path, monkeypatch, viewed=_json.dumps(body))
    out = transition.read_checks(tmp_path, 1)
    assert out["ok"] is True
    assert out["green"] is False
    assert "DIRTY" in out["reason"]


def test_empty_rollup_is_not_green(tmp_path, monkeypatch):
    """§SD2 — `pr-check.yml` runs on every `pull_request` here, so an empty
    rollup means the workflow has not finished, not that there is nothing to
    check. Not-known is not green."""
    body = {"state": "OPEN", "mergeStateStatus": "CLEAN", "statusCheckRollup": []}
    _stub_gh_view(tmp_path, monkeypatch, viewed=_json.dumps(body))
    out = transition.read_checks(tmp_path, 1)
    assert out["ok"] is True
    assert out["green"] is False
    assert "ว่าง" in out["reason"]


def test_an_unknown_check_name_stops_and_is_named(tmp_path, monkeypatch):
    """§SD3 — a job the board has never heard of is a stop, not a pass; the
    reason names it so a reader knows what changed."""
    rows = [dict(r) for r in _FOUR_GREEN]
    rows.append(
        {"name": "new-security-scan", "status": "COMPLETED", "conclusion": "SUCCESS"}
    )
    body = {"state": "OPEN", "mergeStateStatus": "CLEAN", "statusCheckRollup": rows}
    _stub_gh_view(tmp_path, monkeypatch, viewed=_json.dumps(body))
    out = transition.read_checks(tmp_path, 1)
    assert out["ok"] is True
    assert out["green"] is False
    assert "new-security-scan" in out["reason"]


def test_gh_failing_outright_is_not_ok_and_is_not_read_as_green(tmp_path, monkeypatch):
    _stub_gh_view(tmp_path, monkeypatch, viewed="", exit_code=1)
    out = transition.read_checks(tmp_path, 1)
    assert out["ok"] is False
    assert out["green"] is False


# ── the merge leg (S44c · ADR-0049 §SD4 · §SD5 · §SD7) ──────────────────────
#
# `merge_if_clear()` never calls `read_checks()` itself — it is handed
# `pre_press` (what `apply()` read about the PR's head BEFORE the press's own
# commit, §SD5) and only judges the commit the press itself just made:
# stop-list -> route-lint -> touches-one-file, in that order, first hit wins,
# then the merge-method choice from `assignment.mjs` (§SD7). The three
# `tools/**` scripts are faked as tiny real `.mjs` files run by a real
# `node` — the same discipline this file already takes with `git`: a faked
# `node` binary would prove the fake works, not the wiring.

_REL = Path("projects/demo/slices.md")
_GREEN_PRE_PRESS = {"ok": True, "green": True, "reason": ""}


def _fake_tool(
    tree: Path, rel: str, *, stdout: str = "", stderr: str = "", exit_code: int = 0
) -> None:
    path = tree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ""
    if stdout:
        body += f"process.stdout.write({_json.dumps(stdout)});\n"
    if stderr:
        body += f"process.stderr.write({_json.dumps(stderr)});\n"
    body += f"process.exit({exit_code});\n"
    path.write_text(body, encoding="utf-8")


def _clear_stop_list(tree: Path) -> None:
    _fake_tool(tree, "tools/pr-guard/stop-list.mjs", stdout=_json.dumps({"hitPaths": []}))


def _clean_route_lint(tree: Path) -> None:
    _fake_tool(tree, "tools/route-lint/route-lint.mjs", exit_code=0)


def _assignment_says(tree: Path, verdict: str, method) -> None:
    _fake_tool(
        tree,
        "tools/pr-guard/assignment.mjs",
        stdout=_json.dumps({"verdict": verdict, "mergeMethod": method}),
    )


def _stub_gh_merge(tmp_path, monkeypatch, *, exit_code: int = 0) -> Path:
    """A `gh` on PATH whose `pr merge` records its argv and exits `exit_code`.

    The log is touched empty up front: a test asserting the merge leg
    short-circuited before ever calling `gh` needs to read "nothing was
    logged", not get a FileNotFoundError for the log never being created.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh-merge-argv.log"
    log.write_text("", encoding="utf-8")
    (bin_dir / "gh").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


@pytest.fixture
def merge_tree(tmp_path):
    """A real git repo whose HEAD is the "press's own commit" — two commits,
    the second touching only `projects/demo/slices.md`."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "projects" / "demo").mkdir(parents=True)
    (root / "projects" / "demo" / "slices.md").write_text("first\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    (root / "projects" / "demo" / "slices.md").write_text("second\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "press commit")
    return root


def test_merges_with_squash_when_assignment_says_one_id(
    merge_tree, tmp_path, monkeypatch
):
    _clear_stop_list(merge_tree)
    _clean_route_lint(merge_tree)
    _assignment_says(merge_tree, "ok", "squash")
    log = _stub_gh_merge(tmp_path, monkeypatch)
    out = transition.merge_if_clear(
        merge_tree, pr=42, rel=_REL, pre_press=_GREEN_PRE_PRESS
    )
    assert out == {"merged": True, "method": "squash", "reason": ""}
    assert "pr merge 42 --squash" in log.read_text(encoding="utf-8")


def test_merges_with_merge_commit_when_assignment_says_multi_id(
    merge_tree, tmp_path, monkeypatch
):
    """§SD7 — the card that carries >1 `Assignment:` id must never be
    squashed (PR #1120 · #1124 lost work permanently that way)."""
    _clear_stop_list(merge_tree)
    _clean_route_lint(merge_tree)
    _assignment_says(merge_tree, "multi-id", "merge-commit")
    log = _stub_gh_merge(tmp_path, monkeypatch)
    out = transition.merge_if_clear(
        merge_tree, pr=42, rel=_REL, pre_press=_GREEN_PRE_PRESS
    )
    assert out == {"merged": True, "method": "merge-commit", "reason": ""}
    assert "pr merge 42 --merge" in log.read_text(encoding="utf-8")


def test_no_open_pr_stops_before_anything_runs(merge_tree, tmp_path, monkeypatch):
    log = _stub_gh_merge(tmp_path, monkeypatch)
    out = transition.merge_if_clear(
        merge_tree, pr=0, rel=_REL, pre_press=_GREEN_PRE_PRESS
    )
    assert out["merged"] is False
    assert "ไม่มี PR" in out["reason"]
    assert log.read_text(encoding="utf-8") == ""


def test_a_head_that_was_not_green_before_the_press_stops_the_merge(
    merge_tree, tmp_path, monkeypatch
):
    """§SD5 — this function never re-derives the answer; it trusts what it
    was handed about the head that existed before this press's own commit."""
    log = _stub_gh_merge(tmp_path, monkeypatch)
    out = transition.merge_if_clear(
        merge_tree,
        pr=42,
        rel=_REL,
        pre_press={
            "ok": True,
            "green": False,
            "reason": "stop-list (does this need the owner?): FAILURE",
        },
    )
    assert out["merged"] is False
    assert out["reason"] == "stop-list (does this need the owner?): FAILURE"
    assert log.read_text(encoding="utf-8") == ""  # never reached `gh pr merge`


def test_a_stop_list_hit_on_the_press_own_commit_stops_the_merge(
    merge_tree, tmp_path, monkeypatch
):
    _fake_tool(
        merge_tree,
        "tools/pr-guard/stop-list.mjs",
        stdout=_json.dumps({"hitPaths": ["CLAUDE.md"]}),
    )
    log = _stub_gh_merge(tmp_path, monkeypatch)
    out = transition.merge_if_clear(
        merge_tree, pr=42, rel=_REL, pre_press=_GREEN_PRE_PRESS
    )
    assert out["merged"] is False
    assert "CLAUDE.md" in out["reason"]
    assert log.read_text(encoding="utf-8") == ""


def test_a_route_lint_failure_stops_the_merge(merge_tree, tmp_path, monkeypatch):
    _clear_stop_list(merge_tree)
    _fake_tool(
        merge_tree,
        "tools/route-lint/route-lint.mjs",
        stderr="route-lint FAIL — 1 broken link",
        exit_code=1,
    )
    log = _stub_gh_merge(tmp_path, monkeypatch)
    out = transition.merge_if_clear(
        merge_tree, pr=42, rel=_REL, pre_press=_GREEN_PRE_PRESS
    )
    assert out["merged"] is False
    assert "route-lint" in out["reason"]
    assert log.read_text(encoding="utf-8") == ""


def test_a_commit_touching_more_than_the_row_file_stops_the_merge(
    tmp_path, monkeypatch
):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "projects" / "demo").mkdir(parents=True)
    (root / "projects" / "demo" / "slices.md").write_text("first\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    (root / "projects" / "demo" / "slices.md").write_text("second\n", encoding="utf-8")
    (root / "stray.txt").write_text("oops\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "press commit touching two files")
    _clear_stop_list(root)
    _clean_route_lint(root)
    log = _stub_gh_merge(tmp_path, monkeypatch)
    out = transition.merge_if_clear(root, pr=42, rel=_REL, pre_press=_GREEN_PRE_PRESS)
    assert out["merged"] is False
    assert "stray.txt" in out["reason"]
    assert log.read_text(encoding="utf-8") == ""


def test_assignment_unable_to_pick_a_method_stops_the_merge(
    merge_tree, tmp_path, monkeypatch
):
    _clear_stop_list(merge_tree)
    _clean_route_lint(merge_tree)
    _fake_tool(
        merge_tree,
        "tools/pr-guard/assignment.mjs",
        stdout=_json.dumps(
            {
                "verdict": "malformed-commits",
                "mergeMethod": None,
                "reason": "commit 1 missing-trailer",
            }
        ),
    )
    log = _stub_gh_merge(tmp_path, monkeypatch)
    out = transition.merge_if_clear(
        merge_tree, pr=42, rel=_REL, pre_press=_GREEN_PRE_PRESS
    )
    assert out["merged"] is False
    assert "missing-trailer" in out["reason"]
    assert log.read_text(encoding="utf-8") == ""


def test_gh_pr_merge_failing_is_reported_with_the_method_it_tried(
    merge_tree, tmp_path, monkeypatch
):
    _clear_stop_list(merge_tree)
    _clean_route_lint(merge_tree)
    _assignment_says(merge_tree, "ok", "squash")
    log = _stub_gh_merge(tmp_path, monkeypatch, exit_code=1)
    out = transition.merge_if_clear(
        merge_tree, pr=42, rel=_REL, pre_press=_GREEN_PRE_PRESS
    )
    assert out["merged"] is False
    assert out["method"] == "squash"
    assert "pr merge 42 --squash" in log.read_text(encoding="utf-8")


# ── wired into `apply()` at `deployed → done` ───────────────────────────────


def _stub_gh_sixth_press(tmp_path, monkeypatch, *, view_body: str, merge_exit: int = 0) -> Path:
    """A `gh` stub covering every subcommand the 6th press uses: `pr list`
    (the PR already open on the card's branch), `pr view` (§SD5's pre-press
    read), `pr merge`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh-argv.log"
    log.write_text("", encoding="utf-8")
    (bin_dir / "gh").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {log}\n'
        'case "$1 $2" in\n'
        '  "pr list") printf %s \'[{"number":90,"url":"https://github.com/o/r/pull/90"}]\' ;;\n'
        f'  "pr view") printf %s {view_body!r} ;;\n'
        f'  "pr merge") exit {merge_exit} ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


def test_the_sixth_press_merges_end_to_end(remote, repo, tmp_path, monkeypatch):
    """`deployed → done`, criteria closed, every local gate clear — the one
    press that also merges (ADR-0044 §SD3), reading the PR's head before its
    own commit exists (§SD5) and choosing the method `assignment.mjs` names
    (§SD7)."""
    _clear_stop_list(repo)
    _clean_route_lint(repo)
    _assignment_says(repo, "ok", "squash")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed fake pr-guard tools for the test")

    body = {
        "state": "OPEN",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": _FOUR_GREEN,
    }
    log = _stub_gh_sixth_press(tmp_path, monkeypatch, view_body=_json.dumps(body))

    row = _row(stage="deployed", criteria={"done": 2, "total": 2})
    out = transition.apply(
        repo,
        project="demo",
        row=row,
        to_stage="done",
        actor_role="product-owner",
        office="build",
        client="internal",
    )
    assert out["ok"] is True, out["reason"]
    assert out["merge"] == {"merged": True, "method": "squash", "reason": ""}
    argv = log.read_text(encoding="utf-8")
    assert "view 90" in argv
    assert "merge 90 --squash" in argv


def test_the_sixth_press_stops_when_the_pre_press_head_is_not_green(
    remote, repo, tmp_path, monkeypatch
):
    """A card whose head was already red before this press writes and pushes
    its cell anyway (S44d) — the merge just does not happen, and it is not an
    error."""
    _clear_stop_list(repo)
    _clean_route_lint(repo)
    _assignment_says(repo, "ok", "squash")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed fake pr-guard tools for the test")

    rows = [dict(r) for r in _FOUR_GREEN]
    for r in rows:
        if r["name"] == "stop-list (does this need the owner?)":
            r["conclusion"] = "FAILURE"
    body = {"state": "OPEN", "mergeStateStatus": "UNSTABLE", "statusCheckRollup": rows}
    log = _stub_gh_sixth_press(tmp_path, monkeypatch, view_body=_json.dumps(body))

    row = _row(stage="deployed", criteria={"done": 2, "total": 2})
    out = transition.apply(
        repo,
        project="demo",
        row=row,
        to_stage="done",
        actor_role="product-owner",
        office="build",
        client="internal",
    )
    assert out["ok"] is True, out["reason"]  # the write still happened
    assert out["merge"]["merged"] is False
    assert "stop-list" in out["merge"]["reason"]
    assert "merge 90" not in log.read_text(encoding="utf-8")


def test_a_non_merge_station_press_never_touches_the_merge_leg(
    remote, repo, tmp_path, monkeypatch
):
    """`readydev → inprogress` is not `deployed → done` — `merge` comes back
    blank and no `gh pr merge` (or any `tools/**` script) is ever invoked."""
    log = _stub_gh(tmp_path, monkeypatch, created="https://github.com/o/r/pull/77")
    out = transition.apply(
        repo, project="demo", row=_row(), to_stage="inprogress", actor_role="developer"
    )
    assert out["ok"] is True
    assert out["merge"] == {"merged": False, "method": None, "reason": ""}
    assert "pr merge" not in log.read_text(encoding="utf-8")


def test_a_refused_merge_station_press_still_answers_the_merge_shape(repo):
    """The gate (criteria not closed) refuses before any of this runs — the
    caller still gets the same keys, never a KeyError."""
    out = transition.apply(
        repo,
        project="demo",
        row=_row(stage="deployed", criteria={"done": 0, "total": 2}),
        to_stage="done",
        actor_role="product-owner",
    )
    assert out["ok"] is False
    assert out["merge"] == {"merged": False, "method": None, "reason": ""}
