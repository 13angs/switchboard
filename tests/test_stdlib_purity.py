#!/usr/bin/env python3
"""The backend imports the standard library and nothing else.

README § "Your data stays local" tells external readers the server is
"`python3` with the standard library only". That is a promise a cloner relies
on — they run `python3 server.py` with no install step. A single stray
`import requests` in `control_plane/` breaks every such clone at once, and
nothing else in the repo would catch it: there is no requirements file to
review and CI never imports the backend.

Scope is `control_plane/` + `server.py` — the code a consuming host actually
runs. `tests/` is exempt; pytest is a development dependency, not a runtime one.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_stdlib_purity.py
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Modules that live in this repo, so an absolute `import control_plane.x`
# resolves locally rather than to a package on PyPI.
FIRST_PARTY = {"control_plane", "server"}


def _backend_sources() -> list[Path]:
    return sorted(ROOT.glob("control_plane/**/*.py")) + [ROOT / "server.py"]


def _foreign_imports(paths: list[Path]) -> dict[str, set[str]]:
    """Map source file → imported roots that are neither stdlib nor first-party."""
    allowed = sys.stdlib_module_names | FIRST_PARTY
    found: dict[str, set[str]] = {}

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import — first-party by definition.
                if node.level or not node.module:
                    continue
                roots = [node.module.split(".")[0]]
            else:
                continue

            for root in roots:
                if root not in allowed:
                    found.setdefault(_label(path), set()).add(root)

    return found


def _label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def test_backend_imports_only_stdlib():
    sources = _backend_sources()
    assert sources, "found no backend sources to check — the glob is wrong"

    foreign = _foreign_imports(sources)

    assert not foreign, (
        "backend must import stdlib only (README § Your data stays local): "
        + "; ".join(f"{path} -> {sorted(mods)}" for path, mods in sorted(foreign.items()))
    )


def test_guard_detects_a_third_party_import():
    """A guard that cannot fail is not a guard — prove this one can."""
    with tempfile.TemporaryDirectory(prefix="orch-purity-") as tmp:
        offender = Path(tmp) / "offender.py"
        offender.write_text(
            "import json\nimport requests\nfrom control_plane import lock\n",
            encoding="utf-8",
        )

        foreign = _foreign_imports([offender])

    assert list(foreign.values()) == [{"requests"}]


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
