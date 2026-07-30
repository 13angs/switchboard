"""ClaudeStore — read ~/.claude/projects/<enc>/<session>.jsonl transcripts.

Adapter layer isolating the (version-unstable) jsonl schema from the rest of
the board (HLD § Interfaces — tracked risk). Everything here is defensive:
missing fields degrade to None rather than raising, so a schema change on a
`claude` upgrade breaks *this file only*.

Encoding (verified 2026-07-11): the project dir name is the session cwd with
every '/' and '.' replaced by '-'. e.g.
  /workspaces/my-projects/.claude/worktrees/x
  -> -workspaces-my-projects--claude-worktrees-x

v2 (2026-07-11): added all_sessions_for_repo() — scan all sessions under a repo
root; added total_cost_usd + permission_denials to SessionSummary; removed
latest_session_for_cwd().
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config, pricing

PROJECTS_DIR = config.SESSION_ROOT  # spine binding (S6); overridable via env

# Pricing registry injected by server.py at startup (ADR-0022, was ADR-0014).
# None = cost disabled (pricing.json missing/invalid).
_pricing: Optional[pricing.PricingRegistry] = None


def set_pricing(registry: Optional[pricing.PricingRegistry]) -> None:
    """Inject the pricing registry for token→USD calculation."""
    global _pricing
    _pricing = registry


# Schema version this adapter was written/tested against (from event `version`).
TESTED_VERSION = "2.1.207"  # best-effort marker; not enforced


def encode_cwd(cwd: str) -> str:
    """Map an absolute cwd to its ~/.claude/projects dir name."""
    return cwd.replace("/", "-").replace(".", "-")


def existing_session_ids_for_cwd(cwd: str) -> set[str]:
    """Snapshot the session ids (jsonl stems) present under `cwd`'s project dir.
    Taken *before* spawning a fresh `claude` so the new session can be told apart
    from pre-existing ones under the same cwd."""
    proj_dir = PROJECTS_DIR / encode_cwd(os.path.abspath(cwd))
    if not proj_dir.is_dir():
        return set()
    return {j.stem for j in proj_dir.glob("*.jsonl")}


def newest_session_id_for_cwd(
    cwd: str, exclude: Optional[set[str]] = None
) -> Optional[str]:
    """Return the session_id (jsonl stem) of the newest session under `cwd`'s
    project dir whose id is not in `exclude`, or None. Used to discover a *fresh*
    session's id after spawning `claude` (the id isn't known until Claude writes
    its jsonl); `exclude` = the pre-spawn snapshot, so we ignore sibling sessions."""
    exclude = exclude or set()
    proj_dir = PROJECTS_DIR / encode_cwd(os.path.abspath(cwd))
    if not proj_dir.is_dir():
        return None
    newest: Optional[Path] = None
    newest_m = -1.0
    for jsonl_path in proj_dir.glob("*.jsonl"):
        if jsonl_path.stem in exclude:
            continue
        try:
            m = jsonl_path.stat().st_mtime
        except OSError:
            continue
        if m > newest_m:
            newest_m = m
            newest = jsonl_path
    return newest.stem if newest else None


# --- provider metadata (.provider.json sidecar) --------------------------
# Records which model provider a session was created with, next to its jsonl:
#   ~/.claude/projects/<enc-cwd>/<session-id>.provider.json  →  {"provider": ...}
# No file = Claude (default / backward-compat). The provider is locked for the
# life of the session (resume reads it back — never switch mid-session).


def read_provider(session_id: str) -> Optional[str]:
    """Return the stored provider for a session, or None (= Claude default).
    session_id is globally unique, so we scan project dirs for the sidecar."""
    if not session_id or not PROJECTS_DIR.is_dir():
        return None
    fname = f"{session_id}.provider.json"
    for proj_dir in PROJECTS_DIR.iterdir():
        f = proj_dir / fname
        if f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8")).get("provider")
            except (OSError, json.JSONDecodeError):
                return None
    return None


def find_session_path(session_id: str) -> Optional[Path]:
    """Return the jsonl path for `session_id`, scanning all Claude project dirs.

    Claude stores sessions under an encoded cwd directory, and `claude --resume`
    needs to run from that same cwd on some CLI versions. This lookup lets the
    server recover the original cwd even when the card is not in the current
    repo-scoped discovery result.
    """
    if not session_id or not PROJECTS_DIR.is_dir():
        return None
    fname = f"{session_id}.jsonl"
    for proj_dir in PROJECTS_DIR.iterdir():
        f = proj_dir / fname
        if f.is_file():
            return f
    return None


def detect_provider(session_id: str, env_file: dict) -> Optional[str]:
    """Fallback: detect provider from the session jsonl when .provider.json is
    missing (sessions created before the sidecar feature, or outside the
    orchestrator). Scans assistant messages for a model field; if it matches a
    configured non-Claude provider, returns that provider's name.

    Returns None (= default Claude) when no non-Claude model is detected."""
    if not session_id or not PROJECTS_DIR.is_dir():
        return None
    fname = f"{session_id}.jsonl"
    for proj_dir in PROJECTS_DIR.iterdir():
        jf = proj_dir / fname
        if not jf.is_file():
            continue
        try:
            # Read just enough of the jsonl to find the model — the model
            # appears in the first assistant message, typically within the
            # first 100 lines / 64 KiB. Cap to avoid loading huge files.
            text = jf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # Walk lines; bail once we've seen a model we recognize.
        for line in text.splitlines():
            line = line.strip()
            if not line or '"model"' not in line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            model = ev.get("model") or (ev.get("message") or {}).get("model") or ""
            if not model:
                continue
            # Match against configured DeepSeek model from .env (fast scan
            # uses "deepseek" substring; env match confirms it's our config).
            if "deepseek" in str(model).lower():
                deepseek_model = env_file.get("ORCH_DEEPSEEK_MODEL") or os.environ.get(
                    "ORCH_DEEPSEEK_MODEL"
                )
                if deepseek_model:
                    if str(model) == deepseek_model:
                        return "deepseek"
                    # Loose fallback: any deepseek model when provider is
                    # configured → assume deepseek provider (covers model
                    # aliases / variant suffixes).
                    return "deepseek"
                # ORCH_DEEPSEEK_MODEL not configured — skip; the model name
                # substring "deepseek" alone is not enough to claim the session.
            ollama_model = env_file.get("ORCH_OLLAMA_MODEL") or os.environ.get(
                "ORCH_OLLAMA_MODEL"
            )
            if ollama_model and str(model) == ollama_model:
                return "ollama"
        return None
    return None


def write_provider(session_id: str, cwd: str, provider: str) -> None:
    """Persist a session's provider sidecar next to its jsonl.

    Always written — including for the Claude default — so read_provider() is the
    authoritative source and detect_provider() jsonl scanning is never needed as a
    fallback for orchestrator-created sessions. The sidecar is a machine-local
    durable fact; absence of the file for pre-existing sessions means "created
    before the sidecar feature" and the resolution chain falls back to detection.
    """
    if not session_id:
        return
    proj_dir = PROJECTS_DIR / encode_cwd(os.path.abspath(cwd))
    try:
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / f"{session_id}.provider.json").write_text(
            json.dumps({"provider": provider}), encoding="utf-8"
        )
    except OSError:
        pass


def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        # ISO-8601, may end in 'Z'
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _text_of(content) -> str:
    """Flatten a message.content (str | list[block]) into a short text blurb."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                parts.append(block.get("text", ""))
            elif t == "tool_use":
                parts.append(f"[tool: {block.get('name', '?')}]")
            elif t == "tool_result":
                parts.append("[tool_result]")
        return " ".join(p for p in parts if p)
    return ""


