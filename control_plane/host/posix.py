"""POSIX Host Runtime (ADR-0052 SD1).

The `pty`/`fcntl`/`termios`/`signal` behavior that used to live directly in
`terminal.py`, moved here verbatim. Those modules do not exist on Windows,
so every one of them is imported lazily inside the method that needs it —
never at module scope — so this module (and `control_plane.terminal`, which
now reaches it only through `control_plane.host`) stays importable on a
platform that lacks them.
"""

from __future__ import annotations

import os
import shutil
import struct
from typing import Optional


def _winsz(rows: int, cols: int) -> bytes:
    """Pack a `struct winsize` — unsigned short rows x cols x xpix x ypix."""
    return struct.pack("HHHH", rows, cols, 0, 0)


class PosixHost:
    def spawn_pty(
        self,
        argv: list[str],
        cwd: Optional[str],
        env: Optional[dict],
        rows: int,
        cols: int,
    ) -> tuple[int, int]:
        import fcntl
        import pty
        import termios

        pid, fd = pty.fork()
        if pid == 0:
            # Child: apply env overrides, set terminal size, chdir, exec.
            if env:
                for key, val in env.items():
                    os.environ[key] = val
            try:
                fcntl.ioctl(0, termios.TIOCSWINSZ, _winsz(rows, cols))
            except OSError:
                pass
            if cwd:
                try:
                    os.chdir(cwd)
                except OSError:
                    pass
            os.execvp(argv[0], argv)
            os._exit(127)  # exec failed

        # Parent: set master pty size too.
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, _winsz(rows, cols))
        except OSError:
            pass
        return fd, pid

    def resize(self, fd: int, rows: int, cols: int) -> None:
        import fcntl
        import termios

        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, _winsz(rows, cols))
        except OSError:
            pass

    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def terminate(self, pid: int) -> None:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    def reap(self, pid: int) -> int:
        try:
            _, status = os.waitpid(pid, 0)
        except OSError:
            return -1
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        return -1

    def resolve_executable(self, name: str) -> str:
        return shutil.which(name) or name
