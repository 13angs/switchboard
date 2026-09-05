#!/usr/bin/env python3
"""Tier dispatch — model pinning and the prompt that is typed but not sent (ADR-0030).

The cases here are the ones that fail *quietly* in production: a model flag that
silently does not reach argv (the session runs on whatever launched the server),
a resume that re-pins a tier mid-flight, and a prompt that submits itself because
someone reused the chat payload helper.

Run:
    pytest tests/test_dispatch.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from control_plane import harness, workspace  # noqa: E402


SOP = """# Agent orchestration

**Canonical tier → model map** (single source of truth): light = `claude-haiku-4-5`,
standard = `claude-sonnet-5`, heavy = `claude-opus-5` (ราคาต่อ tier → § Economics).
"""

ROLES = """# Roles

| อยากรู้ | เปิด |
| --- | --- |
| มีทีมอะไรบ้าง | `team/README.md` |

## โมเดลต่อ role — tier ที่แต่ละบทบาททำงานด้วย

| Role | default | ขึ้น **heavy** เมื่อ | ลง **light** เมื่อ |
| --- | :-: | --- | --- |
| **CTO** | **heavy** | — | — |
| **Developer** | standard | — | งานกลไก |
| **QA** | standard | — | รันเคสที่ระบุไฟล์ |

## ภาคผนวก — tier ต่อ 14 ทีมของ workspace

| tier | ทีม |
| --- | --- |
| **heavy** | `arch` · `forge` |
"""


def _repo(tmp_path: Path, *, sop: str | None = SOP, roles: str | None = ROLES) -> Path:
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    (proj / "slices.md").write_text(
        "---\nclient: winona\nteam: forge\n---\n\n"
        "| # | ชิ้น | วัน | สถานะ | ผล |\n"
        "| :-: | --- | --- | :-: | --- |\n"
        "| **M0** | ทำของ | จ. | ⬜ | ได้ของ |\n",
        encoding="utf-8",
    )
    if sop is not None:
        sops = tmp_path / "docs" / "sops"
        sops.mkdir(parents=True)
        (sops / "sop-agent-orchestration.md").write_text(sop, encoding="utf-8")
    if roles is not None:
        people = tmp_path / "team-os" / "people"
        people.mkdir(parents=True)
        (people / "roles.md").write_text(roles, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_cache():
    workspace.invalidate_cache()
    yield
    workspace.invalidate_cache()


# ── the map comes from the workspace, not from here ─────────────────────────


def test_reads_tier_map_and_roles_from_the_workspace(tmp_path):
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    d = out["dispatch"]
    assert d["present"] is True
    assert d["tiers"] == {
        "light": "claude-haiku-4-5",
        "standard": "claude-sonnet-5",
        "heavy": "claude-opus-5",
    }
    assert d["roles"] == [
        {"role": "CTO", "tier": "heavy", "model": "claude-opus-5"},
        {"role": "Developer", "tier": "standard", "model": "claude-sonnet-5"},
        {"role": "QA", "tier": "standard", "model": "claude-sonnet-5"},
    ]


def test_appendix_table_is_not_read_as_roles(tmp_path):
    """The 14-team appendix is a different axis and must not become roles."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    assert [r["role"] for r in out["dispatch"]["roles"]] == ["CTO", "Developer", "QA"]


def test_partial_tier_lineup_turns_dispatch_off(tmp_path):
    """Two tiers out of three would let one role resolve while its neighbour
    silently does not — harder to notice than a clean off."""
    half = "light = `claude-haiku-4-5`, standard = `claude-sonnet-5`\n"
    out = workspace.workspace_overview(str(_repo(tmp_path, sop=half)), use_cache=False)
    assert out["dispatch"]["present"] is False
    assert "tier" in out["dispatch"]["reason"]


def test_missing_roles_file_turns_dispatch_off_rather_than_guessing(tmp_path):
    out = workspace.workspace_overview(
        str(_repo(tmp_path, roles=None)), use_cache=False
    )
    assert out["dispatch"]["present"] is False


def test_allowed_models_is_empty_when_the_map_cannot_be_read(tmp_path):
    """An empty set makes the spawn path refuse — never fall back to a default,
    because an unpinned session inherits the server's own model."""
    assert workspace.allowed_models(str(_repo(tmp_path / "no-map", sop=None))) == set()
    assert workspace.allowed_models(str(_repo(tmp_path / "with-map"))) == {
        "claude-haiku-4-5",
        "claude-sonnet-5",
        "claude-opus-5",
    }


