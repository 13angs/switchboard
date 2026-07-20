"""Single-writer guard — external-holder probe (S7, restored v2.3).

Restored (trimmed) per adr-orchestrator-single-writer-guard-2026-07.md:
only the external-holder /proc probe returns. The .orchestrator.lock turn-lock
file stays deleted — the in-memory _registry covers same-instance dedup.

Best-effort, Linux /proc only; /proc absent -> returns None (no holder detected)
rather than blocking legitimately.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class SessionBusy(Exception):
    """Raised when a session cannot be acquired because another live process
    (native claude or cross-instance) already holds it."""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cmdline_tokens(entry: Path) -> list[str]:
    raw = (entry / "cmdline").read_bytes()
    return [p.decode(errors="replace") for p in raw.split(b"\x00") if p]


def _process_matches_harness(comm: str, argv: list[str], harness_name: str) -> bool:
    expected = harness_name.strip().lower()
    names = [comm, *(Path(arg).name for arg in argv)]
    return any(name.strip().lower() == expected for name in names)


def _process_matches_session(argv: list[str], session_id: Optional[str]) -> bool:
    if not session_id:
        return True
    return any(arg == session_id or arg == f"--resume={session_id}" for arg in argv)


def external_holder(
    cwd: str,
    session_id: Optional[str] = None,
    harness_name: str = "claude",
) -> int | None:
    """Return the PID of a live harness process holding this exact session.

    Older versions guarded by cwd only. That blocked every session in a shared
    worktree and also false-matched paths containing `.claude`. When a resume
    has a session id, only a process whose argv names that same session is a
    proven external writer.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    target = str(Path(cwd).resolve())
    me = os.getpid()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            proc_cwd = os.readlink(entry / "cwd")
            if str(Path(proc_cwd)) != target:
                continue
            comm = (entry / "comm").read_text(errors="replace").strip()
            argv = _cmdline_tokens(entry)
        except (OSError, PermissionError):
            continue
        if not _process_matches_harness(comm, argv, harness_name):
            continue
        if _process_matches_session(argv, session_id):
            return pid
    return None
