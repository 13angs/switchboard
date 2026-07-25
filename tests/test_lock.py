#!/usr/bin/env python3
"""Single-writer external-holder checks.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_lock.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from control_plane import lock  # noqa: E402


def _fake_process(proc_root: Path, pid: int, cwd: Path, comm: str, argv: list[str]) -> None:
    proc_dir = proc_root / str(pid)
    proc_dir.mkdir()
    (proc_dir / "cwd").symlink_to(cwd)
    (proc_dir / "comm").write_text(comm, encoding="utf-8")
    (proc_dir / "cmdline").write_bytes(b"\x00".join(a.encode() for a in argv) + b"\x00")


@contextmanager
def _fake_proc(proc_root: Path):
    real_path = lock.Path

    def fake_path(value):
        if value == "/proc":
            return proc_root
        return real_path(value)

    with patch.object(lock, "Path", fake_path):
        yield


def test_external_holder_ignores_claude_path_without_matching_session():
    with tempfile.TemporaryDirectory(prefix="orch-lock-") as tmp:
        root = Path(tmp)
        cwd = root / ".claude" / "worktrees" / "task-a"
        cwd.mkdir(parents=True)
        proc_root = root / "proc"
        proc_root.mkdir()
        _fake_process(
            proc_root,
            123,
            cwd,
            "python3",
            ["python3", str(cwd / "tools" / "runner.py")],
        )

        with _fake_proc(proc_root):
            holder = lock.external_holder(
                str(cwd),
                session_id="target-session",
                harness_name="claude",
            )

        assert holder is None


def test_external_holder_matches_same_resume_session():
    with tempfile.TemporaryDirectory(prefix="orch-lock-") as tmp:
        root = Path(tmp)
        cwd = root / "repo"
        cwd.mkdir()
        proc_root = root / "proc"
        proc_root.mkdir()
        _fake_process(
            proc_root,
            456,
            cwd,
            "claude",
            ["claude", "--resume", "target-session"],
        )

        with _fake_proc(proc_root):
            holder = lock.external_holder(
                str(cwd),
                session_id="target-session",
                harness_name="claude",
            )

        assert holder == 456


def test_external_holder_matches_wrapper_invoked_resume_session():
    with tempfile.TemporaryDirectory(prefix="orch-lock-") as tmp:
        root = Path(tmp)
        cwd = root / "repo"
        cwd.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        claude_bin = bin_dir / "claude"
        claude_bin.touch()
        proc_root = root / "proc"
        proc_root.mkdir()
        _fake_process(
            proc_root,
            567,
            cwd,
            "node",
            ["node", str(claude_bin), "--resume", "target-session"],
        )

        with _fake_proc(proc_root):
            holder = lock.external_holder(
                str(cwd),
                session_id="target-session",
                harness_name="claude",
            )

        assert holder == 567


def test_external_holder_matches_same_codex_resume_session():
    with tempfile.TemporaryDirectory(prefix="orch-lock-") as tmp:
        root = Path(tmp)
        cwd = root / "repo"
        cwd.mkdir()
        proc_root = root / "proc"
        proc_root.mkdir()
        _fake_process(
            proc_root,
            789,
            cwd,
            "codex",
            ["codex", "--no-alt-screen", "-C", str(cwd), "resume", "target-session"],
        )

        with _fake_proc(proc_root):
            holder = lock.external_holder(
                str(cwd),
                session_id="target-session",
                harness_name="codex",
            )

        assert holder == 789


def _run():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {e!r}")
    print(f"\n{'PASS' if failures == 0 else 'FAIL'} - {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
