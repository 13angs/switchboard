#!/usr/bin/env python3
"""P2 model-provider checks — config parsing, provider_env mapping, .provider.json
sidecar roundtrip, and env injection into the spawned process.

Env injection is verified end-to-end by pointing ORCH_CLAUDE_BIN at `env` (prints
its environment + exits), so we can assert the ANTHROPIC_* overrides actually
reach the child — no `claude` binary required.

Run:
    python3 projects/agent-view/repos/agent-view/tests/test_provider.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Point the session store at a temp dir BEFORE importing (config reads it at
# import time), so the .provider.json roundtrip doesn't touch the real ~/.claude.
_TMP = tempfile.mkdtemp(prefix="orch-p2-")
os.environ["ORCH_SESSION_ROOT"] = _TMP

import pytest  # noqa: E402

from control_plane import claude_store, config, terminal  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Re-pin env + claude_store's import-frozen root per test.

    pytest imports all test modules before running any test — the last
    module-level environ write wins the run, and claude_store.PROJECTS_DIR
    froze at first config import. Solo runs pass without this; the full
    suite does not.
    """
    monkeypatch.setenv("ORCH_SESSION_ROOT", _TMP)
    monkeypatch.setattr(claude_store, "PROJECTS_DIR", Path(_TMP))


FULL_DEEPSEEK = {
    "ORCH_DEEPSEEK_BASE_URL": "https://api.deepseek.com/anthropic",
    "ORCH_DEEPSEEK_AUTH_TOKEN": "sk-test-not-a-real-key",
    "ORCH_DEEPSEEK_MODEL": "deepseek-v4-pro",
    "ORCH_DEEPSEEK_SMALL_FAST_MODEL": "deepseek-v4-flash",
}

FULL_OLLAMA = {
    "ORCH_OLLAMA_BASE_URL": "http://127.0.0.1:11434/anthropic",
    "ORCH_OLLAMA_MODEL": "qwen2.5-coder:7b",
}


def test_load_env_file_parses_kv_and_ignores_noise():
    p = Path(_TMP) / "sample.env"
    p.write_text(
        "# comment\n\nORCH_DEEPSEEK_MODEL = deepseek-v4-pro \n"
        'ORCH_DEEPSEEK_AUTH_TOKEN="sk-quoted"\nbogus line no equals\n'
    )
    env = config.load_env_file(p)
    assert env["ORCH_DEEPSEEK_MODEL"] == "deepseek-v4-pro"
    assert env["ORCH_DEEPSEEK_AUTH_TOKEN"] == "sk-quoted"
    assert "bogus line no equals" not in env


def test_load_env_file_falls_back_to_main_checkout_env_from_worktree():
    root = Path(_TMP) / "repo"
    worktree_orch = (
        root
        / ".claude"
        / "worktrees"
        / "task-x"
        / "projects"
        / "agent-view"
        / "repos"
        / "agent-view"
    )
    main_orch = root / "projects" / "agent-view" / "repos" / "agent-view"
    worktree_orch.mkdir(parents=True)
    main_orch.mkdir(parents=True)
    (main_orch / ".env").write_text(
        "\n".join(f"{k}={v}" for k, v in FULL_DEEPSEEK.items()),
        encoding="utf-8",
    )

    old_env_file = config.ENV_FILE
    old_orch_env = os.environ.pop("ORCH_ENV_FILE", None)
    try:
        config.ENV_FILE = worktree_orch / ".env"
        env = config.load_env_file()
        assert config.available_providers(env) == ["claude", "deepseek"]
    finally:
        config.ENV_FILE = old_env_file
        if old_orch_env is not None:
            os.environ["ORCH_ENV_FILE"] = old_orch_env


def test_provider_env_claude_is_zero_config():
    assert config.provider_env("claude", {}) == {}


def test_provider_env_deepseek_maps_and_sets_effort():
    out = config.provider_env("deepseek", FULL_DEEPSEEK)
    assert out["ANTHROPIC_BASE_URL"] == FULL_DEEPSEEK["ORCH_DEEPSEEK_BASE_URL"]
    assert out["ANTHROPIC_AUTH_TOKEN"] == FULL_DEEPSEEK["ORCH_DEEPSEEK_AUTH_TOKEN"]
    assert out["ANTHROPIC_MODEL"] == FULL_DEEPSEEK["ORCH_DEEPSEEK_MODEL"]
    assert (
        out["ANTHROPIC_SMALL_FAST_MODEL"]
        == FULL_DEEPSEEK["ORCH_DEEPSEEK_SMALL_FAST_MODEL"]
    )
    assert out["CLAUDE_CODE_EFFORT_LEVEL"] == "xhigh"


def test_provider_env_deepseek_missing_raises():
    try:
        config.provider_env("deepseek", {"ORCH_DEEPSEEK_BASE_URL": "x"})
    except ValueError as e:
        assert "ORCH_DEEPSEEK_AUTH_TOKEN" in str(e)
        return
    raise AssertionError("expected ValueError for incomplete DeepSeek config")


