"""CodexStore - read $CODEX_HOME/sessions/**/*.jsonl transcripts.

Only transcript files are read. Credential files under CODEX_HOME are not
opened or inspected.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .claude_store import SessionSummary

CODEX_HOME = Path(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")))
SESSIONS_DIR = CODEX_HOME / "sessions"


def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _message_text(payload: dict) -> str:
    msg = payload.get("message")
    if isinstance(msg, str):
        return msg
    if isinstance(msg, list):
        parts = []
        for block in msg:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("message") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(p for p in parts if p)
    return ""


def _content_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"output_text", "input_text", "text"}:
            parts.append(str(block.get("text") or ""))
    return " ".join(p for p in parts if p)


def _turn_from_event(ev: dict) -> Optional[tuple[str, str, Optional[str]]]:
    etype = ev.get("type")
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}

    if etype == "event_msg":
        ptype = payload.get("type")
        role = None
        if ptype == "user_message":
            role = "user"
        elif ptype == "agent_message":
            role = "assistant"
        if not role:
            return None
        text = _message_text(payload)
        return (role, text, ev.get("timestamp")) if text else None

    if etype == "response_item" and payload.get("type") == "message":
        # Codex also records OpenAI response items. Use assistant items as a
        # fallback because user input is already represented by event_msg rows.
        if payload.get("role") != "assistant":
            return None
        text = _content_text(payload)
        return ("assistant", text, ev.get("timestamp")) if text else None

    return None


def _seen_assistant_duplicate(
    seen: dict[str, Optional[datetime]], text: str, ts: Optional[datetime]
) -> bool:
    if text not in seen:
        seen[text] = ts
        return False
    prev_ts = seen[text]
    seen[text] = ts
    if prev_ts is None or ts is None:
        return True
    return abs((ts - prev_ts).total_seconds()) <= 5


def _iter_jsonl() -> list[Path]:
    if not SESSIONS_DIR.is_dir():
        return []
    return sorted(SESSIONS_DIR.rglob("*.jsonl"))


def read_session(jsonl_path: Path) -> SessionSummary:
    session_id = cwd = version = title = None
    provider = "openai"
    last_ts = None
    last_role = None
    last_blurb = ""
    turns = 0
    total_cost = None
    seen_assistant_texts: dict[str, Optional[datetime]] = {}

    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return SessionSummary(
            None,
            jsonl_path,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            harness="codex",
            provider=provider,
        )

    for line in lines:
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue

        ts = _parse_ts(ev.get("timestamp"))
        etype = ev.get("type")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}

        if etype == "session_meta":
            session_id = payload.get("session_id") or payload.get("id") or session_id
            cwd = payload.get("cwd") or cwd
            provider = payload.get("model_provider") or provider
            version = payload.get("cli_version") or version
            if ts and (last_ts is None or ts >= last_ts):
                last_ts = ts
            continue

        if etype == "turn_context":
            cwd = payload.get("cwd") or cwd
            continue

        turn = _turn_from_event(ev)
        if not turn:
            continue
        role, text, _raw_ts = turn
        if role == "assistant":
            if _seen_assistant_duplicate(seen_assistant_texts, text, ts):
                continue
        turns += 1
        if title is None and role == "user":
            title = text[:80]
        if ts and (last_ts is None or ts >= last_ts):
            last_ts = ts
            last_role = role
            last_blurb = text[:280]

        # Capture cost from any event that carries it (envelope-level).
        # ADR-0014 — same pattern as claude_store.py:350-353.
        if total_cost is None and ev.get("total_cost_usd") is not None:
            try:
                total_cost = float(ev["total_cost_usd"])
            except (ValueError, TypeError):
                pass

    return SessionSummary(
        session_id=session_id,
        jsonl_path=jsonl_path,
        cwd=cwd,
        git_branch=None,
        version=version,
        title=title,
        last_ts=last_ts,
        last_role=last_role,
        last_stop_reason=None,
        last_blurb=last_blurb,
        turn_count=turns,
        total_cost_usd=total_cost,
        harness="codex",
        provider=provider,
    )


# --- mtime-based summary cache (ADR-0010 §1, mirrors claude_store) -----------
# codex read_session() re-parses the whole jsonl on every call. Without a cache
# every discover (every 3s) + every transcript poll re-read all codex files.
# Cache the SessionSummary by jsonl mtime so idle files skip the parse.
_SUMMARY_CACHE_LOCK = threading.Lock()
_SUMMARY_CACHE: dict[Path, tuple[float, SessionSummary]] = {}


def invalidate_summary_cache(jsonl_path: Path | None = None) -> None:
    """Clear the mtime cache for a jsonl (or all if None)."""
    with _SUMMARY_CACHE_LOCK:
        if jsonl_path is None:
            _SUMMARY_CACHE.clear()
        else:
            _SUMMARY_CACHE.pop(jsonl_path, None)


def _read_session_cached(jsonl_path: Path) -> SessionSummary:
    """Return the cached SessionSummary if the file mtime is unchanged, else
    re-parse and update the cache (ADR-0010 §1)."""
    try:
        mtime = jsonl_path.stat().st_mtime
    except OSError:
        return read_session(jsonl_path)
    with _SUMMARY_CACHE_LOCK:
        entry = _SUMMARY_CACHE.get(jsonl_path)
        if entry is not None and entry[0] == mtime:
            return entry[1]
    summary = read_session(jsonl_path)
    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE[jsonl_path] = (mtime, summary)
    return summary


def all_sessions_for_repo(repo_root: str) -> list[SessionSummary]:
    repo_root = os.path.abspath(repo_root)
    sessions = []
    for jsonl_path in _iter_jsonl():
        try:
            s = _read_session_cached(jsonl_path)
        except Exception:
            continue
        if not s.session_id and s.last_ts is None:
            continue
        if s.cwd and not os.path.abspath(s.cwd).startswith(repo_root):
            continue
        sessions.append(s)
    sessions.sort(
        key=lambda s: s.last_ts or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return sessions


def existing_session_ids_for_cwd(cwd: str) -> set[str]:
    cwd = os.path.abspath(cwd)
    out = set()
    for jsonl_path in _iter_jsonl():
        s = _read_session_cached(jsonl_path)
        if s.session_id and s.cwd and os.path.abspath(s.cwd) == cwd:
            out.add(s.session_id)
    return out


def newest_session_id_for_cwd(
    cwd: str, exclude: Optional[set[str]] = None
) -> Optional[str]:
    exclude = exclude or set()
    cwd = os.path.abspath(cwd)
    newest: Optional[SessionSummary] = None
    for jsonl_path in _iter_jsonl():
        s = _read_session_cached(jsonl_path)
        if not s.session_id or s.session_id in exclude:
            continue
        if not s.cwd or os.path.abspath(s.cwd) != cwd:
            continue
        if newest is None:
            newest = s
            continue
        s_ts = s.last_ts or datetime.min.replace(tzinfo=timezone.utc)
        n_ts = newest.last_ts or datetime.min.replace(tzinfo=timezone.utc)
        if s_ts >= n_ts:
            newest = s
    return newest.session_id if newest else None


def find_session_path(session_id: str) -> Optional[Path]:
    if not session_id:
        return None
    for jsonl_path in _iter_jsonl():
        if _read_session_cached(jsonl_path).session_id == session_id:
            return jsonl_path
    return None


def read_messages(
    jsonl_path: Path,
    limit: Optional[int] = None,
    since: Optional[str] = None,
) -> list[dict]:
    msgs = []
    since_dt = _parse_ts(since)
    seen_assistant_texts: dict[str, Optional[datetime]] = {}
    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return msgs

    for line in lines:
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        turn = _turn_from_event(ev)
        if not turn:
            continue
        role, text, ts = turn
        ts_dt = _parse_ts(ts)
        if since_dt and (ts_dt is None or ts_dt <= since_dt):
            continue
        if role == "assistant":
            if _seen_assistant_duplicate(seen_assistant_texts, text, ts_dt):
                continue
        msgs.append({"role": role, "text": text, "ts": ts})

    if limit is not None and limit > 0 and len(msgs) > limit:
        return msgs[-limit:]
    return msgs


def _content_blocks_codex(content) -> list[dict]:
    """Preserve structured content blocks from a Codex message/content payload.

    Handles Codex-specific block types: output_text, input_text, text, message.
    Falls back to a generic text block for unknown shapes.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    blocks: list[dict] = []
    for block in content:
        if isinstance(block, str):
            blocks.append({"type": "text", "text": block})
            continue
        if not isinstance(block, dict):
            continue
        t = block.get("type", "text")
        text = block.get("text") or block.get("message") or ""
        # Normalise Codex type names to the canonical set from ADR-0006.
        if t in ("output_text", "input_text", "text", "message"):
            blocks.append({"type": "text", "text": str(text)})
        elif t == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", "?"),
                    "input": block.get("input", {}),
                }
            )
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
            blocks.append({k: v for k, v in block.items()})
    return blocks


