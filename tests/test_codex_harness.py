#!/usr/bin/env python3
"""Codex harness adapter checks.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_codex_harness.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

_TMP = tempfile.mkdtemp(prefix="orch-codex-")
CLAUDE_ROOT = Path(_TMP) / "claude-projects"
CODEX_HOME_DIR = Path(_TMP) / "codex-home"

# Script-mode / solo-import path (under pytest the authority is _isolated_env).
os.environ["ORCH_SESSION_ROOT"] = str(CLAUDE_ROOT)
os.environ["CODEX_HOME"] = str(CODEX_HOME_DIR)
os.environ["ORCH_CLAUDE_BIN"] = "claude-test-bin"
os.environ["ORCH_CODEX_BIN"] = "codex-test-bin"

import pytest  # noqa: E402

from control_plane import codex_store, config, discovery, harness  # noqa: E402
from control_plane import claude_store  # noqa: E402
import server as srv_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Re-pin env + import-frozen store roots per test.

    pytest imports every test module before running any test, so the LAST
    module-level os.environ write wins the whole run — and config /
    claude_store / codex_store freeze their roots at first import (with the
    real env). Without this fixture the file passes solo but fails in the
    full suite.
    """
    monkeypatch.setenv("ORCH_SESSION_ROOT", str(CLAUDE_ROOT))
    monkeypatch.setenv("CODEX_HOME", str(CODEX_HOME_DIR))
    monkeypatch.setenv("ORCH_CLAUDE_BIN", "claude-test-bin")
    monkeypatch.setenv("ORCH_CODEX_BIN", "codex-test-bin")
    monkeypatch.setattr(claude_store, "PROJECTS_DIR", CLAUDE_ROOT)
    monkeypatch.setattr(codex_store, "CODEX_HOME", CODEX_HOME_DIR)
    monkeypatch.setattr(codex_store, "SESSIONS_DIR", CODEX_HOME_DIR / "sessions")
    discovery.invalidate_cache()  # ADR-0010 — stale cache betrays isolation


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n",
        encoding="utf-8",
    )


def _sample_codex_session(
    session_id: str = "019f6489-3463-73c1-9808-2312d18b7564",
    cwd: str = "/workspaces/my-projects/.claude/worktrees/task/codex",
) -> Path:
    path = (
        Path(os.environ["CODEX_HOME"])
        / "sessions"
        / "2026"
        / "07"
        / "15"
        / f"rollout-2026-07-15T06-49-01-{session_id}.jsonl"
    )
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-07-15T06:49:37.368Z",
                "type": "session_meta",
                "payload": {
                    "session_id": session_id,
                    "id": session_id,
                    "cwd": cwd,
                    "model_provider": "openai",
                    "cli_version": "0.143.0",
                },
            },
            {
                "timestamp": "2026-07-15T06:49:38.000Z",
                "type": "turn_context",
                "payload": {
                    "cwd": cwd,
                    "model": "gpt-5",
                    "approval_policy": "on-request",
                    "sandbox_policy": "workspace-write",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:01.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "implement Codex harness adapter",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:03.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": "Codex adapter parsed session_meta successfully.",
                },
            },
        ],
    )
    return path


def _sample_claude_session(
    session_id: str = "claude-resume-1",
    cwd: str = "/tmp/orch-outside-repo",
    later_cwd: str | None = None,
) -> Path:
    path = (
        Path(os.environ["ORCH_SESSION_ROOT"])
        / claude_store.encode_cwd(cwd)
        / f"{session_id}.jsonl"
    )
    rows = [
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": cwd,
            "timestamp": "2026-07-15T07:00:00.000Z",
            "message": {"content": "resume me from my original cwd"},
        }
    ]
    if later_cwd:
        rows.append(
            {
                "type": "assistant",
                "sessionId": session_id,
                "cwd": later_cwd,
                "timestamp": "2026-07-15T07:01:00.000Z",
                "message": {
                    "content": "This turn ran after a tool changed directories."
                },
            }
        )
    _write_jsonl(path, rows)
    return path