def _content_blocks(content) -> list[dict]:
    """Preserve structured content blocks from a message.content.

    - `text` blocks: keep type + text.
    - `tool_use` blocks: keep id, name, input.
    - `thinking` blocks: keep type + thinking text; drop signature (internal).
    - `tool_result` blocks: keep tool_use_id, content (as-is — str or list).
    - Unknown block types: pass through with all fields.
    - If `content` is a plain string, wrap as a single text block.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    blocks: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "text":
            blocks.append({"type": "text", "text": block.get("text", "")})
        elif t == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", "?"),
                    "input": block.get("input", {}),
                }
            )
        elif t == "thinking":
            blocks.append({"type": "thinking", "thinking": block.get("thinking", "")})
        elif t == "tool_result":
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id", ""),
                    "content": block.get("content", ""),
                }
            )
        else:
            # Pass through unknown block types defensively.
            blocks.append({k: v for k, v in block.items() if k != "signature"})
    return blocks


def _token_count(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _usage_from_message(usage: dict) -> pricing.TokenUsage:
    """Map one claude `message.usage` block onto billable token classes.

    The cache-write TTL split lives in `usage.cache_creation`
    (`ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`) and is present on
    every assistant event in the local store, so ADR-0022 §SD3 prices writes
    exactly rather than blending. Transcripts that predate the split carry only
    the flat `cache_creation_input_tokens`; those fall back to the 5-minute
    rate — the cheaper of the two, so a missing split never inflates a figure.

    `usage.iterations[]` restates the same counts for multi-iteration turns and
    is deliberately not read: the top-level block is already the turn total.
    """
    write_5m = write_1h = 0
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        write_5m = _token_count(split.get("ephemeral_5m_input_tokens"))
        write_1h = _token_count(split.get("ephemeral_1h_input_tokens"))
    if write_5m == 0 and write_1h == 0:
        write_5m = _token_count(usage.get("cache_creation_input_tokens"))
    return pricing.TokenUsage(
        input_tokens=_token_count(usage.get("input_tokens")),
        output_tokens=_token_count(usage.get("output_tokens")),
        cache_write_5m=write_5m,
        cache_write_1h=write_1h,
        cache_read=_token_count(usage.get("cache_read_input_tokens")),
    )


@dataclass
class SessionSummary:
    session_id: Optional[str]
    jsonl_path: Path
    cwd: Optional[str]
    git_branch: Optional[str]
    version: Optional[str]
    title: Optional[str]  # ai-title > slug > first user prompt
    last_ts: Optional[datetime]  # newest event timestamp
    last_role: Optional[str]  # 'user' | 'assistant' | ...
    last_stop_reason: Optional[str]
    last_blurb: str = ""  # short text of the last user/assistant turn
    turn_count: int = 0  # user+assistant events (main thread only)
    had_error: bool = False  # any system/assistant error seen late
    permission_denials: bool = False  # any permission_denials in session
    total_cost_usd: Optional[float] = None  # envelope cost, else token-derived
    # Cost state beyond the number (ADR-0022 §SD2/§SD4). A session mixing a
    # priced and an unpriced model reports the priced subtotal with
    # cost_partial=True; one with no priceable model at all reports
    # total_cost_usd=None with unpriced_models non-empty. "We have no rate" and
    # "this cost nothing" must not share a cell.
    cost_partial: bool = False
    unpriced_models: list[str] = field(default_factory=list)
    # Oldest `checked_on` among the models that produced the figure
    # (ADR-0026 §SD3). A cost is only as trustworthy as its stalest rate.
    rates_checked_on: Optional[str] = None
    harness: str = "claude"
    provider: Optional[str] = "claude"

    def age_seconds(self, now: Optional[datetime] = None) -> Optional[float]:
        if self.last_ts is None:
            return None
        now = now or datetime.now(timezone.utc)
        ref = self.last_ts
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        return (now - ref).total_seconds()


def read_session(jsonl_path: Path) -> SessionSummary:
    """Parse a single session jsonl into a SessionSummary (best-effort)."""
    session_id = cwd = branch = version = title = None
    slug = None
    last_ts = None
    last_role = last_stop = None
    last_blurb = ""
    turns = 0
    had_error = False
    had_denials = False
    total_cost = None
    # Per model id, not per session (ADR-0022 §SD4): a session that starts on
    # one model and continues on a pricier one must not bill the whole run at
    # the first model's rate.
    usage_by_model: dict[str, pricing.TokenUsage] = {}

    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return SessionSummary(
            None, jsonl_path, None, None, None, None, None, None, None
        )

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue

        etype = ev.get("type")
        session_id = ev.get("sessionId") or ev.get("session_id") or session_id
        # Claude stores the jsonl under the cwd used when the conversation was
        # created. Later events may carry a different cwd after shell commands
        # `cd` into nested repos; using that later cwd breaks `claude --resume`.
        if cwd is None and ev.get("cwd"):
            cwd = ev.get("cwd")
        branch = ev.get("gitBranch") or branch
        version = ev.get("version") or version
        slug = ev.get("slug") or slug
        if etype == "ai-title" and ev.get("aiTitle"):
            title = ev["aiTitle"]

        # Skip sub-agent (sidechain) turns for main-thread state.
        if ev.get("isSidechain"):
            continue

        # Capture cost from any event that carries it (envelope-level).
        if ev.get("total_cost_usd") is not None:
            try:
                total_cost = float(ev["total_cost_usd"])
            except (ValueError, TypeError):
                pass
        # Permission denials — any event that carries them.
        if ev.get("permission_denials"):
            had_denials = True

        if etype in ("user", "assistant"):
            turns += 1
            ts = _parse_ts(ev.get("timestamp"))
            if ts and (last_ts is None or ts >= last_ts):
                last_ts = ts
                last_role = etype
                msg = ev.get("message") or {}
                last_stop = msg.get("stop_reason") if isinstance(msg, dict) else None
                last_blurb = (
                    _text_of(msg.get("content"))[:280] if isinstance(msg, dict) else ""
                )
            # crude error signal: assistant turns carrying an error stop
            if etype == "assistant":
                msg = ev.get("message") or {}
                if isinstance(msg, dict) and msg.get("stop_reason") in (
                    "error",
                    "refusal",
                ):
                    had_error = True
                # Accumulate billable tokens per model id (ADR-0022 §SD3/§SD4).
                # '<synthetic>' marks a locally-generated turn — no provider
                # call was made, so it is never billed.
                usage = msg.get("usage") if isinstance(msg, dict) else None
                model_id = msg.get("model") if isinstance(msg, dict) else None
                if (
                    isinstance(usage, dict)
                    and isinstance(model_id, str)
                    and model_id
                    and model_id != "<synthetic>"
                ):
                    usage_by_model[model_id] = usage_by_model.get(
                        model_id, pricing.TokenUsage()
                    ) + _usage_from_message(usage)
        elif etype == "system":
            sub = ev.get("subtype")
            if sub and "error" in str(sub).lower():
                had_error = True

    # Cost basis (ADR-0022 §SD1): token-derived is the primary path — no claude
    # assistant event carries an envelope total_cost_usd. The envelope read
    # above stays as an override for harnesses that do supply one.
    cost_partial = False
    unpriced_models: list[str] = []
    rates_checked_on: Optional[str] = None
    if total_cost is None and _pricing is not None and usage_by_model:
        priced_total = 0.0
        priced_any = False
        for model_id in sorted(usage_by_model):
            model_cost = pricing.calculate_cost(
                usage_by_model[model_id], model_id, _pricing
            )
            if model_cost is None:
                unpriced_models.append(model_id)
            else:
                priced_total += model_cost
                priced_any = True
        if priced_any:
            total_cost = priced_total
            cost_partial = bool(unpriced_models)
            rates_checked_on = _pricing.oldest_checked_on(sorted(usage_by_model))

    if not title:
        title = slug
    return SessionSummary(
        session_id=session_id,
        jsonl_path=jsonl_path,
        cwd=cwd,
        git_branch=branch,
        version=version,
        title=title,
        last_ts=last_ts,
        last_role=last_role,
        last_stop_reason=last_stop,
        last_blurb=last_blurb,
        turn_count=turns,
        had_error=had_error,
        permission_denials=had_denials,
        total_cost_usd=total_cost,
        cost_partial=cost_partial,
        unpriced_models=unpriced_models,
        rates_checked_on=rates_checked_on,
    )


def decode_cwd(dirname: str) -> Optional[str]:
    """Reverse encode_cwd — best-effort reconstruct absolute path from
    project dir name. The encoding replaces '/' and '.' with '-', so
    decoding is ambiguous (we can't distinguish original '-' from encoded
    '/' or '.'). We try common patterns: the project dir name with '-'
    mapped back, checking if the path exists.

    Returns None if no plausible path can be reconstructed.
    """
    import re as _re

    # The encoding: cwd.replace('/', '-').replace('.', '-')
    # We try the common case: a path starting with /workspaces/
    # The encoded form looks like: -workspaces-my-projects-...
    # We can reconstruct by splitting on '-' and trying likely '/' positions.
    # Simpler approach: just return the dirname as-is for the caller to use
    # as a fuzzy cwd prefix match. Callers use dirname against known repo roots.
    return None  # unused in v2; kept as a stub for the encode/decode symmetry


def all_sessions_for_repo(repo_root: str) -> list[SessionSummary]:
    """Scan all sessions whose cwd falls under repo_root. Returns every
    non-empty session sorted by last_ts descending (most recent first).

    Sessions with no session_id AND no last_ts are filtered out (empty/never-used).

    v3 (ADR-0010): mtime-based cache — jsonl files whose mtime hasn't changed
    since the last scan reuse their cached SessionSummary, skipping the full
    parse. Thread-safe.
    """
    sessions: list[SessionSummary] = []
    if not PROJECTS_DIR.is_dir():
        return sessions

    repo_root = os.path.abspath(repo_root)
    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        for jsonl_path in sorted(proj_dir.glob("*.jsonl")):
            try:
                s = _read_session_cached(jsonl_path)
            except Exception:
                continue
            if not s.session_id and s.last_ts is None:
                continue  # empty/never-used session
            if s.cwd and not s.cwd.startswith(repo_root):
                continue  # not under this repo
            # Session with no cwd in jsonl (legacy) — check if the encoded
            # dir name plausibly maps to something under repo_root.
            if not s.cwd:
                # The encoded dir name for <repo_root> is the prefix base.
                # If proj_dir.name doesn't start with encode_cwd(repo_root),
                # skip.
                encoded_repo = encode_cwd(repo_root)
                if not proj_dir.name.startswith(encoded_repo):
                    continue
                s.cwd = repo_root  # best guess
            # harness + provider are set/cached inside _read_session_cached
            # (folded into the mtime cache); no per-discover re-resolution here.
            s.harness = "claude"
            sessions.append(s)

    sessions.sort(
        key=lambda s: s.last_ts or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return sessions


# --- mtime-based summary cache (ADR-0010 §1) ---------------------------------

_SUMMARY_CACHE_LOCK = threading.Lock()
_SUMMARY_CACHE: dict[Path, tuple[float, SessionSummary]] = {}


def _resolve_provider(session_id: Optional[str]) -> str:
    """Resolve a session's model provider: sidecar first, then a jsonl model
    scan, else the Claude default. Called only on a summary-cache miss (mtime
    change), so the expensive `detect_provider` full-file read runs once per file
    revision instead of for every session on every 3s discover — the provider is
    then cached alongside the summary. An actively-growing session self-heals to
    the correct provider once its model line lands and the mtime advances."""
    sid = session_id or ""
    if not sid:
        return "claude"
    return (
        read_provider(sid) or detect_provider(sid, config.load_env_file()) or "claude"
    )


def _read_session_cached(jsonl_path: Path) -> SessionSummary:
    """Return cached SessionSummary if the file's mtime is unchanged, otherwise
    re-read and update the cache (ADR-0010 §1 mtime optimisation).

    Provider resolution is folded in here so it is cached by the same mtime key
    — a cache hit returns a summary with `provider` already set, keeping the
    172 MiB "read every jsonl to detect the model" scan off the 3s discover path."""
    try:
        mtime = jsonl_path.stat().st_mtime
    except OSError:
        return SessionSummary(
            None, jsonl_path, None, None, None, None, None, None, None
        )

    with _SUMMARY_CACHE_LOCK:
        entry = _SUMMARY_CACHE.get(jsonl_path)
        if entry is not None:
            cached_mtime, summary = entry
            if cached_mtime == mtime:
                return summary

    summary = read_session(jsonl_path)
    summary.provider = _resolve_provider(summary.session_id)
    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE[jsonl_path] = (mtime, summary)
    return summary


def invalidate_summary_cache(jsonl_path: Path | None = None) -> None:
    """Clear the mtime cache for a specific jsonl (or all if None).
    Called when a session's jsonl is modified by a live harness."""
    with _SUMMARY_CACHE_LOCK:
        if jsonl_path is None:
            _SUMMARY_CACHE.clear()
        else:
            _SUMMARY_CACHE.pop(jsonl_path, None)


