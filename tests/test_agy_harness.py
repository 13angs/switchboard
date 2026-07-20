#!/usr/bin/env python3
"""agy (Antigravity CLI) harness adapter checks.

Run:
    python3 projects/agent-view/repos/agent-view/tests/test_agy_harness.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

_TMP = tempfile.mkdtemp(prefix="orch-agy-")
CLAUDE_ROOT = Path(_TMP) / "claude-projects"
AGY_ROOT = Path(_TMP) / "agy-home"

# Script-mode / solo-import path (under pytest the authority is _isolated_env).
os.environ["ORCH_SESSION_ROOT"] = str(CLAUDE_ROOT)
os.environ["ORCH_AGY_SESSION_ROOT"] = str(AGY_ROOT)
os.environ["ORCH_CLAUDE_BIN"] = "claude-test-bin"
os.environ["ORCH_CODEX_BIN"] = "codex-test-bin"
os.environ["ORCH_AGY_BIN"] = "agy-test-bin"

import pytest  # noqa: E402

from control_plane import (
    agy_store,
    claude_store,
    config,
    discovery,
    harness,
)  # noqa: E402
import server as srv_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Re-pin env + import-frozen store roots per test."""
    shutil.rmtree(CLAUDE_ROOT, ignore_errors=True)
    shutil.rmtree(AGY_ROOT, ignore_errors=True)
    monkeypatch.setenv("ORCH_SESSION_ROOT", str(CLAUDE_ROOT))
    monkeypatch.setenv("ORCH_AGY_SESSION_ROOT", str(AGY_ROOT))
    monkeypatch.setenv("ORCH_CLAUDE_BIN", "claude-test-bin")
    monkeypatch.setenv("ORCH_CODEX_BIN", "codex-test-bin")
    monkeypatch.setenv("ORCH_AGY_BIN", "agy-test-bin")
    monkeypatch.setattr(claude_store, "PROJECTS_DIR", CLAUDE_ROOT)
    monkeypatch.setattr(agy_store, "AGY_ROOT", AGY_ROOT)
    monkeypatch.setattr(agy_store, "CACHE_DIR", AGY_ROOT / "cache")
    monkeypatch.setattr(agy_store, "CONVERSATIONS_DIR", AGY_ROOT / "conversations")
    monkeypatch.setattr(agy_store, "HISTORY_PATH", AGY_ROOT / "history.jsonl")
    discovery.invalidate_cache()  # ADR-0010 — stale cache betrays isolation
    monkeypatch.setattr(config, "AGY_SESSION_ROOT", AGY_ROOT)
    # Re-point config.AGY_BIN for build_command tests.
    monkeypatch.setattr(config, "AGY_BIN", "agy-test-bin")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _agy_metadata(
    conversations: dict | None = None,
) -> dict:
    return {"conversations": conversations or {}}


def _agy_last_conversations(mapping: dict | None = None) -> dict:
    return mapping or {}


def _write_agy_metadata(data: dict) -> None:
    agy_store.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (agy_store.CACHE_DIR / "conversation_metadata.json").write_text(
        json.dumps(data, separators=(",", ":")), encoding="utf-8"
    )


def _write_agy_last_conversations(data: dict) -> None:
    agy_store.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (agy_store.CACHE_DIR / "last_conversations.json").write_text(
        json.dumps(data, separators=(",", ":")), encoding="utf-8"
    )


def _touch_db(conv_id: str) -> Path:
    agy_store.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    db = agy_store.CONVERSATIONS_DIR / f"{conv_id}.db"
    db.write_text("", encoding="utf-8")
    return db


# ---------------------------------------------------------------------------
# harness registry
# ---------------------------------------------------------------------------


def test_launchers_includes_agy():
    launchers = harness.available_launchers({})
    assert {"harness": "agy", "providers": ["google"]} in launchers
    # Claude and Codex are still present.
    assert {"harness": "claude", "providers": ["claude"]} in launchers
    assert {"harness": "codex", "providers": ["openai"]} in launchers


def test_resolve_agy():
    assert harness.resolve("google", "agy") == ("agy", "google")
    assert harness.resolve(None, "agy") == ("agy", "google")
    assert harness.resolve("", "agy") == ("agy", "google")


def test_validate_agy():
    # google provider is always valid; anything else is rejected.
    harness.validate("agy", "google", {})
    try:
        harness.validate("agy", "openai", {})
    except ValueError as e:
        assert "unknown agy provider" in str(e)
        return
    raise AssertionError("expected ValueError for non-google agy provider")


def test_provider_env_agy_zero_config():
    assert harness.provider_env("agy", "google", {}) == {}


def test_build_command_agy_fresh():
    cmd = harness.build_command("agy", None, "/repo", "google")
    assert cmd == ["agy-test-bin"]


