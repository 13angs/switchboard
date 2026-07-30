#!/usr/bin/env python3
"""ADR-0017 + ADR-0025: GET /session/<id>/timeline — tool-call extraction.

Contract under test:
  - _build_timeline() pairs tool_use -> tool_result from read_messages_rich()
  - duration_ms is a number only when BOTH ends parse (ADR-0025 §SD1)
  - duration_state names *why* a duration is missing (ADR-0025 §SD2):
      measured    -> duration_ms is an int
      pending     -> no paired tool_result yet
      unsupported -> this harness's ts does not parse at all
  - entries keep store order; there is no sort (ADR-0025 §SD1)
  - _tool_category() splits Bash out of FILE_TOOLS' 'edit' bucket (ADR-0017 §SD3)

The `unsupported` test deliberately runs the **real** agy_store._parse_ts over
the **real** "step-NNNN" key that agy_store emits, because that is the whole
point of ADR-0025: a stub parser that returns None on demand would prove only
that the code agrees with itself. ADR-0014 and ADR-0016 both shipped green and
inert exactly that way (see ADR-0022 / ADR-0023).

Run:
    python3 -m pytest projects/switchboard/repos/switchboard/tests/test_timeline.py -v
"""

from __future__ import annotations

import http.client
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import server  # noqa: E402
from control_plane import agy_store, claude_store  # noqa: E402

_ORCH = Path(__file__).resolve().parents[1]
_PORT = 8809

# Real claude session store — same resolution rule as test_transcript_contract.py
# (config.SESSION_ROOT is clobbered by sibling tests at import time).
SESSION_ROOT = Path(
    os.environ.get(
        "SWITCHBOARD_CONTRACT_SESSION_ROOT",
        os.path.expanduser("~/.claude/projects"),
    )
)


# --- Helpers ----------------------------------------------------------------


def _stub_store(messages: list[dict], parse_ts):
    """A store module stand-in: fixture messages, a REAL parse bar."""
    return types.SimpleNamespace(
        read_messages_rich=lambda path, **kw: messages,
        _parse_ts=parse_ts,
    )


def _use(tool_id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _result(tool_id: str, content) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_id, "content": content}


# --- Pairing + duration -----------------------------------------------------


