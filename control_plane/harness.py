"""Harness adapter registry for Claude, Codex, and agy (Antigravity).

This module keeps the executable harness (`claude` vs `codex` vs `agy`) separate
from the harness-local provider (`claude`, `deepseek`, `ollama`, `openai`,
`google`).
"""

from __future__ import annotations

import os
from typing import Optional

from . import config, workspace


_MANUAL_TIERS = ("light", "standard", "heavy")
_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")


def _manual_session_start_capabilities(repo_root: str, harness_name: str) -> dict:
    """Project workspace launch policy for one manual fresh-session harness.

    The browser renders this projection; it never owns concrete model ids or
    effort compatibility rules (ADR-0054).
    """
    empty_defaults = {"tier": None, "model": None, "effort": None}
    if harness_name == "agy":
        return {
            "pinning": False,
            "models": [],
            "efforts": [],
            "defaults": empty_defaults,
            "available": True,
            "error": None,
        }

    try:
        payload = workspace.workspace_overview(repo_root)
    except ValueError as exc:
        return {
            "pinning": True,
            "models": [],
            "efforts": [],
            "defaults": empty_defaults,
            "available": False,
            "error": f"workspace model map unavailable: {exc}",
        }

    dispatch = payload.get("dispatch") or {}
    tier_key = "tiers" if harness_name == "claude" else "codex_tiers"
    tiers = dispatch.get(tier_key) or {}
    missing = [tier for tier in _MANUAL_TIERS if not tiers.get(tier)]
    if not dispatch.get("present") or missing:
        return {
            "pinning": True,
            "models": [],
            "efforts": [],
            "defaults": empty_defaults,
            "available": False,
            "error": (
                "workspace model map unavailable"
                if not missing
                else "workspace model map missing tier(s): " + ", ".join(missing)
            ),
        }

    models = []
    for tier in _MANUAL_TIERS:
        models.append(
            {
                "tier": tier,
                "id": tiers[tier],
                "supports_effort": not (
                    harness_name == "claude" and tier == "light"
                ),
            }
        )

    efforts = [
        effort
        for effort in _EFFORT_ORDER
        if effort in workspace.VALID_EFFORTS
        and not (harness_name == "codex" and effort == "max")
    ]
    standard = next(model for model in models if model["tier"] == "standard")
    default_effort = "high" if standard["supports_effort"] and "high" in efforts else None
    return {
        "pinning": True,
        "models": models,
        "efforts": efforts,
        "defaults": {
            "tier": "standard",
            "model": standard["id"],
            "effort": default_effort,
        },
        "available": True,
        "error": None,
    }


def available_launchers(env_file: dict, repo_root: Optional[str] = None) -> list[dict]:
    """Return launcher options for /state.

    When repo_root is supplied, each launcher also carries the manual
    session-start capabilities/defaults defined by ADR-0054. Keeping the
    argument optional preserves non-Board callers that only need providers.
    """
    launchers = [
        {"harness": "claude", "providers": config.available_providers(env_file)},
        {"harness": "codex", "providers": ["openai"]},
        {"harness": "agy", "providers": ["google"]},
    ]
    if repo_root is not None:
        for launcher in launchers:
            launcher["session_start"] = _manual_session_start_capabilities(
                repo_root, launcher["harness"]
            )
    return launchers


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
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> list[str]:
    """Build the interactive PTY command for a harness session.

    `model` pins the tier the session runs on (ADR-0030). It is an argv flag
    rather than an env var because that is the only lever that actually holds:
    a session with no `--model` inherits whatever launched the server, and a
    citation in the prompt changes nothing at all.

    `effort` rides the same path (ADR-0032) — the per-role thinking depth
    (`low`/`medium`/`high`/`xhigh`/`max`) that today only lives in
    `roles.md § โมเดลต่อ role` with nothing carrying it to the process.
    """
    if harness_name == "claude":
        if provider not in ("claude", "deepseek", "ollama"):
            raise ValueError(f"unknown Claude provider: {provider}")
        cmd = [os.environ.get("ORCH_CLAUDE_BIN", config.CLAUDE_BIN)]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd
    if harness_name == "codex":
        if provider != "openai":
            raise ValueError(f"unknown Codex provider: {provider}")
        cmd = [os.environ.get("ORCH_CODEX_BIN", config.CODEX_BIN)]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["-c", f"model_reasoning_effort={effort}"]
        cmd += ["--no-alt-screen", "-C", cwd]
        if session_id:
            cmd += ["resume", session_id]
        return cmd
    if model:
        raise ValueError(f"model pinning is not supported for harness: {harness_name}")
    if effort:
        raise ValueError(f"effort pinning is not supported for harness: {harness_name}")
    if harness_name == "agy":
        if provider != "google":
            raise ValueError(f"unknown agy provider: {provider}")
        cmd = [os.environ.get("ORCH_AGY_BIN", config.AGY_BIN)]
        if session_id:
            cmd += ["--conversation", session_id]
        return cmd
    raise ValueError(f"unknown harness: {harness_name}")
