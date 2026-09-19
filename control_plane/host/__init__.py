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
    ) -> tuple[int, int]:
        """Start `argv` attached to a new pseudo-terminal.

        Returns `(fd, pid)`: `fd` is an OS file descriptor usable with the
        caller's own `os.read`/`os.write`/`os.close`; `pid` is the child's
        real process id (kept public on `PtyTerminal` — `notifications.py`
        and `tests/test_attach_key.py` key off it)."""
        ...

    def resize(self, fd: int, rows: int, cols: int) -> None: ...

    def is_alive(self, pid: int) -> bool: ...

    def terminate(self, pid: int) -> None: ...

    def reap(self, pid: int) -> int:
        """Wait for `pid`'s exit; return its exit code, or -1 if indeterminate."""
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