def read_messages(
    jsonl_path: Path,
    limit: Optional[int] = None,
    since: Optional[str] = None,
) -> list[dict]:
    """Return main-thread turns as [{role, text, ts}].

    - `since` (ISO timestamp): only messages with timestamp > since. None = all.
    - `limit`: return last N messages (applied AFTER since filter). None = all.
    - Sub-agent (sidechain) turns are excluded.
    - For the board transcript drawer: call with limit=40 (or omit for default).
    - For the chat UI poll: call with since=<last-ts> (no limit).
    """
    msgs: list[dict] = []
    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return msgs
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("isSidechain"):
            continue
        if ev.get("type") not in ("user", "assistant"):
            continue
        msg = ev.get("message") or {}
        text = _text_of(msg.get("content")) if isinstance(msg, dict) else ""
        if not text:
            continue
        msgs.append({"role": ev.get("type"), "text": text, "ts": ev.get("timestamp")})

    # Apply since filter (client-side incremental poll).
    if since:
        since_dt = _parse_ts(since)
        if since_dt:
            msgs = [
                m
                for m in msgs
                if _parse_ts(m.get("ts")) is not None
                and _parse_ts(m["ts"]) > since_dt  # type: ignore[operator]
            ]

    # Apply limit (last N).
    if limit is not None and limit > 0 and len(msgs) > limit:
        msgs = msgs[-limit:]
    elif limit is None:
        pass  # return all
    else:
        pass  # limit <= 0 or None → return all

    return msgs