def test_paired_call_over_parseable_ts_is_measured():
    messages = [
        {
            "role": "assistant",
            "ts": "2026-07-24T10:00:00.000Z",
            "content": [_use("t1", "Read", {"file_path": "/workspaces/CLAUDE.md"})],
        },
        {
            "role": "user",
            "ts": "2026-07-24T10:00:00.234Z",
            "content": [_result("t1", "1\t# claudeMd")],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    entries = server._build_timeline(store, Path("/dev/null"))

    assert len(entries) == 1
    e = entries[0]
    assert e["tool"] == "Read"
    assert e["category"] == "read"
    assert e["duration_ms"] == 234
    assert e["duration_state"] == "measured"
    assert e["result_summary"] == "1\t# claudeMd"
    assert e["result_ts"] == "2026-07-24T10:00:00.234Z"


def test_unpaired_call_is_pending_not_unsupported():
    """A tool still running is not the same failure as a harness with no clock."""
    messages = [
        {
            "role": "assistant",
            "ts": "2026-07-24T10:00:00.000Z",
            "content": [_use("t1", "Bash", {"command": "sleep 999"})],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    entries = server._build_timeline(store, Path("/dev/null"))

    assert entries[0]["duration_ms"] is None
    assert entries[0]["duration_state"] == "pending"
    assert entries[0]["result_summary"] is None


def test_agy_step_key_reads_as_unsupported_not_pending():
    """ADR-0025's anti-inert bar, run against the real agy parse path.

    agy_store emits ts = f"step-{idx:04d}" (agy_store.py:500, :557). The pair is
    complete here — only the clock is missing — so a design that reports this as
    "pending" would be telling the panel to wait for a result that already
    arrived.
    """
    assert agy_store._parse_ts("step-0004") is None, (
        "premise moved: agy step keys now parse, so this test no longer covers "
        "the unsupported path — re-derive ADR-0025 §SD1 before deleting it"
    )

    messages = [
        {
            "role": "assistant",
            "ts": "step-0004",
            "content": [_use("t1", "Read", {"file_path": "notes.md"})],
        },
        {
            "role": "assistant",
            "ts": "step-0005",
            "content": [_result("t1", "file body")],
        },
    ]
    store = _stub_store(messages, agy_store._parse_ts)

    entries = server._build_timeline(store, Path("/dev/null"))

    assert entries[0]["duration_ms"] is None
    assert entries[0]["duration_state"] == "unsupported"
    # The pair still resolved — only the timing is absent (ADR-0025 § Rejected:
    # a timeline_supported flag). Args, result and order all survive.
    assert entries[0]["result_summary"] == "file body"
    assert entries[0]["result_ts"] == "step-0005"


def test_unsupported_is_decided_per_entry_not_per_harness():
    """A store that gains timestamps starts reporting measured with no code
    change here (ADR-0025 §SD2)."""
    messages = [
        {
            "role": "assistant",
            "ts": "step-0001",
            "content": [_use("a", "Read", {"file_path": "x"})],
        },
        {"role": "assistant", "ts": "step-0002", "content": [_result("a", "x body")]},
        {
            "role": "assistant",
            "ts": "2026-07-24T10:00:00.000Z",
            "content": [_use("b", "Read", {"file_path": "y"})],
        },
        {
            "role": "user",
            "ts": "2026-07-24T10:00:01.000Z",
            "content": [_result("b", "y body")],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    entries = server._build_timeline(store, Path("/dev/null"))

    assert [e["duration_state"] for e in entries] == ["unsupported", "measured"]
    assert entries[1]["duration_ms"] == 1000


# --- Ordering ---------------------------------------------------------------


def test_entries_keep_store_order_and_are_not_sorted():
    """ADR-0025 §SD1 drops entries.sort(key=lambda e: e.get('ts') or '').

    Store order is already chronological. Lexicographic sorting was only ever
    correct by two accidents: a uniform 'Z' suffix, and agy zero-padding to four
    digits. Mixed offsets break the first; a 10,000-step session breaks the
    second. This fixture is the mixed-offset case.
    """
    messages = [
        {
            "role": "assistant",
            "ts": "2026-07-24T17:00:00+07:00",  # 10:00Z — earlier
            "content": [_use("t1", "Read", {"file_path": "first.md"})],
        },
        {
            "role": "assistant",
            "ts": "2026-07-24T11:00:00Z",  # later, but sorts first as a string
            "content": [_use("t2", "Read", {"file_path": "second.md"})],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    entries = server._build_timeline(store, Path("/dev/null"))

    assert [e["args_summary"] for e in entries] == ["first.md", "second.md"]


# --- Categories -------------------------------------------------------------


def test_tool_category_separates_bash_from_edit():
    """FILE_TOOLS maps Bash -> 'edit' (it can write files). The timeline filter
    chips need it separate (ADR-0017 §SD3 filter override)."""
    assert server._tool_category("Bash") == "bash"
    assert server._tool_category("exec_command") == "bash"


def test_tool_category_follows_file_tools_otherwise():
    assert server._tool_category("Read") == "read"
    assert server._tool_category("Write") == "write"
    assert server._tool_category("Edit") == "edit"
    assert server._tool_category("NotebookEdit") == "edit"
    assert server._tool_category("WebFetch") == "other"


def test_tool_category_map_is_not_a_fourth_copy():
    """ADR-0017 §SD2: import FILE_TOOLS from analytics, do not re-declare it."""
    from control_plane.analytics import FILE_TOOLS

    for name, cat in FILE_TOOLS.items():
        if name in ("Bash", "exec_command"):
            continue
        assert server._tool_category(name) == cat


# --- Summaries --------------------------------------------------------------


def test_args_summary_prefers_the_identifying_argument():
    assert server._args_summary({"file_path": "/a/b.md", "offset": 3}) == "/a/b.md"
    assert server._args_summary({"command": "ls -la", "timeout": 5}) == "ls -la"
    assert server._args_summary({}) == ""


def test_args_summary_is_a_single_line():
    summary = server._args_summary({"command": "echo one\necho two"})
    assert "\n" not in summary


def test_result_summary_truncates_and_joins_block_lists():
    long_text = "x" * 500
    messages = [
        {
            "role": "assistant",
            "ts": "2026-07-24T10:00:00Z",
            "content": [_use("t1", "Read", {"file_path": "big.md"})],
        },
        {
            "role": "user",
            "ts": "2026-07-24T10:00:01Z",
            "content": [
                _result("t1", [{"type": "text", "text": long_text}]),
            ],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    entries = server._build_timeline(store, Path("/dev/null"))

    assert len(entries[0]["result_summary"]) == 200


def test_non_tool_blocks_are_ignored():
    messages = [
        {
            "role": "assistant",
            "ts": "2026-07-24T10:00:00Z",
            "content": [
                {"type": "text", "text": "thinking out loud"},
                {"type": "thinking", "thinking": "hmm"},
            ],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    assert server._build_timeline(store, Path("/dev/null")) == []


def test_id_less_calls_do_not_pair_with_each_other():
    """Two calls with no id must not collide on a shared '' key.

    ADR-0017's sketch keys `pending` on `block.get("id", "")`, so a transcript
    with id-less tool_use blocks would let one call's result land on another
    call's row — a wrong duration attached to the wrong tool, which is the
    ADR-0022 failure shape (a plausible figure beats a missing one only for
    whoever ships it).
    """
    messages = [
        {
            "role": "assistant",
            "ts": "2026-07-24T10:00:00Z",
            "content": [{"type": "tool_use", "name": "Read", "input": {"path": "a"}}],
        },
        {
            "role": "assistant",
            "ts": "2026-07-24T10:00:05Z",
            "content": [{"type": "tool_use", "name": "Read", "input": {"path": "b"}}],
        },
        {
            "role": "user",
            "ts": "2026-07-24T10:00:09Z",
            "content": [_result("", "some body")],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    entries = server._build_timeline(store, Path("/dev/null"))

    assert len(entries) == 2
    assert all(e["duration_state"] == "pending" for e in entries)
    assert all(e["duration_ms"] is None for e in entries)
    assert all(e["result_summary"] is None for e in entries)


def test_a_repeated_result_cannot_overwrite_a_timed_entry():
    messages = [
        {
            "role": "assistant",
            "ts": "2026-07-24T10:00:00Z",
            "content": [_use("t1", "Read", {"file_path": "a.md"})],
        },
        {
            "role": "user",
            "ts": "2026-07-24T10:00:01Z",
            "content": [_result("t1", "first")],
        },
        {
            "role": "user",
            "ts": "2026-07-24T10:05:00Z",
            "content": [_result("t1", "a late duplicate")],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    entries = server._build_timeline(store, Path("/dev/null"))

    assert len(entries) == 1
    assert entries[0]["duration_ms"] == 1000
    assert entries[0]["result_summary"] == "first"


def test_orphan_tool_result_does_not_crash_or_emit():
    """A `since=` window can slice a transcript between a call and its result."""
    messages = [
        {
            "role": "user",
            "ts": "2026-07-24T10:00:01Z",
            "content": [_result("gone", "orphan body")],
        },
    ]
    store = _stub_store(messages, claude_store._parse_ts)

    assert server._build_timeline(store, Path("/dev/null")) == []


# --- Route ------------------------------------------------------------------


def _serve(static_dir: str) -> subprocess.Popen:
    env = dict(
        os.environ,
        ORCH_STATIC_DIR=static_dir,
        ORCH_DEFAULT_CWD="/tmp",
        ORCH_ENV_FILE="/nonexistent",
    )
    return subprocess.Popen(
        [sys.executable, "server.py", "--port", str(_PORT), "--repo", "/tmp"],
        cwd=str(_ORCH),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _get(path: str):
    conn = http.client.HTTPConnection("127.0.0.1", _PORT, timeout=5)
    conn.request("GET", path)
    return conn.getresponse()


def test_timeline_route_is_registered_and_404s_an_unknown_session():
    with tempfile.TemporaryDirectory() as static_dir:
        (Path(static_dir) / "index.html").write_text("board")
        srv = _serve(static_dir)
        try:
            time.sleep(1.3)
            resp = _get("/session/no-such-session/timeline")
            body = resp.read().decode()
            # 404 with the not-found *message* proves the route matched; an
            # unregistered path would 404 with {"error": "not found"}.
            assert resp.status == 404
            assert "no-such-session" in body
        finally:
            srv.terminate()
            srv.wait(timeout=5)


# --- Contract: real transcripts ---------------------------------------------


def _biggest_claude_transcript() -> Path | None:
    if not SESSION_ROOT.is_dir():
        return None
    files = [p for p in SESSION_ROOT.glob("*/*.jsonl") if p.stat().st_size > 4096]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_size)


def test_real_claude_transcript_yields_measured_durations():
    """The anti-inert bar: prove the endpoint is not green-but-empty in prod.

    Skips where there is no store (CI, fresh checkout) — the point is that a
    local run fails loudly when claude's transcript shape drifts.
    """
    path = _biggest_claude_transcript()
    if path is None:
        pytest.skip(f"no claude session store at {SESSION_ROOT}")

    entries = server._build_timeline(claude_store, path)

    assert entries, f"no tool calls extracted from {path}"
    measured = [e for e in entries if e["duration_state"] == "measured"]
    assert measured, "every entry lost its duration against a real transcript"
    assert all(isinstance(e["duration_ms"], int) for e in measured)
    assert all(e["duration_ms"] >= 0 for e in measured)
    assert all(
        e["duration_state"] in ("measured", "pending", "unsupported") for e in entries
    )
