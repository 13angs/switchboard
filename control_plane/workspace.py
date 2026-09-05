"""Workspace structural overview — the board's second data source (v3.0).

Single public entry: workspace_overview() — reads the *repo tree* (markdown the
workspace already maintains) rather than harness session stores, and aggregates
it into the shape the Work tab renders.

Why a second source: every existing surface is session-centric — a card is a
Claude session. Nothing answered "which pieces of work are left, and who holds
them", because that lives in the repo's own documents, not in any jsonl.

Design constraints (ADR-0029):
  - Follows the analytics.py pattern (ADR-0013 § SD1): new module, one public
    entry, compute-on-the-fly, no storage dependency.
  - Standard library only — tests/test_stdlib_purity.py enforces this.
  - Reads committed files only, so the view lags un-merged work by one PR. The
    payload states the HEAD it was computed from rather than implying live data.
  - Cache key is `git rev-parse HEAD`: the tree cannot change without it moving.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Status glyphs used by projects/<name>/slices.md, mapped to board columns.
# The files are written for humans first; the glyph is the only stable token in
# a column whose prose changes freely.
_STATUS_COLUMNS: dict[str, str] = {
    "✅": "done",
    "🔜": "next",
    "🔄": "running",
    "⬜": "todo",
    "⬛": "off",
}

# A row whose text carries this is waiting on a person, not on the agent —
# it is not "todo" in any actionable sense and gets its own column.
_OWNER_MARK = "🖐️"

COLUMN_ORDER = ("done", "running", "next", "todo", "owner", "off")

# module-level cache: repo_root -> (head_sha, payload)
_CACHE: dict[str, tuple[str, dict]] = {}


@dataclass
class Slice:
    id: str
    title: str
    day: str
    column: str
    note: str


def workspace_overview(
    repo_root: str,
    now: Optional[datetime] = None,
    use_cache: bool = True,
) -> dict:
    """Return the structural overview for GET /workspace.

    Args:
        repo_root: absolute workspace root
        now: current time (injected for testability)
        use_cache: skip the HEAD-keyed cache when False (tests, forced refresh)
    """
    root = Path(repo_root)
    if not root.is_dir():
        raise ValueError(f"repo_root is not a directory: {repo_root}")

    head = _head_sha(root)
    if use_cache and head:
        cached = _CACHE.get(str(root))
        if cached and cached[0] == head:
            return cached[1]

    projects = _scan_projects(root)
    payload = {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "repo": str(root),
        "head": head,
        "stale_by": "one merged PR — this view reads committed files only",
        "projects": projects,
        "totals": _totals(projects),
        "gaps": _scan_gaps(root),
    }

    if head:
        _CACHE[str(root)] = (head, payload)
    return payload


def invalidate_cache(repo_root: str | None = None) -> None:
    """Drop cached payloads. Mirrors discovery.invalidate_cache()."""
    if repo_root is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(Path(repo_root)), None)


# ── internals ────────────────────────────────────────────────────────────────


def _head_sha(root: Path) -> str:
    """Current commit, or '' when the tree is not a git checkout.

    An empty sha disables caching rather than failing: a non-git directory is a
    legitimate way to run the board against a plain folder.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _scan_projects(root: Path) -> list[dict]:
    """Every projects/<name>/ that carries a slices.md, plus what it is missing."""
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return []

    found: list[dict] = []
    for child in sorted(projects_dir.iterdir()):
        if not child.is_dir():
            continue
        slices_file = child / "slices.md"
        if not slices_file.is_file():
            continue
        slices = _parse_slices(slices_file)
        found.append(
            {
                "name": child.name,
                "slices": [asdict(s) for s in slices],
                "columns": _bucket(slices),
                "has": {
                    "scope": (child / "scope.md").is_file(),
                    "risks": (child / "risks.md").is_file(),
                    "hld": (child / "docs" / "design").is_dir(),
                },
            }
        )
    return found


def _parse_slices(path: Path) -> list[Slice]:
    """Rows of the first markdown table in slices.md.

    Deliberately positional rather than header-driven: the files are Thai prose
    with a stable column *order* (id | title | day | status | note) but header
    wording that is free to change. A header-name parser would break on a
    rewording that a human would not even notice.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    rows: list[Slice] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table and rows:
                break  # first table only — later tables are other content
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        if set("".join(cells)) <= set("-: "):
            in_table = True  # separator row
            continue
        if not in_table:
            continue  # header row

        ident = _strip_md(cells[0])
        title = _strip_md(cells[1])
        day = _strip_md(cells[2])
        status_cell = cells[3]
        note = _strip_md(cells[4]) if len(cells) > 4 else ""

        rows.append(
            Slice(
                id=ident,
                title=title,
                day=day,
                column=_column_for(status_cell, note),
                note=note,
            )
        )
    return rows


def _column_for(status_cell: str, note: str) -> str:
    """Which board column a row belongs in.

    The owner mark wins over the status glyph: a piece the owner must decide is
    not actionable work regardless of how its status reads.
    """
    if _OWNER_MARK in status_cell or _OWNER_MARK in note:
        return "owner"
    for glyph, column in _STATUS_COLUMNS.items():
        if glyph in status_cell:
            return column
    return "todo"


def _bucket(slices: list[Slice]) -> dict[str, int]:
    counts = {c: 0 for c in COLUMN_ORDER}
    for s in slices:
        counts[s.column] = counts.get(s.column, 0) + 1
    return counts


def _totals(projects: list[dict]) -> dict:
    counts = {c: 0 for c in COLUMN_ORDER}
    for p in projects:
        for column, n in p["columns"].items():
            counts[column] = counts.get(column, 0) + n
    return {"projects_with_slices": len(projects), "slices": counts}


_GAP_ROW = re.compile(r"^\|\s*`(G-\d+)`\s*\|")


def _scan_gaps(root: Path) -> dict:
    """Counts from team-os/GAPS.md — the workspace's own doc-gap register.

    Reads the register rather than recomputing gaps from a full-tree scan: the
    file is already maintained, and a second implementation would drift from it.
    """
    path = root / "team-os" / "GAPS.md"
    if not path.is_file():
        return {"present": False}

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"present": False}

    seen: dict[str, str] = {}
    for line in text.splitlines():
        m = _GAP_ROW.match(line.strip())
        if not m:
            continue
        gid = m.group(1)
        if gid in seen:
            continue  # later mentions are commentary tables, not register rows
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        status = cells[-1] if cells else ""
        if "✅" in status:
            seen[gid] = "closed"
        elif "🟠" in status:
            seen[gid] = "reduced"
        else:
            seen[gid] = "open"

    counts = {"closed": 0, "reduced": 0, "open": 0}
    for state in seen.values():
        counts[state] += 1
    return {"present": True, "total": len(seen), **counts}


def _strip_md(cell: str) -> str:
    """Plain text from a markdown table cell — links keep their label."""
    out = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    out = out.replace("**", "").replace("`", "")
    out = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", out)
    return out.strip()
