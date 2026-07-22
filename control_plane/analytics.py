"""Cross-session file-operation analytics (v2.5).

Single public entry: files_analytics() — scans sessions within a time range
for a given harness, extracts tool_use blocks from transcript jsonl, normalizes
file paths to workspace-root-relative, and aggregates into top-files + summary
+ per-harness breakdown.

ADR-0013 § SD1 — new module, compute-on-the-fly, zero storage dependency.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import agy_store, claude_store, codex_store

# Mirror of client-side file-refs.ts FILE_TOOLS (ADR-0013 § SD4).
FILE_TOOLS: dict[str, str] = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "NotebookEdit": "edit",
    "exec_command": "edit",
    "Bash": "edit",
}

VALID_HARNESSES = frozenset({"claude", "codex", "agy"})
VALID_DAYS = frozenset({1, 7, 30})


@dataclass
class FileOp:
    path: str
    op: str  # 'read' | 'edit' | 'write'
    ts: Optional[str]


def files_analytics(
    repo_root: str,
    days: int,
    harness: str,
    now: Optional[datetime] = None,
) -> dict:
    """Return analytics response for GET /analytics/files.

    Args:
        repo_root: absolute workspace root
        days: time window (1, 7, or 30)
        harness: harness to filter by (required — one of claude/codex/agy)
        now: current time (injected for testability)
    """
    if days not in VALID_DAYS:
        raise ValueError(f"days must be one of {sorted(VALID_DAYS)}")
    if harness not in VALID_HARNESSES:
        raise ValueError(f"harness must be one of {sorted(VALID_HARNESSES)}")

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    repo_root = os.path.abspath(repo_root)

    store_map = {
        "claude": claude_store,
        "codex": codex_store,
        "agy": agy_store,
    }

    # ── per-harness stats (all sessions, all time) ──
    per_harness: dict[str, dict] = {}
    for h, store_mod in store_map.items():
        try:
            all_s = store_mod.all_sessions_for_repo(repo_root)
        except Exception:
            all_s = []
        in_range = [s for s in all_s if s.last_ts and s.last_ts >= cutoff]
        per_harness[h] = {
            "sessions": len(in_range),
            "operations": 0,
            "unique_files": 0,
        }

    # ── detailed scan: selected harness only ──
    store_mod = store_map[harness]
    try:
        all_sessions = store_mod.all_sessions_for_repo(repo_root)
    except Exception:
        all_sessions = []
    sessions_in_range = [s for s in all_sessions if s.last_ts and s.last_ts >= cutoff]

    file_ops: list[FileOp] = []
    session_ids_with_ops: set[str] = set()

    for session in sessions_in_range:
        if not session.jsonl_path or not session.jsonl_path.is_file():
            continue
        cwd = session.cwd or repo_root
        try:
            messages = store_mod.read_messages_rich(session.jsonl_path)
        except Exception:
            continue
        ops = _extract_file_ops(messages)
        if ops:
            session_ids_with_ops.add(session.session_id or "")
        for op in ops:
            normalized = _normalize_path(op.path, cwd, repo_root)
            if normalized is None:
                continue
            file_ops.append(FileOp(path=normalized, op=op.op, ts=op.ts))

    # ── aggregate top files ──
    agg: dict[str, dict] = {}  # path → {reads, edits, writes, sessions, harnesses}
    for fo in file_ops:
        entry = agg.setdefault(
            fo.path,
            {
                "reads": 0,
                "edits": 0,
                "writes": 0,
                "sessions": set(),
                "harnesses": defaultdict(int),
            },
        )
        if fo.op == "read":
            entry["reads"] += 1
        elif fo.op == "edit":
            entry["edits"] += 1
        elif fo.op == "write":
            entry["writes"] += 1
        entry["harnesses"][harness] += 1

    # sessions-per-file — approximate from session_ids_with_ops (total sessions
    # that had at least one file op in the time range). We cannot pin each
    # FileOp to its session without threading session_id through, so we use
    # the total count. The per-file session count is therefore the same for
    # all files — the number of sessions that touched files at all.
    #
    # For a more precise count (sessions per specific file), we'd need to
    # thread session_id through _extract_file_ops. Deferred to caching (D4).
    total_active_sessions = len(session_ids_with_ops)

    # Build top_files list
    top_files = []
    for path, entry in agg.items():
        total_ops = entry["reads"] + entry["edits"] + entry["writes"]
        top_files.append(
            {
                "path": path,
                "total_ops": total_ops,
                "reads": entry["reads"],
                "edits": entry["edits"],
                "writes": entry["writes"],
                "sessions": total_active_sessions,
                "harnesses": dict(entry["harnesses"]),
            }
        )

    top_files.sort(key=lambda f: f["total_ops"], reverse=True)
    top_files = top_files[:50]

    # ── summary ──
    total_operations = sum(f["total_ops"] for f in top_files)
    unique_files = len(agg)

    # Update per-harness entry for the selected harness with detailed counts.
    per_harness[harness]["operations"] = total_operations
    per_harness[harness]["unique_files"] = unique_files

    return {
        "generated_at": now.isoformat(),
        "repo": repo_root,
        "days": days,
        "harness": harness,
        "summary": {
            "total_sessions": len(sessions_in_range),
            "total_operations": total_operations,
            "unique_files": unique_files,
        },
        "per_harness": per_harness,
        "top_files": top_files,
    }


def _extract_file_ops(messages: list[dict]) -> list[FileOp]:
    """Extract file operations from rich transcript messages.

    Scans tool_use content blocks whose tool name matches FILE_TOOLS.
    Returns operations ordered by timestamp (ascending).
    ADR-0013 § SD4 — mirrors client-side file-refs.ts extractFileRefs().
    """
    ops: list[FileOp] = []
    for msg in messages:
        for block in msg.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            op = FILE_TOOLS.get(tool_name)
            if not op:
                continue
            fp = _file_path_from_input(block.get("input"))
            if not fp:
                continue
            ops.append(FileOp(path=fp, op=op, ts=msg.get("ts")))
    return ops


def _file_path_from_input(input_data: dict | None) -> Optional[str]:
    """Extract file path from a tool_use input dict."""
    if not isinstance(input_data, dict):
        return None
    fp = input_data.get("file_path")
    if isinstance(fp, str) and fp:
        return fp
    np = input_data.get("notebook_path")
    if isinstance(np, str) and np:
        return np
    return None


def _normalize_path(
    file_path: str,
    session_cwd: str,
    workspace_root: str,
) -> Optional[str]:
    """Resolve a tool_use file_path to workspace-root-relative.

    ADR-0013 § SD2:
    - Absolute path → relativize against workspace_root.
    - Relative path → join with session cwd → relativize.
    - Paths resolving outside workspace → None (excluded).
    """
    if os.path.isabs(file_path):
        resolved = file_path
    else:
        resolved = os.path.join(session_cwd, file_path)

    # Resolve symlinks/.. so relpath produces a clean result.
    try:
        resolved = os.path.realpath(resolved)
    except OSError:
        return None

    try:
        rel = os.path.relpath(resolved, workspace_root)
    except ValueError:
        return None  # different drives on Windows — not applicable but safe

    if rel.startswith(".."):
        return None  # outside workspace
    return rel
