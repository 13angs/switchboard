#!/usr/bin/env python3
"""ADR-0052 SD1/SD2 — the Host Runtime boundary.

`control_plane.host` and `control_plane.terminal` must import cleanly on any
platform, including one without `pty`/`fcntl`/`termios` (Windows). Those
modules are stdlib but POSIX-only, so `PosixHost` must import them lazily,
inside its own methods, never at module scope — this is what the guard below
actually proves, rather than assuming it from reading the source.

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


def test_windows_host_is_importable_but_not_functional_yet():
    win = WindowsHost()
    try:
        win.spawn_pty(["true"], None, None, 24, 80)
        raise AssertionError("WindowsHost.spawn_pty should not work yet (ADR-0052 SD4)")
    except NotImplementedError as e:
        assert "S62" in str(e)


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
