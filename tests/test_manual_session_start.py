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


DISPATCH = {
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
}


def _launchers(monkeypatch, dispatch=DISPATCH, env_file=None):
    monkeypatch.setattr(
        harness.workspace, "workspace_overview", lambda _repo_root: {"dispatch": dispatch}
    )
    return {
        item["harness"]: item
        for item in harness.available_launchers(env_file or {}, "/workspace")
    }


def test_manual_launch_defaults_come_from_workspace_and_filter_capabilities(monkeypatch):
    launchers = _launchers(monkeypatch)

    claude = launchers["claude"]["session_start"]["claude"]
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

    codex = launchers["codex"]["session_start"]["openai"]
    assert codex["defaults"]["model"] == "codex-standard-from-workspace"
    assert codex["defaults"]["effort"] == "high"
    assert "max" not in codex["efforts"]

    agy = launchers["agy"]["session_start"]["google"]
    assert agy["pinning"] is False
    assert agy["models"] == []
    assert agy["efforts"] == []
    assert agy["available"] is True


def test_external_claude_providers_keep_provider_owned_model_and_effort(monkeypatch):
    env_file = {
        "ORCH_DEEPSEEK_BASE_URL": "https://deepseek.invalid",
        "ORCH_DEEPSEEK_AUTH_TOKEN": "token",
        "ORCH_DEEPSEEK_MODEL": "deepseek-chat",
        "ORCH_DEEPSEEK_SMALL_FAST_MODEL": "deepseek-chat",
        "ORCH_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "ORCH_OLLAMA_MODEL": "qwen-local",
    }
    launchers = _launchers(monkeypatch, env_file=env_file)
    claude_caps = launchers["claude"]["session_start"]

    assert set(claude_caps) == {"claude", "deepseek", "ollama"}
    assert claude_caps["claude"]["pinning"] is True
    for provider in ("deepseek", "ollama"):
        caps = claude_caps[provider]
        assert caps["pinning"] is False
        assert caps["models"] == []
        assert caps["efforts"] == []
        assert caps["defaults"] == {"tier": None, "model": None, "effort": None}


def test_pin_capable_launcher_fails_closed_when_model_map_is_missing(monkeypatch):
    launchers = _launchers(
        monkeypatch, {"present": False, "tiers": {}, "codex_tiers": {}}
    )
    for harness_name, provider in (("claude", "claude"), ("codex", "openai")):
        caps = launchers[harness_name]["session_start"][provider]
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


@pytest.mark.parametrize(
    ("harness_name", "provider", "model", "expected"),
    [
        (
            "claude",
            "claude",
            "claude-standard-from-workspace",
            ["--model", "claude-standard-from-workspace", "--effort", "high"],
        ),
        (
            "codex",
            "openai",
            "codex-standard-from-workspace",
            ["--model", "codex-standard-from-workspace", "-c", "model_reasoning_effort=high"],
        ),
    ],
)
def test_session_start_wires_selected_pin_into_command_and_attach_reuses_one_pty(
    monkeypatch, tmp_path, harness_name, provider, model, expected
):
    """Acceptance 13-14: HTTP selection reaches argv and attach_key does not respawn."""
    server._registry.clear()
    spawned_commands = []

    class FakeTerm:
        def __init__(self):
            self.session_id = None
            self.attach_key = None
            self.harness = harness_name
            self.provider = provider

        def is_alive(self):
            return True

        def start_reader(self, _on_close):
            return None

    def fake_spawn(
        selected_harness,
        session_id=None,
        cwd=None,
        provider="claude",
        model=None,
        effort=None,
        **_kwargs,
    ):
        spawned_commands.append(
            harness.build_command(
                selected_harness,
                session_id,
                cwd or str(tmp_path),
                provider,
                model,
                effort,
            )
        )
        return FakeTerm()

    monkeypatch.setattr(server.terminal, "spawn_harness", fake_spawn)
    monkeypatch.setattr(server, "_start_id_capture", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server.workspace,
        "allowed_models",
        lambda _root, selected_harness="claude": {model},
    )
    monkeypatch.setattr(
        server.workspace,
        "model_tier",
        lambda _root, _model, _harness="claude": "standard",
    )

    httpd = HTTPServer(("127.0.0.1", 0), server.make_handler(str(tmp_path)))
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
        conn.request(
            "POST",
            "/session/start",
            body=json.dumps(
                {
                    "harness": harness_name,
                    "provider": provider,
                    "model": model,
                    "effort": "high",
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()

        assert response.status == 202
        assert body["attach_key"].startswith("attach:")
        assert len(spawned_commands) == 1
        command = spawned_commands[0]
        for token in expected:
            assert token in command

        attached, reused = server._get_or_spawn(
            None,
            str(tmp_path),
            harness_name,
            provider,
            {},
            attach_key=body["attach_key"],
        )
        assert reused is True
        assert attached is server._registry[body["attach_key"]]
        assert len(spawned_commands) == 1, "attach must reuse the HTTP-created PTY"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)
        server._registry.clear()


@pytest.mark.parametrize("provider, field", [("deepseek", "model"), ("ollama", "effort")])
def test_external_claude_provider_rejects_manual_pin(monkeypatch, tmp_path, provider, field):
    payload = {"harness": "claude", "provider": provider, field: "should-not-override-provider"}
    httpd = HTTPServer(("127.0.0.1", 0), server.make_handler(str(tmp_path)))
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
        conn.request(
            "POST",
            "/session/start",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()

        assert response.status == 400
        assert "owns its" in body["error"]
        assert "pinning is unsupported" in body["error"]
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


def test_new_session_dialog_is_viewport_bounded_with_scrollable_body():
    dialog = (ROOT / "src" / "components" / "board" / "NewSessionDialog.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "src" / "pages" / "Board.css").read_text(encoding="utf-8")

    assert 'className="modal new-session-modal"' in dialog
    assert 'className="new-session-body"' in dialog
    assert 'className="modal-actions new-session-actions"' in dialog
    assert dialog.index('className="new-session-body"') < dialog.index(
        'className="modal-actions new-session-actions"'
    )
    assert "max-height: calc(100dvh - 24px);" in css
    assert ".new-session-body {" in css
    assert "overflow-y: auto;" in css
    assert ".new-session-actions {" in css


def test_dialog_uses_provider_specific_server_capabilities():
    dialog = (ROOT / "src" / "components" / "board" / "NewSessionDialog.tsx").read_text(
        encoding="utf-8"
    )
    assert "launcher?.session_start?.[provider]" in dialog
    assert "defaultSelection(currentLauncher, nextProvider)" in dialog
    assert "caps.defaults.model" in dialog
    assert "caps.defaults.effort" in dialog
    assert "selectedModel?.supports_effort" in dialog
    assert "caps.efforts.map" in dialog
    for concrete_id in ("claude-sonnet", "claude-opus", "claude-haiku", "gpt-5."):
        assert concrete_id not in dialog


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
