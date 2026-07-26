"""Session discovery (v2) — scan harness sessions, build cards.

v2 pivot: card = session, not task branch. Discovery scans all sessions under
the repo root via all_sessions_for_repo(), overlays archive state, and returns
cards sorted by last_ts descending. No worktree/PR union needed — branch badge
is an optional overlay from gh.

v3 (2026-07-19): TTL cache + mtime optimisation (ADR-0010). discover_cached()
returns the last result within _CACHE_TTL seconds; invalidate_cache() is called
on PTY spawn/kill. all_sessions_for_repo() caches SessionSummary by jsonl mtime
so idle sessions skip the full parse.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from . import agy_store, archive, claude_store, codex_store, gh, health, state

# --- discovery cache (ADR-0010 §1) -------------------------------------------

_CACHE_TTL = 3.0  # seconds — shorter than the 5s poll interval
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, List[SessionCard]]] = {}


def invalidate_cache(repo_root: str | None = None) -> None:
    """Clear the discovery cache. Called on PTY spawn/kill.
    If repo_root is None, clears all cached repos."""
    with _CACHE_LOCK:
        if repo_root is None:
            _CACHE.clear()
        else:
            _CACHE.pop(repo_root, None)


@dataclass
class SessionCard:
    session_id: Optional[str]
    title: Optional[str]
    activity: str  # Working | Awaiting | Blocked | Idle
    last_ts: Optional[str]  # ISO-8601
    last_role: Optional[str]
    turn_count: int
    git_branch: Optional[str]
    total_cost_usd: Optional[float]
    # Cost state (ADR-0022 §SD2/§SD4). `unpriced_models` non-empty with a null
    # total means the board renders `unpriced` — distinct from both a figure
    # and `$0.00`. With a total, it means the figure is a priced subtotal and
    # `cost_partial` is set.
    cost_partial: bool = False
    unpriced_models: Optional[List[str]] = None
    harness: Optional[str] = "claude"
    provider: Optional[str] = "claude"
    worktree_path: Optional[str] = None
    # Archive state
    dismissed: bool = False
    auto_archived: bool = False
    dismissed_at: Optional[str] = None
    # PR badge (optional overlay)
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    pr_state: Optional[str] = None
    merged_pr: Optional[str] = None
    note_kind: Optional[str] = None
    note_text: Optional[str] = None
    note_code: Optional[str] = None
    note_tail: Optional[str] = None
    # Transcript path
    jsonl_path: Optional[str] = None
    # Health score (Branch C, ADR-0016)
    health: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


def discover(repo_root: str) -> List[SessionCard]:
    """Build the session-backed kanban. Returns cards sorted by last_ts desc."""
    now = datetime.now(timezone.utc)
    sessions = claude_store.all_sessions_for_repo(repo_root)
    sessions.extend(codex_store.all_sessions_for_repo(repo_root))
    sessions.extend(agy_store.all_sessions_for_repo(repo_root))

    # Auto-archive idle sessions > 7d (D10)
    archive.auto_archive_candidates(sessions, repo_root, now)

    # Load archive + dismiss state
    archive_entries = archive.load(repo_root)

    # Branch → PR mapping for badge overlay (non-blocking: served from cache,
    # refreshed in the background so /state never waits on a `gh` subprocess).
    prs = gh.list_prs_cached(repo_root)
    pr_by_branch = gh.by_branch(prs)

    cards = [_summary_to_card(s, now, archive_entries, pr_by_branch) for s in sessions]
    cards.sort(key=lambda c: c.last_ts or "", reverse=True)
    return cards


def _summary_to_card(s, now, archive_entries, pr_by_branch) -> SessionCard:
    """Build a SessionCard from a SessionSummary with archive + PR overlays.
    Shared by discover() (bulk) and card_for_session() (single, ADR-0010 §2)."""
    entry = archive_entries.get(s.session_id or "")
    dismissed = bool(entry and entry.dismissed)
    auto_archived = bool(entry and entry.auto)

    activity = state.infer_activity(s, now)

    # Branch badge overlay
    pr = pr_by_branch.get(s.git_branch) if s.git_branch else None
    note_kind = None
    note_text = None
    note_tail = None
    if s.harness == "agy" and "metadata cache is incomplete" in (s.last_blurb or ""):
        note_kind = "metadata"
        note_text = "Metadata pending"
        note_tail = " — agy cache has the conversation DB but no metadata entry yet"

    # Health score (Branch C, ADR-0016)
    health_score = None
    if s.jsonl_path:
        hs = health.session_health(
            jsonl_path=str(s.jsonl_path),
            last_ts=s.last_ts.isoformat() if s.last_ts else None,
            harness=s.harness or "claude",
            now=now,
        )
        if hs is not None:
            health_score = {
                "status": hs.status,
                "stale": hs.stale,
                "loop": hs.loop,
                "error": hs.error,
                "stale_hrs": hs.stale_hrs,
                "loop_count": hs.loop_count,
                "error_count": hs.error_count,
                "error_total": hs.error_total,
            }

    return SessionCard(
        session_id=s.session_id,
        title=s.title,
        activity=activity,
        last_ts=s.last_ts.isoformat() if s.last_ts else None,
        last_role=s.last_role,
        turn_count=s.turn_count,
        git_branch=s.git_branch,
        total_cost_usd=s.total_cost_usd,
        # getattr: codex/agy build the shared SessionSummary but predate these
        # fields on their own construction paths.
        cost_partial=bool(getattr(s, "cost_partial", False)),
        unpriced_models=list(getattr(s, "unpriced_models", None) or []),
        harness=s.harness,
        provider=s.provider,
        worktree_path=s.cwd,
        dismissed=dismissed,
        auto_archived=auto_archived,
        dismissed_at=entry.dismissed_at if entry else None,
        pr_number=pr.number if pr else None,
        pr_url=pr.url if pr else None,
        pr_state=pr.state if pr else None,
        note_kind=note_kind,
        note_text=note_text,
        note_tail=note_tail,
        jsonl_path=str(s.jsonl_path) if s.jsonl_path else None,
        health=health_score,
    )


def card_for_session(session_id: str, repo_root: str) -> Optional[SessionCard]:
    """Direct single-session card lookup (ADR-0010 §2) — resolve the summary via
    each store's exact-match find_session_path, overlay archive state, and build
    one card. No full discovery scan. Returns None when no store owns the id
    (caller may fall back to the cached scan).

    The PR-badge overlay is intentionally skipped here: this path runs on every
    transcript poll and its callers (server._resolve_session_runtime /
    _transcript_source) read only harness / provider / cwd / jsonl_path — not the
    PR fields — so it must not spend a `gh` subprocess per poll."""
    for store_mod in (claude_store, codex_store, agy_store):
        path = store_mod.find_session_path(session_id)
        if path is None:
            continue
        # Prefer the mtime-cached reader where a store has one (claude also
        # resolves provider there); agy's read_session is metadata-based + cheap.
        reader = getattr(store_mod, "_read_session_cached", store_mod.read_session)
        s = reader(path)
        if s.session_id != session_id:
            continue
        now = datetime.now(timezone.utc)
        archive_entries = archive.load(repo_root)
        return _summary_to_card(s, now, archive_entries, pr_by_branch={})
    return None


def discover_cached(repo_root: str, ttl: float = _CACHE_TTL) -> List[SessionCard]:
    """Return cached cards if fresh, else recompute + cache (ADR-0010 §1).

    Thread-safe: only one thread computes; concurrent callers wait for the
    result rather than duplicating the scan.
    """
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(repo_root)
        if entry is not None:
            cached_time, cards = entry
            if now - cached_time < ttl:
                return cards

    cards = discover(repo_root)
    with _CACHE_LOCK:
        _CACHE[repo_root] = (time.time(), cards)
    return cards
