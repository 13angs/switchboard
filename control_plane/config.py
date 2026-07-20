"""Spine bindings (S6) — the workspace-specific contract the board depends on.

The board reads *the spine* (~/.claude, gh) rather than an external system.
Everything that ties it to THIS workspace is centralized here, so another
workspace adopts the tool by overriding a handful of env vars. Defaults
match /workspaces/my-projects; each is overridable via env.

Adoption contract (what a new workspace must satisfy) →
docs/sops/sop-orchestrator-board.md § Spine contract (S6).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# Base branch PRs merge into (branch badge overlay).
BASE_BRANCH = os.environ.get("ORCH_BASE_BRANCH", "main")

# --- ~/.claude session store --------------------------------------------
SESSION_ROOT = Path(
    os.environ.get("ORCH_SESSION_ROOT", os.path.expanduser("~/.claude/projects"))
)

# --- archive -------------------------------------------------------------
# Machine-local directory for archive ledger (dismiss + auto-archive state).
ARCHIVE_DIR = Path(
    os.environ.get(
        "ORCH_ARCHIVE_DIR",
        os.path.expanduser("~/.claude/orchestrator"),
    )
)

# --- terminal ------------------------------------------------------------
# Claude CLI binary to spawn for in-browser terminal.
CLAUDE_BIN = os.environ.get("ORCH_CLAUDE_BIN", "claude")
# Codex CLI binary to spawn for in-browser terminal.
CODEX_BIN = os.environ.get("ORCH_CODEX_BIN", "codex")
# Antigravity CLI binary (agy) to spawn for in-browser terminal.
AGY_BIN = os.environ.get("ORCH_AGY_BIN", "agy")
# Default cwd for new sessions (+New Session).
DEFAULT_CWD = os.environ.get("ORCH_DEFAULT_CWD", os.getcwd())

# --- agy session store --------------------------------------------------
# agy session root (conversations + metadata cache).
AGY_SESSION_ROOT = Path(
    os.environ.get(
        "ORCH_AGY_SESSION_ROOT",
        os.path.expanduser("~/.gemini/antigravity-cli"),
    )
)

# --- model provider (.env) ----------------------------------------------
# Model provider config lives in projects/agent-view/repos/agent-view/.env (gitignored) so the
# API key stays out of shell rc + out of git. Read once at server start.
_ORCH_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("ORCH_ENV_FILE", str(_ORCH_DIR / ".env")))

# DeepSeek (Anthropic-compatible endpoint): the child `claude` reads ANTHROPIC_*;
# we source them from ORCH_DEEPSEEK_* so they don't collide with the board's own
# environment. All four are required to enable the DeepSeek provider.
_DEEPSEEK_MAP = {
    "ANTHROPIC_BASE_URL": "ORCH_DEEPSEEK_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN": "ORCH_DEEPSEEK_AUTH_TOKEN",
    "ANTHROPIC_MODEL": "ORCH_DEEPSEEK_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL": "ORCH_DEEPSEEK_SMALL_FAST_MODEL",
}
# DeepSeek reasoning effort — fixed for now (plan C7), not per-session.
_DEEPSEEK_EFFORT = "xhigh"

# Ollama via an Anthropic-compatible local/proxy endpoint. The child `claude`
# still reads ANTHROPIC_*; we keep the local provider config under ORCH_OLLAMA_*.
_OLLAMA_REQUIRED = ("ORCH_OLLAMA_BASE_URL", "ORCH_OLLAMA_MODEL")
_OLLAMA_AUTH_TOKEN = "ORCH_OLLAMA_AUTH_TOKEN"
_OLLAMA_SMALL_FAST_MODEL = "ORCH_OLLAMA_SMALL_FAST_MODEL"
_OLLAMA_EFFORT = "ORCH_OLLAMA_EFFORT"
_OLLAMA_DEFAULT_AUTH_TOKEN = "ollama"


# mtime-keyed cache: the .env is read on every discover (once per Claude session
# — 260+ reads per 3s poll before this) but changes only when the operator edits
# it. Cache the parsed dict per resolved path and reuse while its mtime is
# unchanged. Callers treat the result as read-only, so the dict is shared.
_ENV_CACHE_LOCK = threading.Lock()
_ENV_CACHE: dict[Path, tuple[float, dict]] = {}


def load_env_file(path=None) -> dict:
    """Parse a minimal KEY=VALUE .env (blank/`#` lines ignored, quotes stripped).
    Missing file → empty dict. Not a full dotenv parser — no interpolation.

    Cached by file mtime — re-parsed only when the .env changes on disk."""
    p = Path(path) if path else ENV_FILE
    if path is None and "ORCH_ENV_FILE" not in os.environ and not p.is_file():
        fallback = _main_checkout_env_file(p.parent)
        if fallback is not None:
            p = fallback
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}  # missing file → empty (no caching of the absent state)
    with _ENV_CACHE_LOCK:
        entry = _ENV_CACHE.get(p)
        if entry is not None and entry[0] == mtime:
            return entry[1]
    out: dict = {}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    with _ENV_CACHE_LOCK:
        _ENV_CACHE[p] = (mtime, out)
    return out


def _main_checkout_env_file(orch_dir: Path) -> Path | None:
    """Find the main checkout's project .env when running from a git worktree.

    Per-task worktrees do not carry gitignored files. Supported layouts:
    `<repo>/.claude/worktrees/<branch>/...` and `<repo>/.worktrees/<branch>/...`.
    The durable local project config remains at `<repo>/projects/agent-view/repos/agent-view/.env`.
    """
    parts = orch_dir.resolve().parts
    for i in range(len(parts) - 1):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            repo_root = Path(*parts[:i])
            candidate = repo_root / "projects" / "agent-view" / "repos" / "agent-view" / ".env"
            return candidate if candidate.is_file() else None
        if parts[i] == ".worktrees":
            repo_root = Path(*parts[:i])
            candidate = repo_root / "projects" / "agent-view" / "repos" / "agent-view" / ".env"
            return candidate if candidate.is_file() else None
    return None


def available_providers(env_file: dict) -> list:
    """Providers the board may offer. Claude is always available (zero-config);
    external Claude providers only appear when their required ORCH_* keys are
    present (env file or process env)."""
    providers = ["claude"]
    if all((env_file.get(k) or os.environ.get(k)) for k in _DEEPSEEK_MAP.values()):
        providers.append("deepseek")
    if all((env_file.get(k) or os.environ.get(k)) for k in _OLLAMA_REQUIRED):
        providers.append("ollama")
    return providers


def provider_env(provider: str, env_file: dict) -> dict:
    """Env-var overrides to inject into the spawned `claude` for `provider`.

    - claude  → {} (default env, zero config)
    - deepseek → ANTHROPIC_* mapped from ORCH_DEEPSEEK_* + fixed xhigh effort
    - ollama → ANTHROPIC_* mapped from ORCH_OLLAMA_* for a local/proxy endpoint

    Raises ValueError if a required provider key is missing (caller surfaces it
    to the browser instead of silently spawning a mis-configured session)."""
    if provider == "deepseek":
        out: dict = {}
        missing = []
        for anthropic_key, orch_key in _DEEPSEEK_MAP.items():
            val = env_file.get(orch_key) or os.environ.get(orch_key)
            if not val:
                missing.append(orch_key)
            else:
                out[anthropic_key] = val
        if missing:
            raise ValueError(
                "DeepSeek provider not configured — missing: " + ", ".join(missing)
            )
        out["CLAUDE_CODE_EFFORT_LEVEL"] = _DEEPSEEK_EFFORT
        return out
    if provider == "ollama":
        base_url = env_file.get("ORCH_OLLAMA_BASE_URL") or os.environ.get(
            "ORCH_OLLAMA_BASE_URL"
        )
        model = env_file.get("ORCH_OLLAMA_MODEL") or os.environ.get("ORCH_OLLAMA_MODEL")
        missing = [
            k
            for k, val in (
                ("ORCH_OLLAMA_BASE_URL", base_url),
                ("ORCH_OLLAMA_MODEL", model),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                "Ollama provider not configured — missing: " + ", ".join(missing)
            )
        auth_token = (
            env_file.get(_OLLAMA_AUTH_TOKEN)
            or os.environ.get(_OLLAMA_AUTH_TOKEN)
            or _OLLAMA_DEFAULT_AUTH_TOKEN
        )
        small_fast_model = (
            env_file.get(_OLLAMA_SMALL_FAST_MODEL)
            or os.environ.get(_OLLAMA_SMALL_FAST_MODEL)
            or model
        )
        out = {
            "ANTHROPIC_BASE_URL": _ollama_anthropic_base_url(base_url),
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": small_fast_model,
        }
        effort = env_file.get(_OLLAMA_EFFORT) or os.environ.get(_OLLAMA_EFFORT)
        if effort:
            out["CLAUDE_CODE_EFFORT_LEVEL"] = effort
        return out
    return {}


def _ollama_anthropic_base_url(base_url: str) -> str:
    """Return the base URL Claude Code should receive for Ollama Anthropic mode.

    Ollama docs use `/api` for the native API and `/v1` for OpenAI-compatible
    clients. Claude Code appends Anthropic paths such as `/v1/messages`, so
    ANTHROPIC_BASE_URL must be the host root for Ollama local/cloud.
    """
    trimmed = base_url.rstrip("/")
    parsed = urlsplit(trimmed)
    if not parsed.scheme or not parsed.netloc:
        return trimmed
    path = parsed.path.rstrip("/")
    if path in ("/api", "/v1"):
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return trimmed
