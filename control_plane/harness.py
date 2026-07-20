"""Harness adapter registry for Claude, Codex, and agy (Antigravity).

This module keeps the executable harness (`claude` vs `codex` vs `agy`) separate
from the harness-local provider (`claude`, `deepseek`, `ollama`, `openai`,
`google`).
"""

from __future__ import annotations

import os
from typing import Optional

from . import config


def available_launchers(env_file: dict) -> list[dict]:
    """Return launcher options for /state.

    `providers` is kept harness-local: Claude can expose DeepSeek when
    configured, while Codex exposes OpenAI only and agy exposes Google only.
    """
    return [
        {"harness": "claude", "providers": config.available_providers(env_file)},
        {"harness": "codex", "providers": ["openai"]},
        {"harness": "agy", "providers": ["google"]},
    ]


def resolve(
    provider: Optional[str], harness_name: Optional[str] = None
) -> tuple[str, str]:
    """Normalize API/query input into `(harness, provider)`.

    Backward compatibility: omitted harness means the legacy Claude path, so a
    provider-only `deepseek` or `ollama` request resolves to `claude/<provider>`.
    """
    h = (harness_name or "claude").strip().lower()
    p = (provider or "").strip().lower()
    if h == "claude":
        return "claude", p or "claude"
    if h == "codex":
        return "codex", p or "openai"
    if h == "agy":
        return "agy", p or "google"
    raise ValueError(f"unknown harness: {h}")


def validate(harness_name: str, provider: str, env_file: dict) -> None:
    if harness_name == "claude":
        if provider not in config.available_providers(env_file):
            raise ValueError(f"unknown Claude provider: {provider}")
        return
    if harness_name == "codex":
        if provider != "openai":
            raise ValueError(f"unknown Codex provider: {provider}")
        return
    if harness_name == "agy":
        if provider != "google":
            raise ValueError(f"unknown agy provider: {provider}")
        return
    raise ValueError(f"unknown harness: {harness_name}")


def provider_env(harness_name: str, provider: str, env_file: dict) -> dict:
    validate(harness_name, provider, env_file)
    if harness_name == "claude":
        return config.provider_env(provider, env_file)
    return {}


def build_command(
    harness_name: str,
    session_id: Optional[str],
    cwd: str,
    provider: str,
) -> list[str]:
    """Build the interactive PTY command for a harness session."""
    if harness_name == "claude":
        if provider not in ("claude", "deepseek", "ollama"):
            raise ValueError(f"unknown Claude provider: {provider}")
        cmd = [os.environ.get("ORCH_CLAUDE_BIN", config.CLAUDE_BIN)]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd
    if harness_name == "codex":
        if provider != "openai":
            raise ValueError(f"unknown Codex provider: {provider}")
        cmd = [
            os.environ.get("ORCH_CODEX_BIN", config.CODEX_BIN),
            "--no-alt-screen",
            "-C",
            cwd,
        ]
        if session_id:
            cmd += ["resume", session_id]
        return cmd
    if harness_name == "agy":
        if provider != "google":
            raise ValueError(f"unknown agy provider: {provider}")
        cmd = [os.environ.get("ORCH_AGY_BIN", config.AGY_BIN)]
        if session_id:
            cmd += ["--conversation", session_id]
        return cmd
    raise ValueError(f"unknown harness: {harness_name}")