def test_build_command_agy_resume():
    cmd = harness.build_command(
        "agy", "eb5cbe37-89d9-4a57-8a5f-3231bb62bbde", "/repo", "google"
    )
    assert cmd == [
        "agy-test-bin",
        "--conversation",
        "eb5cbe37-89d9-4a57-8a5f-3231bb62bbde",
    ]


def test_build_command_agy_rejects_unknown_provider():
    try:
        harness.build_command("agy", None, "/repo", "openai")
    except ValueError as e:
        assert "unknown agy provider" in str(e)
        return
    raise AssertionError("expected ValueError for non-google agy provider")


# ---------------------------------------------------------------------------
# chat message payload
# ---------------------------------------------------------------------------


def test_chat_message_payload_agy_uses_newline():
    assert srv_mod._chat_message_payload("hello", "agy") == b"hello\n"


# ---------------------------------------------------------------------------
# agy store — metadata parsing
# ---------------------------------------------------------------------------


def test_read_session_from_metadata():
    conv_id = "eb5cbe37-89d9-4a57-8a5f-3231bb62bbde"
    _write_agy_metadata(
        _agy_metadata(
            {
                conv_id: {
                    "summary": {
                        "ID": conv_id,
                        "Title": "test agy session",
                        "Preview": "last assistant response",
                        "NumSteps": 5,
                        "WorkspaceURIs": ["file:///workspaces/my-projects"],
                        "AgentName": "claude-opus",
                    },
                    "is_internal": False,
                    "last_modified_time": "2026-07-18T23:50:32.618224372Z",
                }
            }
        )
    )
    _write_agy_last_conversations({"/workspaces/my-projects": conv_id})
    _touch_db(conv_id)

    s = agy_store.read_session(conv_id)
    assert s.session_id == conv_id
    assert s.title == "test agy session"
    assert s.last_blurb == "last assistant response"
    assert s.turn_count == 5
    assert s.harness == "agy"
    assert s.provider == "google"
    assert s.cwd == "/workspaces/my-projects"
    assert s.last_ts is not None


def test_read_session_from_db_path_derives_id():
    conv_id = "4059c4ee-ad1e-4124-81b7-a78eb94a7c63"
    _write_agy_metadata(
        _agy_metadata(
            {
                conv_id: {
                    "summary": {
                        "ID": conv_id,
                        "Title": "notebooklm work",
                        "Preview": "",
                        "NumSteps": 3,
                        "WorkspaceURIs": [
                            "file:///workspaces/my-projects/tools/notebooklm"
                        ],
                        "AgentName": "",
                    },
                    "is_internal": False,
                    "last_modified_time": "2026-07-18T23:47:00Z",
                }
            }
        )
    )
    _touch_db(conv_id)

    db_path = agy_store.CONVERSATIONS_DIR / f"{conv_id}.db"
    s = agy_store.read_session(db_path)
    assert s.session_id == conv_id
    assert s.title == "notebooklm work"
    assert s.cwd == "/workspaces/my-projects/tools/notebooklm"


def test_all_sessions_for_repo_filters_by_workspace():
    conv_main = "main-conv-id-1"
    conv_other = "other-conv-id-2"
    _write_agy_metadata(
        _agy_metadata(
            {
                conv_main: {
                    "summary": {
                        "ID": conv_main,
                        "Title": "main project",
                        "Preview": "",
                        "NumSteps": 2,
                        "WorkspaceURIs": ["file:///workspaces/my-projects"],
                    },
                    "is_internal": False,
                    "last_modified_time": "2026-07-18T23:50:00Z",
                },
                conv_other: {
                    "summary": {
                        "ID": conv_other,
                        "Title": "other project",
                        "Preview": "",
                        "NumSteps": 1,
                        "WorkspaceURIs": ["file:///tmp/other"],
                    },
                    "is_internal": False,
                    "last_modified_time": "2026-07-18T23:40:00Z",
                },
            }
        )
    )
    _write_agy_last_conversations(
        {
            "/workspaces/my-projects": conv_main,
            "/tmp/other": conv_other,
        }
    )
    _touch_db(conv_main)
    _touch_db(conv_other)

    sessions = agy_store.all_sessions_for_repo("/workspaces/my-projects")
    ids = [s.session_id for s in sessions]
    assert conv_main in ids
    assert conv_other not in ids


