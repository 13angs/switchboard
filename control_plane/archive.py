"""ArchiveLedger — machine-local dismiss + auto-archive state.

Each dismissed/archived session is recorded in
~/.claude/orchestrator/<repo-hash>-archive.json. The ledger is read on startup;
dismissed sessions are excluded from the active kanban view and shown only when
the archive toggle is on.

Auto-archive rule: sessions idle > 7 days that haven't been explicitly dismissed
are auto-appended on the next /state poll.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from . import config

ARCHIVE_DIR = config.ARCHIVE_DIR
AUTO_ARCHIVE_DAYS = 7  # D10: idle > 7 days → auto-archive


@dataclass
class ArchiveEntry:
    dismissed: bool
    dismissed_at: str  # ISO-8601
    auto: bool = False  # True when auto-archived (idle > 7d)


def _repo_hash(repo_root: str) -> str:
    """Stable short hash of the repo root path for the ledger filename."""
    return hashlib.sha256(os.path.abspath(repo_root).encode()).hexdigest()[:12]


def _ledger_path(repo_root: str) -> Path:
    return ARCHIVE_DIR / f"{_repo_hash(repo_root)}-archive.json"


def load(repo_root: str) -> Dict[str, ArchiveEntry]:
    """Load archive entries, keyed by session_id."""
    path = _ledger_path(repo_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    entries: Dict[str, ArchiveEntry] = {}
    for sid, raw in data.items():
        if not isinstance(raw, dict):
            continue
        entries[sid] = ArchiveEntry(
            dismissed=raw.get("dismissed", True),
            dismissed_at=raw.get("dismissed_at", ""),
            auto=raw.get("auto", False),
        )
    return entries


def dismiss(session_id: str, repo_root: str, auto: bool = False) -> None:
    """Mark a session as dismissed/archived. Persists immediately."""
    entries = load(repo_root)
    entries[session_id] = ArchiveEntry(
        dismissed=True,
        dismissed_at=datetime.now(timezone.utc).isoformat(),
        auto=auto,
    )
    _save(repo_root, entries)


def _unique_session_ids(session_ids: list[str]) -> list[str]:
    """Return non-empty session ids once, preserving request order."""
    seen = set()
    unique = []
    for sid in session_ids:
        if not isinstance(sid, str):
            continue
        sid = sid.strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        unique.append(sid)
    return unique


def dismiss_many(session_ids: list[str], repo_root: str, auto: bool = False) -> list[str]:
    """Mark many sessions as dismissed/archived with one ledger write.

    Returns the unique session ids accepted from the request, preserving order.
    Existing entries are refreshed to match single-session dismiss semantics.
    """
    unique = _unique_session_ids(session_ids)
    if not unique:
        return []

    entries = load(repo_root)
    dismissed_at = datetime.now(timezone.utc).isoformat()
    for sid in unique:
        entries[sid] = ArchiveEntry(
            dismissed=True,
            dismissed_at=dismissed_at,
            auto=auto,
        )
    _save(repo_root, entries)
    return unique


def undismiss(session_id: str, repo_root: str) -> None:
    """Remove a session from the archive ledger (un-dismiss)."""
    entries = load(repo_root)
    entries.pop(session_id, None)
    _save(repo_root, entries)


def undismiss_many(session_ids: list[str], repo_root: str) -> list[str]:
    """Remove many sessions from the archive ledger with one write.

    Returns the ids that were present and removed, preserving request order.
    """
    unique = _unique_session_ids(session_ids)
    if not unique:
        return []

    entries = load(repo_root)
    removed = []
    for sid in unique:
        if sid in entries:
            entries.pop(sid, None)
            removed.append(sid)
    if removed:
        _save(repo_root, entries)
    return removed


def _save(repo_root: str, entries: Dict[str, ArchiveEntry]) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    data = {sid: asdict(e) for sid, e in entries.items()}
    _ledger_path(repo_root).write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
    )


def auto_archive_candidates(
    sessions: list,  # list[SessionSummary]
    repo_root: str,
    now: Optional[datetime] = None,
) -> int:
    """Auto-archive sessions idle > AUTO_ARCHIVE_DAYS. Returns count archived.

    Only archives sessions that are not already in the ledger (explicit dismiss
    already handled; this catches sessions that went idle and were never touched).
    """
    now = now or datetime.now(timezone.utc)
    entries = load(repo_root)
    threshold = AUTO_ARCHIVE_DAYS * 86400.0  # seconds
    dismissed_at = now.isoformat()
    count = 0
    for s in sessions:
        if s.session_id is None:
            continue
        if s.session_id in entries:
            continue  # already archived/dismissed
        age = s.age_seconds(now)
        if age is not None and age > threshold:
            # Mutate in memory; persist once below (was load+write per candidate).
            entries[s.session_id] = ArchiveEntry(
                dismissed=True, dismissed_at=dismissed_at, auto=True
            )
            count += 1
    if count:
        _save(repo_root, entries)
    return count
