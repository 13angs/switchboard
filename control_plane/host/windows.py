"""Windows Host Runtime — stub (ADR-0052 SD4).

Real ConPTY support (a `ctypes` binding to `kernel32.dll`'s
`CreatePseudoConsole`/`ClosePseudoConsole` — never `pywinpty`, which would
break `tests/test_stdlib_purity.py`) ships as its own slice
(`slices.md` `S62`) once it can be written and verified against a native
Windows Python process. Importing this module must never fail on any
platform; only calling one of its methods does, so that `control_plane.host`
stays importable everywhere ahead of that slice landing.
"""

from __future__ import annotations

from typing import Optional

_NOT_YET = (
    "WindowsHost.{fn}: native ConPTY support has not shipped yet "
    "(ADR-0052 SD4, slices.md S62) — run Switchboard's server under "
    "WSL or Linux until it does"
)


class WindowsHost:
    def spawn_pty(
        self,
        argv: list[str],
        cwd: Optional[str],
        env: Optional[dict],
        rows: int,
        cols: int,
    ) -> tuple[int, int]:
        raise NotImplementedError(_NOT_YET.format(fn="spawn_pty"))

    def resize(self, fd: int, rows: int, cols: int) -> None:
        raise NotImplementedError(_NOT_YET.format(fn="resize"))

    def is_alive(self, pid: int) -> bool:
        raise NotImplementedError(_NOT_YET.format(fn="is_alive"))

    def terminate(self, pid: int) -> None:
        raise NotImplementedError(_NOT_YET.format(fn="terminate"))

    def reap(self, pid: int) -> int:
        raise NotImplementedError(_NOT_YET.format(fn="reap"))

    def resolve_executable(self, name: str) -> str:
        raise NotImplementedError(_NOT_YET.format(fn="resolve_executable"))