def test_provider_env_ollama_maps_minimal_config_with_safe_defaults():
    out = config.provider_env("ollama", FULL_OLLAMA)
    assert out["ANTHROPIC_BASE_URL"] == FULL_OLLAMA["ORCH_OLLAMA_BASE_URL"]
    assert out["ANTHROPIC_AUTH_TOKEN"] == "ollama"
    assert out["ANTHROPIC_MODEL"] == FULL_OLLAMA["ORCH_OLLAMA_MODEL"]
    assert out["ANTHROPIC_SMALL_FAST_MODEL"] == FULL_OLLAMA["ORCH_OLLAMA_MODEL"]
    assert "CLAUDE_CODE_EFFORT_LEVEL" not in out


def test_provider_env_ollama_uses_optional_auth_small_model_and_effort():
    env = {
        **FULL_OLLAMA,
        "ORCH_OLLAMA_AUTH_TOKEN": "local-token",
        "ORCH_OLLAMA_SMALL_FAST_MODEL": "qwen2.5-coder:3b",
        "ORCH_OLLAMA_EFFORT": "low",
    }
    out = config.provider_env("ollama", env)
    assert out["ANTHROPIC_AUTH_TOKEN"] == "local-token"
    assert out["ANTHROPIC_SMALL_FAST_MODEL"] == "qwen2.5-coder:3b"
    assert out["CLAUDE_CODE_EFFORT_LEVEL"] == "low"


def test_provider_env_ollama_normalizes_cloud_and_openai_base_paths():
    for base_url in ("https://ollama.com/api", "https://ollama.com/v1"):
        out = config.provider_env(
            "ollama",
            {
                **FULL_OLLAMA,
                "ORCH_OLLAMA_BASE_URL": base_url,
                "ORCH_OLLAMA_AUTH_TOKEN": "cloud-token",
                "ORCH_OLLAMA_MODEL": "gemma4:31b-cloud",
            },
        )
        assert out["ANTHROPIC_BASE_URL"] == "https://ollama.com"
        assert out["ANTHROPIC_AUTH_TOKEN"] == "cloud-token"
        assert out["ANTHROPIC_MODEL"] == "gemma4:31b-cloud"


def test_provider_env_ollama_missing_required_raises():
    try:
        config.provider_env("ollama", {"ORCH_OLLAMA_BASE_URL": "x"})
    except ValueError as e:
        assert "ORCH_OLLAMA_MODEL" in str(e)
        return
    raise AssertionError("expected ValueError for incomplete Ollama config")


def test_available_providers_gated_on_full_config():
    assert config.available_providers({}) == ["claude"]
    assert "deepseek" in config.available_providers(FULL_DEEPSEEK)
    assert "ollama" in config.available_providers(FULL_OLLAMA)


def test_provider_sidecar_roundtrip_and_claude_writes_nothing():
    cwd = "/tmp/orch-p2-cwd"
    # Claude (default) writes NO sidecar → read returns None.
    claude_store.write_provider("sess-claude", cwd, "claude")
    assert claude_store.read_provider("sess-claude") is None
    # DeepSeek writes a sidecar → read returns it (provider lock).
    claude_store.write_provider("sess-deep", cwd, "deepseek")
    assert claude_store.read_provider("sess-deep") == "deepseek"
    # Ollama uses the same non-default provider sidecar path.
    claude_store.write_provider("sess-ollama", cwd, "ollama")
    assert claude_store.read_provider("sess-ollama") == "ollama"


def test_detect_provider_ollama_matches_configured_model_without_sidecar():
    cwd = "/tmp/orch-p2-ollama"
    session_id = "sess-ollama-detect"
    path = Path(_TMP) / claude_store.encode_cwd(cwd) / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": session_id,
                "model": FULL_OLLAMA["ORCH_OLLAMA_MODEL"],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    assert claude_store.detect_provider(session_id, FULL_OLLAMA) == "ollama"


def test_env_injection_reaches_child():
    """spawn_claude(env=...) must inject the overrides into the spawned process."""
    # Save/restore ORCH_CLAUDE_BIN (same idiom as the ENV_FILE test above) —
    # leaking `env` here starved the PTY-lifecycle tests of their `cat` stub.
    old_bin = os.environ.get("ORCH_CLAUDE_BIN")
    os.environ["ORCH_CLAUDE_BIN"] = "env"  # prints its environment, then exits
    inj = {
        "ANTHROPIC_BASE_URL": "https://injected.example",
        "CLAUDE_CODE_EFFORT_LEVEL": "xhigh",
    }
    try:
        term = terminal.spawn_claude(cwd="/tmp", provider="deepseek", env=inj)
        buf = bytearray()
        done = threading.Event()

        class Sub:
            def on_data(self, d):
                buf.extend(d)

            def on_control(self, m):
                pass

            def on_exit(self, c):
                done.set()

        term.attach(Sub())
        term.start_reader(lambda t: None)
        assert done.wait(5), "child did not exit"
        text = bytes(buf).decode("utf-8", "replace")
        assert "ANTHROPIC_BASE_URL=https://injected.example" in text, text[:400]
        assert "CLAUDE_CODE_EFFORT_LEVEL=xhigh" in text
    finally:
        if old_bin is None:
            os.environ.pop("ORCH_CLAUDE_BIN", None)
        else:
            os.environ["ORCH_CLAUDE_BIN"] = old_bin


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
    print(f"\n{'PASS' if failures == 0 else 'FAIL'} — {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
