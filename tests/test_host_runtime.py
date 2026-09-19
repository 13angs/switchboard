#!/usr/bin/env python3
"""ADR-0052 — the Host Runtime boundary (SD1/SD2) and real ConPTY (SD4).

`control_plane.host` and `control_plane.terminal` must import cleanly on any
platform, including one without `pty`/`fcntl`/`termios` (Windows). Those
modules are stdlib but POSIX-only, so `PosixHost` must import them lazily,
inside its own methods, never at module scope — this is what the guard below
actually proves, rather than assuming it from reading the source.

On Windows, `test_windows_host_spawns_a_real_conpty_session_on_windows` also
exercises the real `ctypes`-based ConPTY implementation end to end. See its
own docstring for exactly what it does and does not verify.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_host_runtime.py
    # or, if pytest is available:  pytest projects/switchboard/repos/switchboard/tests
"""

from __future__ import annotations

import ast
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import host, terminal  # noqa: E402
from control_plane.host.posix import PosixHost  # noqa: E402
from control_plane.host.windows import WindowsHost  # noqa: E402

POSIX_ONLY_MODULES = {"fcntl", "pty", "termios", "signal"}


def test_get_host_runtime_matches_this_platform():
    runtime = host.get_host_runtime()
    if platform.system() == "Windows":
        assert isinstance(runtime, WindowsHost)
    else:
        assert isinstance(runtime, PosixHost)


def test_posix_module_has_no_module_level_posix_only_imports():
    """The actual guard: parse `posix.py` and assert none of the POSIX-only
    stdlib modules are imported at module scope (only inside functions)."""
    tree = ast.parse(
        Path(host.__file__).parent.joinpath("posix.py").read_text(encoding="utf-8")
    )
    module_level_imports: set[str] = set()
    for node in tree.body:  # top-level statements only — not nested in a def
        if isinstance(node, ast.Import):
            module_level_imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level_imports.add(node.module.split(".")[0])

    offenders = module_level_imports & POSIX_ONLY_MODULES
    assert not offenders, (
        f"posix.py imports {offenders} at module scope — this breaks import "
        "on Windows, which is exactly what ADR-0052 SD1 requires never happen"
    )


def test_windows_host_spawns_a_real_conpty_session_on_windows():
    """ADR-0052 SD4 / `slices.md` `S62` — real ConPTY via `ctypes`.

    Verified in-session, repeatedly, across `cmd.exe` and a plain `python.exe`
    child: `spawn_pty`/`is_alive`/`terminate`/`reap`/`close` all work, and the
    ConPTY session's own negotiation handshake
    (`\\x1b[?9001h\\x1b[?1004h` — win32-input-mode + focus-event-mode) and its
    teardown sequence (embedding the real child's own path) both arrive
    correctly on `read_fd`, proving `CreatePseudoConsole` +
    `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` are wired to the right process.

    What is **not** asserted here: that a child's own text output (e.g. what
    `echo` prints) arrives on `read_fd`. In this session's own sandboxed
    shell it consistently did not — visible instead on the shell's own
    console — while `AllocConsole` for this same process independently
    returned `ERROR_ACCESS_DENIED` (5), pointing at a console/window-station
    restriction specific to that shell rather than a wiring bug (the
    handshake/teardown bytes proving the wiring is correct). Confirming full
    interactive echo needs a run from an ordinary, non-agent-sandboxed
    Windows terminal."""
    if platform.system() != "Windows":
        WindowsHost()  # must not explode merely existing off-Windows
        return

    import os
    import threading

    win = WindowsHost()
    read_fd, write_fd, pid = win.spawn_pty(
        ["cmd.exe", "/c", "echo ADR_0052_S62_SMOKE"], None, None, 24, 80
    )
    try:
        collected = bytearray()

        def _reader():
            while True:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    return
                collected.extend(chunk)

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(3)

        assert b"\x1b[?9001h" in bytes(collected), (
            f"expected the ConPTY win32-input-mode handshake, got {bytes(collected)!r}"
        )
        code = win.reap(pid)
        assert code == 0, f"expected clean exit, got {code}"
        assert win.is_alive(pid) is False
    finally:
        win.close(read_fd, write_fd)


def test_terminal_module_does_not_import_posix_only_modules_directly():
    """`terminal.py` must reach POSIX facilities only through `control_plane.host`."""
    assert not hasattr(terminal, "fcntl")
    assert not hasattr(terminal, "pty")
    assert not hasattr(terminal, "termios")
    assert not hasattr(terminal, "signal")


def test_posix_host_resolve_executable_finds_a_real_binary():
    # `cat` is what tests/test_terminal_lifecycle.py itself spawns in place
    # of the `claude` binary. `shutil.which` (what `resolve_executable`
    # wraps) is platform-native, so on Windows/MSYS this legitimately
    # resolves to `cat.exe` rather than a bare `cat` — check the stem, not
    # an exact suffix.
    resolved = PosixHost().resolve_executable("cat")
    assert Path(resolved).stem == "cat", resolved


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