def test_project_carries_its_client_and_team(tmp_path):
    """The dispatch prompt needs an owner id; these are where it comes from."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    assert out["projects"][0]["client"] == "winona"
    assert out["projects"][0]["team"] == "forge"


# ── the flag actually reaches argv ──────────────────────────────────────────


def test_model_reaches_the_claude_command_line(tmp_path):
    cmd = harness.build_command(
        "claude", None, str(tmp_path), "claude", "claude-opus-5"
    )
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"


def test_no_model_means_no_flag(tmp_path):
    cmd = harness.build_command("claude", None, str(tmp_path), "claude")
    assert "--model" not in cmd


def test_model_precedes_resume_so_both_survive(tmp_path):
    cmd = harness.build_command(
        "claude", "sid-1", str(tmp_path), "claude", "claude-sonnet-5"
    )
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
    assert cmd[cmd.index("--resume") + 1] == "sid-1"


def test_unverified_harness_refuses_model_rather_than_dropping_it(tmp_path):
    """Silently ignoring the flag would run the work on the wrong tier and say
    nothing; guessing another CLI's flag fails inside a PTY as a blank screen."""
    for name in ("codex", "agy"):
        with pytest.raises(ValueError, match="model pinning"):
            harness.build_command(
                name,
                None,
                str(tmp_path),
                "openai" if name == "codex" else "google",
                "claude-opus-5",
            )


def test_unverified_harness_still_builds_without_a_model(tmp_path):
    assert harness.build_command("codex", None, str(tmp_path), "openai")
    assert harness.build_command("agy", None, str(tmp_path), "google")


# ── the spawn path: what the flag does, and what the prompt deliberately does not ──


class _FakeTerm:
    """Stands in for a PTY. Records what was written; never appends anything."""

    def __init__(self, alive: bool = True):
        self.written: list[bytes] = []
        self._alive = alive
        self.attach_key = None
        self.harness = "claude"
        self.session_id = None

    def is_alive(self) -> bool:
        return self._alive

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def start_reader(self, *_a, **_kw) -> None:
        pass


@pytest.fixture()
def srv(monkeypatch):
    """server.py, with the PTY replaced and the TUI settle wait removed."""
    import os

    os.environ["ORCH_CLAUDE_BIN"] = "cat"
    import server  # noqa: PLC0415

    monkeypatch.setattr(server, "_PROMPT_SETTLE_S", 0)
    server._registry.clear()
    yield server
    server._registry.clear()


def test_resume_never_re_pins_the_model(srv, monkeypatch, tmp_path):
    """A resume re-enters work already in flight. Silently moving it to another
    tier mid-task is the failure this guards — the caller may still pass a model
    (the UI has one in hand); the spawn path is what must drop it."""
    seen = {}

    def fake_spawn(harness_name, **kwargs):
        seen.update(kwargs)
        return _FakeTerm()

    monkeypatch.setattr(srv.terminal, "spawn_harness", fake_spawn)
    monkeypatch.setattr(srv.lock, "external_holder", lambda *a, **kw: None)

    srv._get_or_spawn(None, str(tmp_path), model="claude-opus-5")
    assert seen["model"] == "claude-opus-5", "a fresh spawn must carry the tier"

    seen.clear()
    srv._get_or_spawn("existing-session", str(tmp_path), model="claude-opus-5")
    assert seen["model"] is None, "a resume must not re-pin the tier"


def test_prompt_is_typed_without_the_submit_key(srv):
    """The whole point of the feature: the board fills the box, a person presses
    Enter. One newline here would turn preparation into execution."""
    term = _FakeTerm()
    assert srv._type_prompt(term, "อ่าน slices.md แล้วทำ M2") is True
    payload = b"".join(term.written)
    assert payload == "อ่าน slices.md แล้วทำ M2".encode("utf-8")
    assert not payload.endswith(b"\n")
    assert not payload.endswith(b"\r")


def test_chat_payload_still_submits_so_the_contrast_is_pinned(srv):
    """`_message` must keep sending. If these two ever converge, one of the two
    behaviours has been broken silently."""
    assert srv._chat_message_payload("hi", "claude").endswith(b"\n")
    assert srv._chat_message_payload("hi", "codex").endswith(b"\r")


def test_typing_into_a_dead_pty_reports_instead_of_raising(srv):
    """The session is already spawned by this point; a failed prompt is an empty
    input box, not a failed dispatch."""
    assert srv._type_prompt(_FakeTerm(alive=False), "x") is False
