"""agy_store — read agy (Antigravity CLI) session metadata + transcripts.

agy stores conversations as SQLite databases under
~/.gemini/antigravity-cli/conversations/<uuid>.db with metadata cached in
~/.gemini/antigravity-cli/cache/.

ADR-0011: transcript readers use regex-based extraction from protobuf BLOBs
(no .proto dependency). Fidelity is degraded vs Claude/Codex (no timestamps,
model, or usage fields) but sufficient for view=chat + view=files.

Credential files (antigravity-oauth-token) are never read or inspected.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from .claude_store import SessionSummary

AGY_ROOT = config.AGY_SESSION_ROOT
CACHE_DIR = AGY_ROOT / "cache"
CONVERSATIONS_DIR = AGY_ROOT / "conversations"
HISTORY_PATH = AGY_ROOT / "history.jsonl"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        # "2026-07-18T23:50:32.618224372Z" or "0001-01-01T00:00:00Z"
        text = raw.replace("Z", "+00:00")
        text = re.sub(r"(\.\d{6})\d+(?=[+-]\d\d:\d\d$)", r"\1", text)
        dt = datetime.fromisoformat(text)
        year = dt.year
        if year <= 1:
            return None
        return dt
    except (ValueError, TypeError):
        return None


def _file_mtime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


# (mtime, size)-keyed cache: the metadata + last_conversations JSON files are
# read many times per discover (once per candidate conversation, ×2-3 — an N+1
# that grows with session count) but change only when agy writes them. These are
# rewritten in place (unlike append-only jsonl), so a rewrite can keep the same
# mtime tick while changing content — the size guards that case. Cache the parsed
# payload per path and reuse while (mtime, size) is unchanged (F4). Callers treat
# the result as read-only, so the dict is shared.
_JSON_CACHE_LOCK = threading.Lock()
_JSON_CACHE: dict[Path, tuple[tuple[float, int], dict]] = {}


def _load_json(path: Path) -> dict:
    try:
        st = path.stat()
        sig = (st.st_mtime, st.st_size)
    except OSError:
        return {}
    with _JSON_CACHE_LOCK:
        entry = _JSON_CACHE.get(path)
        if entry is not None and entry[0] == sig:
            return entry[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}
    with _JSON_CACHE_LOCK:
        _JSON_CACHE[path] = (sig, data)
    return data


def _last_conversations() -> dict[str, str]:
    """Return {workspace_path: conversation_uuid} from the last-conversations cache."""
    return _load_json(CACHE_DIR / "last_conversations.json")


def _conversation_metadata() -> dict:
    """Return the full conversation_metadata.json payload (or empty dict)."""
    return _load_json(CACHE_DIR / "conversation_metadata.json")


def _db_conversation_ids() -> set[str]:
    if not CONVERSATIONS_DIR.is_dir():
        return set()
    return {p.stem for p in CONVERSATIONS_DIR.glob("*.db") if p.is_file()}


def _metadata_entries() -> dict:
    payload = _conversation_metadata().get("conversations", {})
    return payload if isinstance(payload, dict) else {}


def _cwd_from_summary(summary: dict) -> Optional[str]:
    uris = summary.get("WorkspaceURIs") or []
    if not uris:
        return None
    uri = uris[0]
    if not isinstance(uri, str) or not uri:
        return None
    return uri.removeprefix("file://") if uri.startswith("file://") else uri


def _cwd_from_last_conversations(conv_id: str) -> Optional[str]:
    for ws, cid in _last_conversations().items():
        if cid == conv_id:
            return ws
    return None


def _cwd_for_conversation(conv_id: str) -> Optional[str]:
    entry = _metadata_entries().get(conv_id, {})
    summary = entry.get("summary", {}) if isinstance(entry, dict) else {}
    cwd = _cwd_from_summary(summary) if isinstance(summary, dict) else None
    return cwd or _cwd_from_last_conversations(conv_id)


def _is_under_repo(cwd: Optional[str], repo_root: str) -> bool:
    if not cwd:
        return False
    try:
        return os.path.abspath(cwd).startswith(os.path.abspath(repo_root))
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# session summary from metadata cache
# ---------------------------------------------------------------------------


def read_session(meta_or_path) -> SessionSummary:
    """Build a SessionSummary from a conversation metadata entry.

    Accepts either a conversation UUID (str) or a Path (for interface
    compatibility with claude_store / codex_store). For agy the path is
    the SQLite db under conversations/<uuid>.db, from which we derive the id.
    """
    if isinstance(meta_or_path, Path):
        # Derive conversation id from the db filename.
        conv_id = meta_or_path.stem
    else:
        conv_id = str(meta_or_path)

    meta = _metadata_entries()
    entry = meta.get(conv_id, {})
    summary = entry.get("summary", {}) if isinstance(entry, dict) else {}

    session_id = summary.get("ID") or conv_id or None
    db_path = CONVERSATIONS_DIR / f"{conv_id}.db"
    metadata_present = bool(entry)
    title = summary.get("Title") or None
    if not title:
        title = f"Antigravity {conv_id[:8]}" if conv_id else None
    last_ts = _parse_ts(entry.get("last_modified_time")) or _file_mtime(db_path)
    turn_count = summary.get("NumSteps") or 0

    # Resolve cwd from workspace URIs or last_conversations.
    cwd = _cwd_from_summary(summary) or _cwd_from_last_conversations(conv_id)
    last_blurb = summary.get("Preview") or ""
    if not metadata_present:
        last_blurb = "Antigravity metadata cache is incomplete for this conversation."

    return SessionSummary(
        session_id=session_id,
        jsonl_path=db_path,
        cwd=cwd,
        git_branch=None,
        version=None,
        title=title,
        last_ts=last_ts,
        last_role=None,
        last_stop_reason=None,
        last_blurb=last_blurb,
        turn_count=turn_count,
        total_cost_usd=None,
        harness="agy",
        provider="google",
    )


# ---------------------------------------------------------------------------
# discovery interface (mirrors claude_store / codex_store)
# ---------------------------------------------------------------------------


def all_sessions_for_repo(repo_root: str) -> list[SessionSummary]:
    """Return all agy sessions whose workspace falls under repo_root.

    agy's cache files have different jobs: conversations/*.db is the durable
    session existence store, conversation_metadata.json enriches cards, and
    last_conversations.json only records the latest conversation per workspace.
    Do not treat last_conversations.json as a complete session index.
    """
    candidate_ids = set(_last_conversations().values())
    candidate_ids.update(_metadata_entries().keys())
    candidate_ids.update(_db_conversation_ids())
    sessions = []
    for conv_id in candidate_ids:
        if not _is_under_repo(_cwd_for_conversation(conv_id), repo_root):
            continue
        try:
            s = read_session(conv_id)
        except Exception:
            continue
        if not s.session_id:
            continue
        sessions.append(s)
    sessions.sort(
        key=lambda s: s.last_ts or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return sessions


def existing_session_ids_for_cwd(cwd: str) -> set[str]:
    """Return conversation ids whose workspace matches exactly `cwd`."""
    cwd_abs = os.path.abspath(cwd)
    out: set[str] = set()
    for conv_id in (
        _db_conversation_ids()
        | set(_metadata_entries().keys())
        | set(_last_conversations().values())
    ):
        conv_cwd = _cwd_for_conversation(conv_id)
        if not conv_cwd:
            continue
        try:
            if os.path.abspath(conv_cwd) == cwd_abs:
                out.add(conv_id)
        except (ValueError, OSError):
            continue
    return out


def newest_session_id_for_cwd(
    cwd: str, exclude: Optional[set[str]] = None
) -> Optional[str]:
    """Return the most recently modified conversation id for `cwd`."""
    exclude = exclude or set()
    cwd_abs = os.path.abspath(cwd)
    candidate_ids: set[str] = set()
    for conv_id in (
        _db_conversation_ids()
        | set(_metadata_entries().keys())
        | set(_last_conversations().values())
    ):
        if conv_id in exclude:
            continue
        conv_cwd = _cwd_for_conversation(conv_id)
        if not conv_cwd:
            continue
        try:
            if os.path.abspath(conv_cwd) == cwd_abs:
                candidate_ids.add(conv_id)
        except (ValueError, OSError):
            continue
    if not candidate_ids:
        return None

    meta = _metadata_entries()
    best_id = None
    best_ts = datetime.min.replace(tzinfo=timezone.utc)
    for cid in candidate_ids:
        entry = meta.get(cid, {}) if isinstance(meta, dict) else {}
        ts = _parse_ts(entry.get("last_modified_time")) or _file_mtime(
            CONVERSATIONS_DIR / f"{cid}.db"
        )
        if ts and ts >= best_ts:
            best_ts = ts
            best_id = cid
    return best_id


def find_session_path(session_id: str) -> Optional[Path]:
    """Return the SQLite db path for a session id, if it exists."""
    if not session_id:
        return None
    db_path = CONVERSATIONS_DIR / f"{session_id}.db"
    return db_path if db_path.is_file() else None


# ---------------------------------------------------------------------------
# transcript readers (ADR-0011 — regex-based protobuf extraction)
# ---------------------------------------------------------------------------

# Step type → role mapping (reverse-engineered from live agy DBs, ADR-0011 §1).
_STEP_USER = 14
_STEP_ASSISTANT = 15
_STEP_TOOL_TYPES: set[int] = {5, 7, 8, 9, 21}
_STEP_TOOL_RESULT = 17
_STEP_SYSTEM_TYPES: set[int] = {23, 98}

# Agy tool name → canonical ADR-0006 tool name (view=files compatibility).
_TOOL_NAME_MAP: dict[str, str] = {
    "view_file": "Read",
    "replace_file_content": "Edit",
    "list_dir": "Bash",
    "grep_search": "Bash",
    "run_command": "Bash",
}


def _clean_protobuf(data: bytes) -> str:
    """Decode protobuf BLOB, strip wire-format control bytes, collapse whitespace."""
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_metadata_noise(text: str) -> str:
    """Remove UUIDs, hex tokens, session IDs, and file URIs from extracted text."""
    text = re.sub(
        r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b",
        "",
        text,
    )
    text = re.sub(r"\b[a-f0-9]{20,}\b", "", text)
    text = re.sub(r"sessionID\s*-?\d+", "", text)
    text = re.sub(r"\(bot-[a-f0-9-]+\)", "", text)
    text = re.sub(r"file:///\S+", "", text)
    # Strip trailing protobuf wire-format noise.
    text = re.sub(r'\s+[bB]?"?\s*$', "", text)
    # Strip trailing b" after final quoted segment (common protobuf artifact).
    text = re.sub(r'"\s+b"\s*$', "", text)
    # Remove leading protobuf junk (non-letter, non-Thai chars before first word).
    text = re.sub(r"^[^A-Za-zก-๙0-9\*\[\]\#]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _longest_natural_segment(text: str, min_len: int = 15) -> str:
    """Return the longest segment that looks like natural language (has letters)."""
    parts = re.findall(
        r"[\w\s\.\,\!\?\-\:\;\(\)\[\]\{\}\/\@\#\*\"\'\\\`\~\>\<\&\^\$\+\=฀-๿]{"
        + str(min_len)
        + r",}",
        text,
    )
    for p in sorted(parts, key=len, reverse=True):
        candidate = p.strip()
        if re.search(r"[A-Za-zก-๙]", candidate) and not re.match(
            r"^[a-f0-9\s\-]{20,}$", candidate
        ):
            return candidate
    return ""


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from text. Returns {} on failure."""
    m = re.search(r"\{[^{}]{10,}\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _extract_user_text(payload: bytes) -> str:
    """Extract user message text from a type-14 step_payload BLOB."""
    text = _clean_protobuf(payload)
    text = _strip_metadata_noise(text)
    content = _longest_natural_segment(text, min_len=5)
    if content:
        # Post-process: strip protobuf artifacts that survive extraction.
        content = content.replace(' b"', " ")
        content = re.sub(r'"\s+b\s*$', '"', content)
        content = content.strip('" \t')
    return content or text


def _extract_assistant_text(payload: bytes) -> str:
    """Extract assistant response text from a type-15 step_payload BLOB.

    Type 15 payloads carry either (a) a JSON tool-summary preamble before a
    tool call, or (b) natural-language analysis/response text.  Both are
    surfaced as text content blocks.
    """
    text = _clean_protobuf(payload)

    # Try JSON toolSummary first — agy wraps tool announcements in JSON.
    js = _extract_json(text)
    if js:
        summary = js.get("toolSummary") or js.get("toolAction") or ""
        if summary:
            return summary

    # Fall back to natural-language extraction after stripping metadata.
    text = _strip_metadata_noise(text)
    content = _longest_natural_segment(text, min_len=10)
    return content or text


def _extract_tool_meta(metadata: bytes) -> tuple[str, dict]:
    """Extract canonical tool name + input args from a tool-step metadata BLOB.

    Input keys are normalised to the canonical ADR-0006 names so that
    ``file-refs.ts`` (view=files) can extract file references from agy sessions.
    """
    text = _clean_protobuf(metadata)
    # Tool name is embedded as a protobuf string before the JSON block.
    m = re.search(
        r"(view_file|list_dir|replace_file_content|grep_search|run_command)",
        text,
    )
    raw_name = m.group(1) if m else "unknown"
    name = _TOOL_NAME_MAP.get(raw_name, raw_name)
    args = _extract_json(text)

    # Normalise file-path keys so file-refs.ts can find them.
    if "AbsolutePath" in args:
        args["file_path"] = args.pop("AbsolutePath")
    elif "filePath" in args:
        args["file_path"] = args.pop("filePath")

    return name, args


def _extract_tool_result(payload: bytes) -> str:
    """Extract output text from a type-17 (tool_result) step_payload."""
    text = _clean_protobuf(payload)
    text = _strip_metadata_noise(text)
    content = _longest_natural_segment(text, min_len=10)
    return content or text


def _read_steps(db_path: Path) -> list[dict]:
    """Return all steps from an agy SQLite DB as raw column dicts."""
    if not db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT idx, step_type, metadata, step_payload FROM steps ORDER BY idx"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except (sqlite3.Error, OSError):
        return []


def read_messages(
    db_path: Path,
    limit: Optional[int] = None,
    since: Optional[str] = None,
) -> list[dict]:
    """Read plain-text turns from an agy SQLite conversation DB (ADR-0011).

    Returns ``[{role, text, ts}]`` — same contract as claude_store.read_messages().
    Tool steps produce ``[tool: Name]`` summary text.
    """
    steps = _read_steps(db_path)
    if not steps:
        return []

    # since filter: compare synthetic idx-as-ts
    since_idx = -1
    if since and since.startswith("step-"):
        try:
            since_idx = int(since.split("-")[1])
        except (ValueError, IndexError):
            pass

    msgs: list[dict] = []
    for row in steps:
        idx: int = row["idx"]
        if since_idx >= 0 and idx <= since_idx:
            continue
        st: int = row["step_type"]
        payload: Optional[bytes] = row["step_payload"]
        meta: Optional[bytes] = row["metadata"]
        ts = f"step-{idx:04d}"

        if st == _STEP_USER and payload:
            text = _extract_user_text(payload)
            if text:
                msgs.append({"role": "user", "text": text, "ts": ts})
        elif st == _STEP_ASSISTANT and payload:
            text = _extract_assistant_text(payload)
            if text:
                msgs.append({"role": "assistant", "text": text, "ts": ts})
        elif st in _STEP_TOOL_TYPES and meta:
            name, args = _extract_tool_meta(meta)
            msgs.append({"role": "assistant", "text": f"[tool: {name}]", "ts": ts})
        elif st == _STEP_TOOL_RESULT and payload:
            text = _extract_tool_result(payload)
            if text:
                msgs.append({"role": "assistant", "text": text, "ts": ts})

    if limit is not None and limit > 0 and len(msgs) > limit:
        return msgs[-limit:]
    return msgs


def read_messages_rich(
    db_path: Path,
    limit: Optional[int] = None,
    since: Optional[str] = None,
) -> list[dict]:
    """Read rich structured-content turns from an agy SQLite conversation DB (ADR-0011).

    Returns ``[{role, ts, content: [{type, ...}]}]`` — same contract as
    claude_store.read_messages_rich() per ADR-0006.  Fidelity is degraded:
    no ``model``, ``stop_reason``, ``usage``, or ``thinking`` blocks.
    Tool names are normalised to the canonical set (Read / Edit / Bash).
    """
    steps = _read_steps(db_path)
    if not steps:
        return []

    since_idx = -1
    if since and since.startswith("step-"):
        try:
            since_idx = int(since.split("-")[1])
        except (ValueError, IndexError):
            pass

    msgs: list[dict] = []
    # Track the last tool_use id per step to correlate with tool_results.
    last_tool_id: Optional[str] = None

    for row in steps:
        idx: int = row["idx"]
        if since_idx >= 0 and idx <= since_idx:
            continue
        st: int = row["step_type"]
        payload: Optional[bytes] = row["step_payload"]
        meta: Optional[bytes] = row["metadata"]
        ts = f"step-{idx:04d}"

        if st in _STEP_SYSTEM_TYPES:
            continue

        if st == _STEP_USER and payload:
            text = _extract_user_text(payload)
            if text:
                msgs.append(
                    {
                        "role": "user",
                        "ts": ts,
                        "content": [{"type": "text", "text": text}],
                    }
                )

        elif st == _STEP_ASSISTANT and payload:
            text = _extract_assistant_text(payload)
            if text:
                msgs.append(
                    {
                        "role": "assistant",
                        "ts": ts,
                        "content": [{"type": "text", "text": text}],
                    }
                )

        elif st in _STEP_TOOL_TYPES and meta:
            name, args = _extract_tool_meta(meta)
            tool_id = f"agy-step-{idx:04d}"
            last_tool_id = tool_id
            msgs.append(
                {
                    "role": "assistant",
                    "ts": ts,
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": args,
                        }
                    ],
                }
            )

        elif st == _STEP_TOOL_RESULT and payload:
            text = _extract_tool_result(payload)
            content_val = text if text else ""
            msgs.append(
                {
                    "role": "assistant",
                    "ts": ts,
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": last_tool_id or "",
                            "content": content_val,
                        }
                    ],
                }
            )

    if limit is not None and limit > 0 and len(msgs) > limit:
        return msgs[-limit:]
    return msgs