def _extract_file_paths_from_cmd(cmd: str, workdir: Optional[str] = None) -> list[str]:
    """Extract likely file paths from a shell command string.

    Heuristic — tokenises the command (respecting single/double quotes),
    keeps tokens that look like paths (contain ``/``, don't start with ``-``),
    and resolves relative paths against *workdir*.  Used by the
    ``exec_command`` handler so that ``view=files`` can surface at least
    *some* file references from codex sessions.
    """
    if not cmd:
        return []

    # Crude tokeniser that respects quoted segments.
    tokens: list[str] = []
    current = ""
    quote: Optional[str] = None
    for ch in cmd:
        if quote:
            if ch == quote:
                quote = None
            else:
                current += ch
        elif ch in ("'", '"'):
            quote = ch
        elif ch in (" ", "\t", "\n", ";"):
            if current:
                tokens.append(current)
                current = ""
        else:
            current += ch
    if current:
        tokens.append(current)

    paths: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        # Must contain a slash and not be a flag.
        if "/" not in t or t.startswith("-"):
            continue
        # Skip known non-path tokens.
        if t.startswith("rg") or t.startswith("docker") or t.startswith("kubectl"):
            continue
        # Skip env-var assignments (contain = before /).
        if "=" in t.split("/")[0]:
            continue
        # Resolve relative → absolute (best-effort).
        if not t.startswith("/") and workdir:
            resolved = os.path.normpath(os.path.join(workdir, t))
        else:
            resolved = t
        if resolved in seen:
            continue
        # Only keep tokens that plausibly name a file or directory.
        if _is_plausible_path(resolved):
            seen.add(resolved)
            paths.append(resolved)
    return paths


