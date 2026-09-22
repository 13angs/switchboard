#!/usr/bin/env python3
"""Manual New Session launch policy and spawn-first Board flow (ADR-0054 / S64)."""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from control_plane import harness  # noqa: E402


def _launchers(monkeypatch, dispatch):
    monkeypatch.setattr(
        harness.workspace, "workspace_overview", lambda _repo_root: {"dispatch": dispatch}
    )
    return {item["harness"]: item for item in harness.available_launchers({}, "/workspace")}


def test_manual_launch_defaults_come_from_workspace_and_filter_capabilities(monkeypatch):
    launchers = _launchers(
        monkeypatch,
        {
            "present": True,
            "tiers": {
                "light": "claude-light-from-workspace",
                "standard": "claude-standard-from-workspace",
                "heavy": "claude-heavy-from-workspace",
            },
            "codex_tiers": {
                "light": "codex-light-from-workspace",
                "standard": "codex-standard-from-workspace",
                "heavy": "codex-heavy-from-workspace",
            },
        },
    )

    claude = launchers["claude"]["session_start"]
    assert claude["defaults"] == {
        "tier": "standard",
        "model": "claude-standard-from-workspace",
        "effort": "high",
    }
    assert claude["models"][0] == {
        "tier": "light",
        "id": "claude-light-from-workspace",
        "supports_effort": False,
    }
    assert "max" in claude["efforts"]

    codex = launchers["codex"]["session_start"]
    assert codex["defaults"]["model"] == "codex-standard-from-workspace"
    assert codex["defaults"]["effort"] == "high"
    assert "max" not in codex["efforts"]

    agy = launchers["agy"]["session_start"]
    assert agy["pinning"] is False
    assert agy["models"] == []
    assert agy["efforts"] == []
    assert agy["available"] is True


def test_pin_capable_launcher_fails_closed_when_model_map_is_missing(monkeypatch):
    launchers = _launchers(monkeypatch, {"present": False, "tiers": {}, "codex_tiers": {}})
    for name in ("claude", "codex"):
        caps = launchers[name]["session_start"]
        assert caps["pinning"] is True
        assert caps["available"] is False
        assert caps["models"] == []
        assert caps["defaults"]["model"] is None
        assert "model map" in caps["error"]


class _PromptlessTerm:
    session_id = None
    attach_key = "attach:s64"

    def is_alive(self):
        return True


def test_promptless_session_start_returns_on_attach_key_without_waiting(monkeypatch, tmp_path):
    term = _PromptlessTerm()
    monkeypatch.setattr(server, "_get_or_spawn", lambda *_args, **_kwargs: (term, False))
    sleeps = []
    monkeypatch.setattr(server.time, "sleep", lambda seconds: sleeps.append(seconds))

    httpd = HTTPServer(("127.0.0.1", 0), server.make_handler(str(tmp_path)))
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
        conn.request(
            "POST",
            "/session/start",
            body=json.dumps({"harness": "claude", "provider": "claude"}),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()

        assert response.status == 202
        assert body["session_id"] is None
        assert body["attach_key"] == "attach:s64"
        assert sleeps == []
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def test_board_spawns_before_navigation_and_attaches_existing_pty():
    board = (ROOT / "src" / "pages" / "Board.tsx").read_text(encoding="utf-8")
    assert "await startSessionApi(" in board
    assert "params.set('session_id', res.session_id)" in board
    assert "params.set('attach_key', res.attach_key)" in board
    assert "params.set('model'" not in board
    assert "params.set('effort'" not in board
    assert board.index("await startSessionApi(") < board.index("popup.location.href = url")


def test_dialog_renders_only_server_supplied_model_and_effort_choices():
    dialog = (ROOT / "src" / "components" / "board" / "NewSessionDialog.tsx").read_text(
        encoding="utf-8"
    )
    assert "launcher?.session_start" in dialog
    assert "caps.defaults.model" in dialog
    assert "caps.defaults.effort" in dialog
    assert "option.supports_effort" in dialog
    assert "caps.efforts.map" in dialog
    for concrete_id in ("claude-sonnet", "claude-opus", "claude-haiku", "gpt-5."):
        assert concrete_id not in dialog


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
