"""GhPoller — pull PR state via `gh pr list --json` (branch badge only, v2).

Read-only. Branch→PR mapping for optional badge overlay on session cards.
CI/review/merge are not tracked (no kanban columns for them in v2).
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Minimal fields — branch badge only (v2).
_FIELDS = "number,headRefName,state,title,url"


@dataclass
class PullRequest:
    number: int
    branch: str
    state: str  # OPEN | MERGED | CLOSED
    title: str
    url: str


def list_prs(repo_dir: str, states=("open", "merged")) -> List[PullRequest]:
    """Return PRs for the repo at repo_dir."""
    prs: List[PullRequest] = []
    seen = set()
    for st in states:
        try:
            out = subprocess.run(
                ["gh", "pr", "list", "--state", st, "--limit", "60", "--json", _FIELDS],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
                cwd=repo_dir,
            ).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        try:
            rows = json.loads(out) if out.strip() else []
        except json.JSONDecodeError:
            rows = []
        for r in rows:
            num = r.get("number")
            if num in seen:
                continue
            seen.add(num)
            prs.append(
                PullRequest(
                    number=num,
                    branch=r.get("headRefName", ""),
                    state=str(r.get("state", "")).upper(),
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                )
            )
    return prs


# --- non-blocking PR cache ---------------------------------------------------
# `gh pr list` spawns subprocesses + hits the network; on a hung/offline `gh`
# it could block the /state response for up to the 30s×2 timeout. PR badges are
# an optional overlay that changes slowly, so /state must never wait on `gh`.
# list_prs_cached() returns the last snapshot immediately and refreshes it in a
# background daemon thread when stale — the next poll picks up fresh data.
_PR_TTL = 30.0  # seconds a snapshot is served before a background refresh fires
_PR_CACHE_LOCK = threading.Lock()
_PR_CACHE: Dict[str, Tuple[float, List[PullRequest]]] = {}
_PR_REFRESHING: set[str] = set()


def _refresh_prs(repo_dir: str, states: Tuple[str, ...]) -> None:
    try:
        prs = list_prs(repo_dir, states)
        with _PR_CACHE_LOCK:
            _PR_CACHE[repo_dir] = (time.time(), prs)
    finally:
        with _PR_CACHE_LOCK:
            _PR_REFRESHING.discard(repo_dir)


def list_prs_cached(
    repo_dir: str, states=("open", "merged"), ttl: float = _PR_TTL
) -> List[PullRequest]:
    """Return the cached PR snapshot for repo_dir, refreshing in the background
    when stale. Never blocks on `gh` — the first call returns [] until the first
    background refresh lands (badges appear on a subsequent poll)."""
    now = time.time()
    with _PR_CACHE_LOCK:
        entry = _PR_CACHE.get(repo_dir)
        snapshot = entry[1] if entry is not None else []
        fresh = entry is not None and (now - entry[0]) < ttl
        should_refresh = not fresh and repo_dir not in _PR_REFRESHING
        if should_refresh:
            _PR_REFRESHING.add(repo_dir)
    if should_refresh:
        threading.Thread(
            target=_refresh_prs, args=(repo_dir, tuple(states)), daemon=True
        ).start()
    return snapshot


def by_branch(prs: List[PullRequest]) -> Dict[str, PullRequest]:
    """Index PRs by head branch, preferring open over merged."""
    idx: Dict[str, PullRequest] = {}
    for pr in prs:
        cur = idx.get(pr.branch)
        if cur is None or (cur.state == "MERGED" and pr.state == "OPEN"):
            idx[pr.branch] = pr
    return idx