def _is_plausible_path(path: str) -> bool:
    """Return True if *path* looks like a real file/directory reference."""
    # Must have at least one path segment beyond the root.
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return False
    # Last segment must contain a letter/digit (not just symbols).
    last = parts[-1]
    if not any(c.isalnum() for c in last):
        return False
    return True


def _rich_turn_from_event(ev: dict) -> Optional[dict]:
    """Extract a rich message entry from a Codex JSONL event.

    Returns {role, ts, content: [{type, ...}]} or None if the event
    is not a message turn.
    """
    etype = ev.get("type")
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}

    if etype == "event_msg":
        ptype = payload.get("type")
        role = None
        if ptype == "user_message":
            role = "user"
        elif ptype == "agent_message":
            role = "assistant"
        if not role:
            return None
        msg = payload.get("message")
        blocks = _content_blocks_codex(msg)
        if not blocks:
            return None
        return {"role": role, "ts": ev.get("timestamp"), "content": blocks}

    if etype == "response_item":
        ptype = payload.get("type")

        if ptype == "message":
            if payload.get("role") != "assistant":
                return None
            content = payload.get("content")
            blocks = _content_blocks_codex(content)
            if not blocks:
                return None
            return {"role": "assistant", "ts": ev.get("timestamp"), "content": blocks}

        if ptype == "function_call":
            name = payload.get("name", "?")
            args_str = payload.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except (json.JSONDecodeError, TypeError):
                args = {"_raw": str(args_str)}
            # For exec_command: extract file paths from the shell command so
            # that view=files (file-refs.ts) can find file references.
            if (
                name == "exec_command"
                and isinstance(args, dict)
                and "file_path" not in args
            ):
                cmd = args.get("cmd", "")
                wd = args.get("workdir")
                extracted = _extract_file_paths_from_cmd(cmd, wd)
                if extracted:
                    args["file_path"] = extracted[0]
            return {
                "role": "assistant",
                "ts": ev.get("timestamp"),
                "content": [
                    {
                        "type": "tool_use",
                        "id": payload.get("id") or payload.get("call_id") or "",
                        "name": name,
                        "input": (
                            args if isinstance(args, dict) else {"_raw": str(args)}
                        ),
                    }
                ],
            }

        if ptype == "function_call_output":
            return {
                "role": "assistant",
                "ts": ev.get("timestamp"),
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": payload.get("call_id", ""),
                        "content": payload.get("output", ""),
                    }
                ],
            }

    return None


def read_messages_rich(
    jsonl_path: Path,
    limit: Optional[int] = None,
    since: Optional[str] = None,
) -> list[dict]:
    """Return main-thread Codex turns with structured content blocks.

    Same contract as claude_store.read_messages_rich() (ADR-0006).
    Deduplication of near-duplicate assistant messages is preserved.
    """
    msgs: list[dict] = []
    since_dt = _parse_ts(since)
    seen_texts: dict[str, Optional[datetime]] = {}
    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return msgs

    for line in lines:
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        turn = _rich_turn_from_event(ev)
        if not turn:
            continue
        ts = turn.get("ts")
        ts_dt = _parse_ts(ts) if isinstance(ts, str) else None
        if since_dt and (ts_dt is None or ts_dt <= since_dt):
            continue
        # Dedup: flatten content to a key for near-duplicate detection.
        if turn.get("role") == "assistant":
            text_key = " ".join(
                b.get("text", "")
                for b in turn.get("content", [])
                if b.get("type") == "text"
            )
            if text_key and _seen_assistant_duplicate(seen_texts, text_key, ts_dt):
                continue
        msgs.append(turn)

    if limit is not None and limit > 0 and len(msgs) > limit:
        return msgs[-limit:]
    return msgs