def test_all_sessions_for_repo_reads_metadata_not_present_in_last_conversations():
    conv_id = "metadata-only-conv"
    _write_agy_metadata(
        _agy_metadata(
            {
                conv_id: {
                    "summary": {
                        "ID": conv_id,
                        "Title": "metadata indexed session",
                        "Preview": "",
                        "NumSteps": 4,
                        "WorkspaceURIs": ["file:///workspaces/my-projects"],
                    },
                    "is_internal": False,
                    "last_modified_time": "2026-07-18T23:50:32.618224372Z",
                }
            }
        )
    )
    _write_agy_last_conversations({})
    _touch_db(conv_id)

    sessions = agy_store.all_sessions_for_repo("/workspaces/my-projects")
    assert [s.session_id for s in sessions] == [conv_id]
    assert sessions[0].title == "metadata indexed session"
    assert sessions[0].last_ts is not None


def test_all_sessions_for_repo_keeps_last_conversation_with_missing_metadata():
    conv_id = "last-map-missing-metadata"
    _write_agy_metadata(_agy_metadata({}))
    _write_agy_last_conversations({"/workspaces/my-projects": conv_id})
    _touch_db(conv_id)

    sessions = agy_store.all_sessions_for_repo("/workspaces/my-projects")
    assert [s.session_id for s in sessions] == [conv_id]
    assert sessions[0].title == "Antigravity last-map"
    assert sessions[0].last_ts is not None
    assert "metadata cache is incomplete" in sessions[0].last_blurb


def test_existing_session_ids_for_cwd():
    conv_id = "cwd-test-conv"
    _write_agy_last_conversations(
        {
            "/workspaces/my-projects": conv_id,
            "/tmp/other": "other-conv",
        }
    )

    ids = agy_store.existing_session_ids_for_cwd("/workspaces/my-projects")
    assert conv_id in ids
    assert "other-conv" not in ids


def test_existing_session_ids_for_cwd_includes_metadata_indexed_sessions():
    conv_id = "cwd-metadata-indexed"
    _write_agy_metadata(
        _agy_metadata(
            {
                conv_id: {
                    "summary": {
                        "ID": conv_id,
                        "WorkspaceURIs": ["file:///workspaces/my-projects"],
                    },
                    "last_modified_time": "2026-07-18T23:50:00Z",
                }
            }
        )
    )
    _write_agy_last_conversations({})
    _touch_db(conv_id)

    ids = agy_store.existing_session_ids_for_cwd("/workspaces/my-projects")
    assert conv_id in ids


def test_newest_session_id_for_cwd():
    """last_conversations.json maps each cwd to a single (latest) conversation.
    newest_session_id_for_cwd returns that conversation; with it excluded,
    returns None (no other candidates for the same cwd)."""
    conv = "newest-cwd-conv"
    cwd = "/workspaces/my-projects"
    _write_agy_metadata(
        _agy_metadata(
            {
                conv: {
                    "summary": {
                        "ID": conv,
                        "Title": "the one",
                        "Preview": "",
                        "NumSteps": 2,
                        "WorkspaceURIs": [f"file://{cwd}"],
                    },
                    "is_internal": False,
                    "last_modified_time": "2026-07-18T23:50:00Z",
                }
            }
        )
    )
    _write_agy_last_conversations({cwd: conv})
    _touch_db(conv)

    result = agy_store.newest_session_id_for_cwd(cwd)
    assert result == conv

    # Excluding the only candidate → None.
    assert agy_store.newest_session_id_for_cwd(cwd, exclude={conv}) is None


def test_newest_session_id_for_cwd_falls_back_to_db_mtime_when_metadata_missing():
    conv_id = "newest-db-mtime-conv"
    cwd = "/workspaces/my-projects"
    _write_agy_metadata(_agy_metadata({}))
    _write_agy_last_conversations({cwd: conv_id})
    _touch_db(conv_id)

    assert agy_store.newest_session_id_for_cwd(cwd) == conv_id


def test_find_session_path():
    conv_id = "find-path-test"
    db = _touch_db(conv_id)

    found = agy_store.find_session_path(conv_id)
    assert found == db

    assert agy_store.find_session_path("nonexistent") is None
    assert agy_store.find_session_path("") is None


def test_state_exposes_agy_launcher():
    state = srv_mod.build_state(str(REPO))
    assert {"harness": "agy", "providers": ["google"]} in state["launchers"]


def test_state_marks_agy_card_when_metadata_cache_is_incomplete():
    conv_id = "state-missing-metadata"
    _write_agy_metadata(_agy_metadata({}))
    _write_agy_last_conversations({str(REPO): conv_id})
    _touch_db(conv_id)

    state = srv_mod.build_state(str(REPO))
    cards = [s for s in state["sessions"] if s["session_id"] == conv_id]
    assert len(cards) == 1
    assert cards[0]["title"] == "Antigravity state-mi"
    assert cards[0]["note_kind"] == "metadata"
    assert cards[0]["note_text"] == "Metadata pending"


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


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