def test_harness_registry_builds_claude_and_codex_commands():
    launchers = harness.available_launchers({})
    assert {"harness": "claude", "providers": ["claude"]} in launchers
    assert {"harness": "codex", "providers": ["openai"]} in launchers

    assert harness.resolve("deepseek", None) == ("claude", "deepseek")
    assert harness.resolve("ollama", None) == ("claude", "ollama")
    assert harness.resolve("openai", "codex") == ("codex", "openai")

    assert harness.build_command("claude", "sid-1", "/repo", "claude") == [
        "claude-test-bin",
        "--resume",
        "sid-1",
    ]
    assert harness.build_command("claude", "sid-ollama", "/repo", "ollama") == [
        "claude-test-bin",
        "--resume",
        "sid-ollama",
    ]
    assert harness.build_command("codex", None, "/repo", "openai") == [
        "codex-test-bin",
        "--no-alt-screen",
        "-C",
        "/repo",
    ]
    assert harness.build_command("codex", "sid-2", "/repo", "openai") == [
        "codex-test-bin",
        "--no-alt-screen",
        "-C",
        "/repo",
        "resume",
        "sid-2",
    ]


def test_chat_message_payload_uses_terminal_submit_key_per_harness():
    assert srv_mod._chat_message_payload("hello", "claude") == b"hello\n"
    assert srv_mod._chat_message_payload("hello", "codex") == b"hello\r"


def test_codex_store_parses_summary_and_messages():
    path = _sample_codex_session()
    summary = codex_store.read_session(path)

    assert summary.session_id == "019f6489-3463-73c1-9808-2312d18b7564"
    assert summary.harness == "codex"
    assert summary.provider == "openai"
    assert summary.cwd.endswith("/task/codex")
    assert summary.version == "0.143.0"
    assert summary.title == "implement Codex harness adapter"
    assert summary.last_role == "assistant"
    assert summary.turn_count == 2

    messages = codex_store.read_messages(path)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "implement Codex harness adapter"
    assert "parsed session_meta" in messages[1]["text"]


def test_codex_store_parses_response_item_assistant_messages():
    path = _sample_codex_session(session_id="codex-response-item-1")
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-07-15T06:49:37.368Z",
                "type": "session_meta",
                "payload": {
                    "session_id": "codex-response-item-1",
                    "cwd": "/workspaces/my-projects/.claude/worktrees/task/codex",
                    "model_provider": "openai",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:01.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "ผมเทสเสร็จแล้ว",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:04.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "รับทราบครับ",
                        }
                    ],
                },
            },
        ],
    )

    summary = codex_store.read_session(path)
    messages = codex_store.read_messages(path)

    assert summary.last_role == "assistant"
    assert summary.last_blurb == "รับทราบครับ"
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["text"] == "รับทราบครับ"


def test_codex_store_dedupes_event_msg_and_response_item_assistant_text():
    path = _sample_codex_session(session_id="codex-response-item-dupe-1")
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-07-15T06:49:37.368Z",
                "type": "session_meta",
                "payload": {
                    "session_id": "codex-response-item-dupe-1",
                    "cwd": "/workspaces/my-projects/.claude/worktrees/task/codex",
                    "model_provider": "openai",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:01.000Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello"},
            },
            {
                "timestamp": "2026-07-15T06:50:03.000Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "same answer"},
            },
            {
                "timestamp": "2026-07-15T06:50:04.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same answer"}],
                },
            },
        ],
    )

    messages = codex_store.read_messages(path)

    assert [m["text"] for m in messages] == ["hello", "same answer"]


def test_codex_store_keeps_repeated_assistant_text_across_turns():
    path = _sample_codex_session(session_id="codex-repeated-assistant-1")
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-07-15T06:49:37.368Z",
                "type": "session_meta",
                "payload": {
                    "session_id": "codex-repeated-assistant-1",
                    "cwd": "/workspaces/my-projects/.claude/worktrees/task/codex",
                    "model_provider": "openai",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:01.000Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "first"},
            },
            {
                "timestamp": "2026-07-15T06:50:03.000Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "OK"},
            },
            {
                "timestamp": "2026-07-15T06:51:01.000Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "second"},
            },
            {
                "timestamp": "2026-07-15T06:51:03.000Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "OK"},
            },
        ],
    )

    messages = codex_store.read_messages(path)

    assert [m["text"] for m in messages] == ["first", "OK", "second", "OK"]


def test_codex_store_discovers_repo_sessions_and_id_capture():
    cwd = "/workspaces/my-projects/.claude/worktrees/task/codex"
    _sample_codex_session(cwd=cwd)

    sessions = codex_store.all_sessions_for_repo("/workspaces/my-projects")
    assert any(
        s.session_id == "019f6489-3463-73c1-9808-2312d18b7564" and s.harness == "codex"
        for s in sessions
    )

    before = codex_store.existing_session_ids_for_cwd(cwd)
    assert "019f6489-3463-73c1-9808-2312d18b7564" in before
    assert codex_store.newest_session_id_for_cwd(cwd) in before


