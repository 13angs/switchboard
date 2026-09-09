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

ROLES_WITH_OWNERSHIP = """# Roles

## โมเดลต่อ role — tier ที่แต่ละบทบาททำงานด้วย

| Role | default | ขึ้น **heavy** เมื่อ | ลง **light** เมื่อ |
| --- | :-: | --- | --- |
| **CTO** | **heavy** | — | — |
| **Senior Developer** | standard | — | — |
| **Developer** | standard | — | งานกลไก |
| **Product Owner** | standard | — | — |
| **QA** | standard | — | รันเคสที่ระบุไฟล์ |

## แกนความเป็นเจ้าของ — role → office · discipline · ที่บันทึก

| role | office | เปิด discipline leaf ใน `team/` เพื่ออ่านวิธีทำงาน | บันทึกผลลงที่ |
| --- | :-: | --- | --- |
| `cto` | `build` | `arch` · `security` | `meta/adr-*.md` |
| `senior-developer` | `build` | `dev` · `devex` | ADR |
| `developer` | `build` | `dev` | commit body |
| `product-owner` | `business` | `forge` · `client` | day file |
"""

ROLES_WITH_EFFORT = """# Roles

## โมเดลต่อ role — tier ที่แต่ละบทบาททำงานด้วย

| Role | default | effort | ขึ้น **heavy** เมื่อ |
| --- | :-: | :-: | --- |
| **CTO** | **heavy** | `xhigh` | — |
| **Developer** | standard | `medium` | — |
| **QA** | standard | garbage | — |
| **Support** | light | `medium` | — |
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
        {"role": "CTO", "tier": "heavy", "model": "claude-opus-5", "effort": None},
        {
            "role": "Developer",
            "tier": "standard",
            "model": "claude-sonnet-5",
            "effort": None,
        },
        {
            "role": "QA",
            "tier": "standard",
            "model": "claude-sonnet-5",
            "effort": None,
        },
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


# ── effort rides with the model (ADR-0032, S12) ─────────────────────────────


def test_effort_column_is_read_positionally(tmp_path):
    out = workspace.workspace_overview(
        str(_repo(tmp_path, roles=ROLES_WITH_EFFORT)), use_cache=False
    )
    roles = {r["role"]: r["effort"] for r in out["dispatch"]["roles"]}
    assert roles["CTO"] == "xhigh"
    assert roles["Developer"] == "medium"


def test_unknown_effort_value_is_dropped_not_guessed(tmp_path):
    """`garbage` in the effort column must not become a value the client can
    forward to the CLI — silence, not a guess (ADR-0032 §SD2)."""
    out = workspace.workspace_overview(
        str(_repo(tmp_path, roles=ROLES_WITH_EFFORT)), use_cache=False
    )
    roles = {r["role"]: r["effort"] for r in out["dispatch"]["roles"]}
    assert roles["QA"] is None


def test_light_tier_never_carries_effort(tmp_path):
    """Haiku rejects `--effort` outright (§SD6) — even a valid-looking cell
    next to a `light` row must not survive."""
    out = workspace.workspace_overview(
        str(_repo(tmp_path, roles=ROLES_WITH_EFFORT)), use_cache=False
    )
    roles = {r["role"]: r["effort"] for r in out["dispatch"]["roles"]}
    assert roles["Support"] is None


def test_table_without_the_effort_column_still_dispatches(tmp_path):
    """§SD5: an older 2/3-column roles.md must not break — it just carries no
    effort, same as before this ADR."""
    out = workspace.workspace_overview(str(_repo(tmp_path)), use_cache=False)
    assert all(r["effort"] is None for r in out["dispatch"]["roles"])


def test_model_tier_finds_the_declared_tier(tmp_path):
    root = str(_repo(tmp_path))
    assert workspace.model_tier(root, "claude-opus-5") == "heavy"
    assert workspace.model_tier(root, "claude-haiku-4-5") == "light"
    assert workspace.model_tier(root, "not-a-real-model") is None


def test_effort_reaches_the_claude_command_line(tmp_path):
    cmd = harness.build_command(
        "claude", None, str(tmp_path), "claude", "claude-opus-5", "xhigh"
    )
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_no_effort_means_no_flag(tmp_path):
    cmd = harness.build_command("claude", None, str(tmp_path), "claude")
    assert "--effort" not in cmd


def test_effort_precedes_resume_so_both_survive(tmp_path):
    cmd = harness.build_command(
        "claude", "sid-1", str(tmp_path), "claude", "claude-sonnet-5", "medium"
    )
    assert cmd[cmd.index("--effort") + 1] == "medium"
    assert cmd[cmd.index("--resume") + 1] == "sid-1"


def test_unverified_harness_refuses_effort_rather_than_dropping_it(tmp_path):
    for name in ("codex", "agy"):
        with pytest.raises(ValueError, match="effort pinning"):
            harness.build_command(
                name,
                None,
                str(tmp_path),
                "openai" if name == "codex" else "google",
                None,
                "high",
            )


# ── the dispatch box opens on the project's own role, not roles[0] (ADR-0033) ──


def _repo_with_team(tmp_path: Path, team: str) -> Path:
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    (proj / "slices.md").write_text(
        f"---\nclient: winona\nteam: {team}\n---\n\n"
        "| # | ชิ้น | วัน | สถานะ | ผล |\n"
        "| :-: | --- | --- | :-: | --- |\n"
        "| **M0** | ทำของ | จ. | ⬜ | ได้ของ |\n",
        encoding="utf-8",
    )
    sops = tmp_path / "docs" / "sops"
    sops.mkdir(parents=True)
    (sops / "sop-agent-orchestration.md").write_text(SOP, encoding="utf-8")
    people = tmp_path / "team-os" / "people"
    people.mkdir(parents=True)
    (people / "roles.md").write_text(ROLES_WITH_OWNERSHIP, encoding="utf-8")
    return tmp_path


def test_default_role_resolves_a_discipline_to_its_owning_role(tmp_path):
    """`team: forge` is a discipline, not a role — it must resolve through the
    ownership table rather than be compared to role names directly."""
    out = workspace.workspace_overview(
        str(_repo_with_team(tmp_path, "forge")), use_cache=False
    )
    assert out["projects"][0]["default_role"] == "Product Owner"


def test_default_role_reads_a_role_name_written_directly(tmp_path):
    """`team:` sometimes names a role outright (partner-offer's `team: product-owner`)."""
    out = workspace.workspace_overview(
        str(_repo_with_team(tmp_path, "product-owner")), use_cache=False
    )
    assert out["projects"][0]["default_role"] == "Product Owner"


def test_ambiguous_dev_discipline_ties_to_developer_not_senior(tmp_path):
    """`dev` is opened by both `senior-developer` and `developer` in the
    ownership table — the routine-work role wins by default (S-09's fix)."""
    out = workspace.workspace_overview(
        str(_repo_with_team(tmp_path, "dev")), use_cache=False
    )
    assert out["projects"][0]["default_role"] == "Developer"


def test_devex_is_unambiguous_and_goes_to_senior_developer(tmp_path):
    out = workspace.workspace_overview(
        str(_repo_with_team(tmp_path, "devex")), use_cache=False
    )
    assert out["projects"][0]["default_role"] == "Senior Developer"


def test_unrecognised_team_falls_back_to_no_default(tmp_path):
    """An unmatched `team:` resolves to nothing rather than a guess — the
    client keeps its own fallback (first role), same as before this fix."""
    out = workspace.workspace_overview(
        str(_repo_with_team(tmp_path, "nonexistent-team")), use_cache=False
    )
    assert out["projects"][0]["default_role"] is None


def test_default_role_is_none_when_dispatch_is_not_present(tmp_path):
    out = workspace.workspace_overview(
        str(_repo(tmp_path, roles=None)), use_cache=False
    )
    assert out["projects"][0]["default_role"] is None


# ── a row's own `role` column overrides the file default (ADR-0035, S14) ────


def _repo_with_row_roles(tmp_path: Path, *, team: str, row_role: str) -> Path:
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    (proj / "slices.md").write_text(
        f"---\nclient: winona\nteam: {team}\n---\n\n"
        "| # | ชิ้น | วัน | สถานะ | ผล | role |\n"
        "| :-: | --- | --- | :-: | --- | :-: |\n"
        "| **M0** | ทำของกลไก | จ. | ⬜ | ได้ของ | |\n"
        f"| **M1** | ตัดสินสถาปัตยกรรม | จ. | ⬜ | ตัดสินแล้ว | {row_role} |\n",
        encoding="utf-8",
    )
    sops = tmp_path / "docs" / "sops"
    sops.mkdir(parents=True)
    (sops / "sop-agent-orchestration.md").write_text(SOP, encoding="utf-8")
    people = tmp_path / "team-os" / "people"
    people.mkdir(parents=True)
    (people / "roles.md").write_text(ROLES_WITH_OWNERSHIP, encoding="utf-8")
    return tmp_path


def test_row_role_overrides_the_file_default_for_that_row_only(tmp_path):
    out = workspace.workspace_overview(
        str(_repo_with_row_roles(tmp_path, team="developer", row_role="qa")),
        use_cache=False,
    )
    slices = out["projects"][0]["slices"]
    assert out["projects"][0]["default_role"] == "Developer"
    assert slices[0]["role"] == "Developer"  # blank cell → file default
    assert slices[1]["role"] == "QA"  # row override, direct role name


def test_row_role_resolves_a_discipline_same_as_team_does(tmp_path):
    """The row cell gets the same latitude `team:` has — a discipline name,
    not only a bare role name (ADR-0035 §SD2: one resolver, not two)."""
    out = workspace.workspace_overview(
        str(_repo_with_row_roles(tmp_path, team="developer", row_role="forge")),
        use_cache=False,
    )
    assert out["projects"][0]["slices"][1]["role"] == "Product Owner"


def test_unresolved_row_role_falls_back_to_file_default_not_a_guess(tmp_path):
    out = workspace.workspace_overview(
        str(_repo_with_row_roles(tmp_path, team="developer", row_role="nonexistent")),
        use_cache=False,
    )
    assert out["projects"][0]["slices"][1]["role"] == "Developer"


def test_table_without_a_role_column_still_gets_the_file_default_per_row(tmp_path):
    """Backward compatibility: a file that never adopts the `role` column
    (every file before this ADR) keeps working exactly as before — every row
    just inherits `default_role`."""
    out = workspace.workspace_overview(
        str(_repo_with_team(tmp_path, "developer")), use_cache=False
    )
    slices = out["projects"][0]["slices"]
    assert slices and all(s["role"] == "Developer" for s in slices)


def test_slice_role_is_none_when_dispatch_is_not_present(tmp_path):
    out = workspace.workspace_overview(str(_repo(tmp_path, roles=None)), use_cache=False)
    slices = out["projects"][0]["slices"]
    assert slices and all(s["role"] is None for s in slices)


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

    def __init__(self, alive: bool = True, session_id_after_writes: int | None = None):
        self.written: list[bytes] = []
        self._alive = alive
        self.attach_key = None
        self.harness = "claude"
        self.session_id = None
        # S24: simulates `_start_id_capture`'s background poll discovering the
        # jsonl only after the Nth byte write actually lands the first turn.
        self._session_id_after_writes = session_id_after_writes

    def is_alive(self) -> bool:
        return self._alive

    def write(self, data: bytes) -> None:
        self.written.append(data)
        if self._session_id_after_writes is not None and (
            len(self.written) >= self._session_id_after_writes
        ):
            self.session_id = "session-discovered"

    def start_reader(self, *_a, **_kw) -> None:
        pass


@pytest.fixture()
def srv(monkeypatch):
    """server.py, with the PTY replaced and the TUI settle wait removed."""
    import os

    os.environ["ORCH_CLAUDE_BIN"] = "cat"
    import server  # noqa: PLC0415

    monkeypatch.setattr(server, "_PROMPT_SETTLE_S", 0)
    monkeypatch.setattr(server, "_SUBMIT_RETRY_INTERVAL_S", 0)
    monkeypatch.setattr(server, "_SUBMIT_RETRY_WINDOW_S", 0.5)
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


def test_resume_never_re_pins_effort(srv, monkeypatch, tmp_path):
    """Same rule as the model, for the same reason (ADR-0032 §SD3)."""
    seen = {}

    def fake_spawn(harness_name, **kwargs):
        seen.update(kwargs)
        return _FakeTerm()

    monkeypatch.setattr(srv.terminal, "spawn_harness", fake_spawn)
    monkeypatch.setattr(srv.lock, "external_holder", lambda *a, **kw: None)

    srv._get_or_spawn(None, str(tmp_path), effort="high")
    assert seen["effort"] == "high", "a fresh spawn must carry the effort"

    seen.clear()
    srv._get_or_spawn("existing-session", str(tmp_path), effort="high")
    assert seen["effort"] is None, "a resume must not re-pin the effort"


def test_prompt_is_typed_without_a_submit_key(srv):
    """`_type_prompt` never appends the submit key any more (S24) — bundling
    text + submit into one `term.write()` is exactly what made the harness's
    TUI read the whole thing as a bracketed paste instead of a keypress, so
    the trailing byte landed inside the pasted text rather than submitting
    it. Resume/prompt-less/manual dispatch relies on this: the board fills
    the box, a person presses Enter."""
    term = _FakeTerm()
    assert srv._type_prompt(term, "อ่าน slices.md แล้วทำ M2") is True
    payload = b"".join(term.written)
    assert payload == "อ่าน slices.md แล้วทำ M2".encode("utf-8")
    assert not payload.endswith(b"\n")
    assert not payload.endswith(b"\r")


def test_chat_payload_still_submits_so_the_contrast_is_pinned(srv):
    """`_message` must keep sending. If these two ever converge, one of the two
    behaviours has been broken silently."""
    assert srv._chat_message_payload("hi", "claude").endswith(b"\r")
    assert srv._chat_message_payload("hi", "codex").endswith(b"\r")


def test_claude_and_codex_submit_with_carriage_return_not_linefeed(srv):
    """Regression guard for the byte itself, not just "does it end in
    something". Both run raw-mode terminal UIs, which deliver Enter exactly
    as sent — `\\r`, what a real terminal transmits for Enter — with no
    ICRNL/INLCR translation. `claude` used `\\n` here until a live
    reproduction against the real binary (S24) proved it never submits:
    `_type_prompt`/`_submit_typed_prompt` could retry a wrong byte forever
    and never see a `session_id`. `agy` is a different CLI with no such
    evidence either way, so it keeps its prior default rather than guessing."""
    assert srv._chat_message_payload("x", "claude") == b"x\r"
    assert srv._chat_message_payload("x", "codex") == b"x\r"
    assert srv._chat_message_payload("x", "agy") == b"x\n"


def test_typing_into_a_dead_pty_reports_instead_of_raising(srv):
    """The session is already spawned by this point; a failed prompt is an empty
    input box, not a failed dispatch."""
    assert srv._type_prompt(_FakeTerm(alive=False), "x") is False


# ── S24 / ADR-0038 amendment: the submit key is its own write, retried on
# evidence (`term.session_id`), never bundled with the prompt text ──────────


def test_submit_key_is_its_own_write_not_bundled_with_the_prompt(srv):
    """The bug `S24` found: one `term.write()` carrying prompt text plus a
    trailing submit byte is read by the harness's TUI as a bracketed paste,
    so the byte becomes a literal newline inside the pasted text instead of
    submitting it. `_type_prompt` and `_submit_typed_prompt` must never be
    collapsed back into a single write."""
    term = _FakeTerm(session_id_after_writes=2)
    assert srv._type_prompt(term, "อ่าน slices.md แล้วทำ M2") is True
    assert srv._submit_typed_prompt(term) is True
    assert term.written == [
        "อ่าน slices.md แล้วทำ M2".encode("utf-8"),
        srv._chat_message_payload("", "claude"),
    ]


def test_submit_key_matches_harness(srv):
    """codex submits with \\r, same as chat (`_chat_message_payload`) — the
    dispatch path must not hardcode \\n regardless of harness."""
    term = _FakeTerm(session_id_after_writes=1)
    term.harness = "codex"
    srv._submit_typed_prompt(term)
    assert b"".join(term.written).endswith(b"\r")


def test_submit_retries_the_enter_key_until_a_session_id_appears(srv):
    """A single press can be lost (e.g. the TUI genuinely was not ready yet,
    `risks.md S-03`) — retrying only the submit key is safe because an extra
    Enter into an already-empty input box is a no-op. `_submit_typed_prompt`
    must keep pressing until `_start_id_capture`'s poll proves a first turn
    landed, not give up after one attempt."""
    term = _FakeTerm(session_id_after_writes=3)
    assert srv._submit_typed_prompt(term) is True
    assert len(term.written) == 3
    assert all(w == srv._chat_message_payload("", "claude") for w in term.written)


def test_submit_gives_up_after_the_retry_window_with_no_evidence(srv):
    """If the harness never actually accepts a first turn (e.g. it crashed
    before ever reading the box), retrying forever would keep firing Enter
    at a session that is never coming back — `prompt_submitted` must report
    False on real evidence, not swallow the failure by retrying past its
    budget."""
    term = _FakeTerm()  # session_id never appears
    assert srv._submit_typed_prompt(term) is False
    assert len(term.written) > 1, "must have retried, not given up after one press"


def test_submit_stops_retrying_once_the_pty_dies(srv):
    """A PTY that exits mid-retry must not be written to again."""

    class _DyingTerm(_FakeTerm):
        def write(self, data: bytes) -> None:
            super().write(data)
            if len(self.written) >= 2:
                self._alive = False

    term = _DyingTerm()
    assert srv._submit_typed_prompt(term) is False
    assert len(term.written) == 2