def read_messages_rich(
    jsonl_path: Path,
    limit: Optional[int] = None,
    since: Optional[str] = None,
) -> list[dict]:
    """Return main-thread turns with structured content blocks + metadata.

    Same signature and filtering as read_messages(), but returns:
      [{role, ts, content: [{type, ...}], model?, stop_reason?, usage?}]

    - `content` preserves the JSONL content blocks: text, thinking, tool_use,
      tool_result (ADR-0006).
    - `model`, `stop_reason`, `usage` are only present on assistant messages.
    - Sub-agent (sidechain) turns are excluded.
    """
    msgs: list[dict] = []
    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return msgs
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("isSidechain"):
            continue
        if ev.get("type") not in ("user", "assistant"):
            continue
        msg = ev.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else ""
        blocks = _content_blocks(content)
        if not blocks:
            continue
        entry: dict = {
            "role": ev.get("type"),
            "ts": ev.get("timestamp"),
            "content": blocks,
        }
        # Assistant-only metadata
        if isinstance(msg, dict) and ev.get("type") == "assistant":
            model = msg.get("model")
            if model:
                entry["model"] = model
            stop = msg.get("stop_reason")
            if stop:
                entry["stop_reason"] = stop
            usage = msg.get("usage")
            if isinstance(usage, dict):
                entry["usage"] = {
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                }
        msgs.append(entry)

    # Apply since filter.
    if since:
        since_dt = _parse_ts(since)
        if since_dt:
            msgs = [
                m
                for m in msgs
                if _parse_ts(m.get("ts")) is not None
                and _parse_ts(m["ts"]) > since_dt  # type: ignore[operator]
            ]

    # Apply limit (last N).
    if limit is not None and limit > 0 and len(msgs) > limit:
        msgs = msgs[-limit:]

    return msgs