def test_resume_runtime_falls_back_to_claude_session_cwd_by_id():
    _sample_claude_session()

    h, provider, cwd = srv_mod._resolve_session_runtime(
        "claude-resume-1",
        "/workspaces/my-projects",
        {"harness": ["claude"], "provider": ["claude"]},
    )

    assert h == "claude"
    assert provider == "claude"
    assert cwd == "/tmp/orch-outside-repo"


def test_resume_runtime_uses_original_claude_cwd_not_later_event_cwd():
    _sample_claude_session(
        session_id="claude-resume-moved-cwd",
        cwd="/workspaces/my-projects",
        later_cwd="/workspaces/my-projects/projects/ai-chatbot/repo",
    )

    _h, _provider, cwd = srv_mod._resolve_session_runtime(
        "claude-resume-moved-cwd",
        "/workspaces/my-projects",
        {"harness": ["claude"], "provider": ["claude"]},
    )

    assert cwd == "/workspaces/my-projects"


def test_transcript_source_falls_back_to_claude_store_by_id():
    path = _sample_claude_session(session_id="claude-transcript-1")

    h, found = srv_mod._transcript_source(
        "claude-transcript-1",
        "/workspaces/my-projects",
    )

    assert h == "claude"
    assert found == path


def test_state_exposes_launchers_and_legacy_providers():
    state = srv_mod.build_state(str(REPO))
    assert state["providers"] == config.available_providers(srv_mod._ENV_FILE)
    assert {"harness": "codex", "providers": ["openai"]} in state["launchers"]


def test_codex_store_parses_function_call_and_output_as_rich_blocks():
    """_rich_turn_from_event must extract tool_use from function_call and
    tool_result from function_call_output."""
    path = _sample_codex_session(session_id="codex-fn-call-1")
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-07-15T06:49:37.368Z",
                "type": "session_meta",
                "payload": {
                    "session_id": "codex-fn-call-1",
                    "cwd": "/workspaces/my-projects/.claude/worktrees/task/codex",
                    "model_provider": "openai",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:01.000Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "read a file"},
            },
            {
                "timestamp": "2026-07-15T06:50:02.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "id": "fc_abc123",
                    "name": "exec_command",
                    "arguments": json.dumps(
                        {
                            "cmd": "cat /tmp/hello.txt",
                            "workdir": "/workspaces/my-projects",
                        }
                    ),
                    "call_id": "call_xyz",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:03.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_xyz",
                    "output": "hello world\n",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:04.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "id": "fc_def456",
                    "name": "update_plan",
                    "arguments": json.dumps(
                        {"plan": [{"step": "do X", "status": "done"}]}
                    ),
                    "call_id": "call_pln",
                },
            },
            {
                "timestamp": "2026-07-15T06:50:05.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_pln",
                    "output": "plan updated",
                },
            },
        ],
    )

    rich = codex_store.read_messages_rich(path)

    # Verify block types
    all_blocks = [
        (b["type"], b.get("name", "")) for m in rich for b in m.get("content", [])
    ]
    block_types = [t for t, _ in all_blocks]
    assert "tool_use" in block_types, f"expected tool_use blocks, got {block_types}"
    assert (
        "tool_result" in block_types
    ), f"expected tool_result blocks, got {block_types}"

    # Verify exec_command tool_use
    tool_use = [
        b for m in rich for b in m.get("content", []) if b["type"] == "tool_use"
    ]
    exec_cmd = [t for t in tool_use if t["name"] == "exec_command"]
    assert len(exec_cmd) == 1
    assert exec_cmd[0]["input"]["cmd"] == "cat /tmp/hello.txt"
    assert exec_cmd[0]["input"]["workdir"] == "/workspaces/my-projects"
    assert exec_cmd[0]["id"] == "fc_abc123"

    # Verify update_plan tool_use
    plan_calls = [t for t in tool_use if t["name"] == "update_plan"]
    assert len(plan_calls) == 1
    assert plan_calls[0]["input"]["plan"][0]["step"] == "do X"

    # Verify tool_result correlation
    tool_results = [
        b for m in rich for b in m.get("content", []) if b["type"] == "tool_result"
    ]
    assert len(tool_results) == 2
    assert tool_results[0]["tool_use_id"] == "call_xyz"
    assert tool_results[0]["content"] == "hello world\n"

    # Plain reader stays unaffected (no function_call in plain path)
    plain = codex_store.read_messages(path)
    assert [m["role"] for m in plain] == ["user"]


def _run():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {e!r}")
    print(f"\n{'PASS' if failures == 0 else 'FAIL'} - {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
