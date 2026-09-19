"""Host Runtime boundary (ADR-0052 SD1).

`control_plane/terminal.py` talks only to this module's `HostRuntime`
protocol; the choice between `PosixHost` and `WindowsHost` happens once,
here, by `platform.system()`. No other module branches on platform.
"""

from __future__ import annotations

import platform
from typing import Optional, Protocol


class HostRuntime(Protocol):
    """The process/PTY operations `terminal.py` needs from the host OS."""

    def spawn_pty(
        self,
        argv: list[str],
        cwd: Optional[str],
        env: Optional[dict],
        rows: int,
        cols: int,
    ) -> tuple[int, int, int]:
        """Start `argv` attached to a new pseudo-terminal.

        Returns `(read_fd, write_fd, pid)` — OS file descriptors usable with
        the caller's own `os.read`/`os.write`. A POSIX PTY master fd is
        bidirectional, so `read_fd == write_fd` there; ConPTY's input and
        output are two separate pipes, so Windows returns two distinct fds.
        `pid` is the child's real process id (kept public on `PtyTerminal` —
        `notifications.py` and `tests/test_attach_key.py` key off it)."""
        ...

    def resize(self, fd: int, rows: int, cols: int) -> None:
        """Resize the terminal behind `read_fd` (as returned by `spawn_pty`)."""
        ...

    def is_alive(self, pid: int) -> bool: ...

    def terminate(self, pid: int) -> None: ...

    def reap(self, pid: int) -> int:
        """Wait for `pid`'s exit; return its exit code, or -1 if indeterminate."""
        ...

    def close(self, fd: int, write_fd: Optional[int] = None) -> None:
        """Release everything `spawn_pty` allocated for this pty: `fd`
        (`read_fd`), `write_fd` when it differs, and any host-specific
        handle (e.g. Windows' HPCON) kept behind them."""
        ...

    def resolve_executable(self, name: str) -> str:
        """Resolve a bare command name to an invocable path for this host."""
        ...


def get_host_runtime() -> HostRuntime:
    if platform.system() == "Windows":
        from .windows import WindowsHost

        return WindowsHost()
    from .posix import PosixHost

    return PosixHost()
