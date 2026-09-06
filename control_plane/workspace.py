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
    dispatch = _scan_dispatch(root)
    _attach_default_roles(root, projects, dispatch)
    payload = {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "repo": str(root),
        "head": head,
        "stale_by": "one merged PR — this view reads committed files only",
        "projects": projects,
        "totals": _totals(projects),
        "gaps": _scan_gaps(root),
        "dispatch": dispatch,
    }

    if head:
        _CACHE[str(root)] = (head, payload)
    return payload


def allowed_models(repo_root: str) -> set[str]:
    """Model ids this workspace has declared a tier for.

    The spawn path validates against this rather than a list held in switchboard:
    the lineup changes when the workspace says it changes, and an id that is not
    in the declared map is a typo or a stale client, never a silent pass-through.
    """
    try:
        payload = workspace_overview(repo_root)
    except ValueError:
        return set()
    dispatch = payload.get("dispatch") or {}
    if not dispatch.get("present"):
        return set()
    return set(dispatch.get("tiers", {}).values())


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
        owns = _frontmatter(slices_file)
        found.append(
            {
                "name": child.name,
                "slices": [asdict(s) for s in slices],
                "columns": _bucket(slices),
                "client": owns.get("client", ""),
                "team": owns.get("team", ""),
                "has": {
                    "scope": (child / "scope.md").is_file(),
                    "risks": (child / "risks.md").is_file(),
                    "hld": (child / "docs" / "design").is_dir(),
                },
            }
        )
    return found


def _frontmatter(path: Path) -> dict[str, str]:
    """The `key: value` lines of a leading `---` block.

    Hand-rolled rather than PyYAML: HLD v2 AD1 keeps this server zero-dependency,
    and the two keys read here (`client`, `team`) are plain scalars. Anything
    more structured is deliberately not supported — a parser that half-implements
    YAML is worse than one that says what it reads.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}

    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or key.startswith((" ", "\t", "-")):
            continue
        out[key.strip()] = value.strip().strip("\"'")
    return out


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


# The workspace declares its tier lineup in one prose sentence, on purpose:
# "light = `claude-haiku-4-5`, standard = `claude-sonnet-5`, heavy = `claude-opus-5`".
_TIER_ASSIGN = re.compile(r"\b(light|standard|heavy)\s*=\s*`([A-Za-z0-9._-]+)`")

_ROLE_HEADING = "โมเดลต่อ role"
_ROLE_TIERS = ("heavy", "standard", "light")


def _scan_dispatch(root: Path) -> dict:
    """Which role runs on which model — read from the workspace, never held here.

    Two files, each the declared single source of its own half:
      - docs/sops/sop-agent-orchestration.md  → tier name → model id
      - team-os/people/roles.md               → role → default tier

    Deliberately *not* mirrored into this repo. ADR-0029 §SD4 took the same line
    for the gap register: a second copy of a maintained table drifts from it and
    then argues with it. The cost is that a rewording upstream turns dispatch
    off — which is the safe direction, because roles.md states plainly that
    "ไม่ส่ง model ≠ ค่า default ที่ปลอดภัย": a missing model is inherited from
    whatever launched the server, so guessing one is worse than refusing.
    """
    sop = root / "docs" / "sops" / "sop-agent-orchestration.md"
    roles_file = root / "team-os" / "people" / "roles.md"

    tiers = _parse_tier_models(sop)
    if not tiers:
        return {
            "present": False,
            "reason": f"ไม่พบแผนที่ tier → model ใน {sop.name}",
        }

    roles = _parse_role_tiers(roles_file, tiers)
    if not roles:
        return {
            "present": False,
            "reason": f"ไม่พบตาราง role → tier ใน {roles_file.name}",
        }

    return {
        "present": True,
        "tiers": tiers,
        "roles": roles,
        "source": {
            "tiers": "docs/sops/sop-agent-orchestration.md",
            "roles": "team-os/people/roles.md",
        },
    }


def _parse_tier_models(path: Path) -> dict[str, str]:
    """tier name → model id, from the canonical sentence in the SOP."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found = {tier: model for tier, model in _TIER_ASSIGN.findall(text)}
    # All three or none: a partial lineup would let a role resolve while its
    # neighbour silently does not, which is harder to notice than a clean off.
    return found if all(t in found for t in _ROLE_TIERS) else {}


def _parse_role_tiers(path: Path, tiers: dict[str, str]) -> list[dict]:
    """role → default tier, from the table under roles.md § โมเดลต่อ role.

    Anchored on that heading rather than "the first table": roles.md opens with
    a routing table that has nothing to do with models.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    out: list[dict] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if in_section:
                break  # the section ended; later tables are a different axis
            in_section = _ROLE_HEADING in stripped
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        role = _strip_md(cells[0])
        tier = _strip_md(cells[1]).lower()
        if tier not in tiers or not role:
            continue  # header row, separator row, or the "ขึ้น heavy เมื่อ" prose
        out.append({"role": role, "tier": tier, "model": tiers[tier]})
    return out


_OWNERSHIP_HEADING = "แกนความเป็นเจ้าของ"

# `dev` is read by both `senior-developer` and `developer` in the ownership
# table (roles.md § แกนความเป็นเจ้าของ) — the escalation is a judgment call the
# row itself does not carry, so an unqualified `team: dev` lands on the role
# that does routine in-slice work, not the one that owns system-wide calls.
_DISCIPLINE_TIE_BREAK = {"dev": "developer"}


def _slug(text: str) -> str:
    return re.sub(r"[\s_]+", "-", text.strip().lower())


def _parse_role_disciplines(path: Path) -> dict[str, list[str]]:
    """role slug → disciplines it owns, from roles.md § แกนความเป็นเจ้าของ.

    Same anchored-on-heading approach as `_parse_role_tiers`: the table's
    prose wording is free to change, but its position under this heading is
    the contract. Read once per call rather than cached alongside the tier
    table — this table is small and dispatch already re-reads roles.md.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    out: dict[str, list[str]] = {}
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if in_section:
                break  # section ended; the appendix table is a different axis
            in_section = _OWNERSHIP_HEADING in stripped
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        role = _slug(_strip_md(cells[0]))
        disciplines_cell = _strip_md(cells[2])
        if not role or set(role) <= set("-: "):
            continue  # separator row
        if role in ("role", "แกนความเป็นเจ้าของ"):
            continue  # header row
        disciplines = [
            _slug(d) for d in disciplines_cell.split("·") if _slug(d) and _slug(d) != "—"
        ]
        out[role] = disciplines
    return out


def _default_role_for_team(
    team: str, role_disciplines: dict[str, list[str]], roles: list[dict]
) -> Optional[str]:
    """Which of `dispatch.roles` a project's `team:` frontmatter points to.

    `team:` is written freely across projects — sometimes a discipline
    (`dev`, `forge`), sometimes a role name directly (`product-owner`).
    Both are tried; an unrecognised value resolves to nothing rather than a
    guess, so the caller falls back to its own default (ADR-0033 §SD1).
    """
    if not team:
        return None
    known = {_slug(r["role"]): r["role"] for r in roles}
    slug = _slug(team)
    if slug in known:
        return known[slug]
    if slug in _DISCIPLINE_TIE_BREAK:
        return known.get(_DISCIPLINE_TIE_BREAK[slug])
    for role_slug, disciplines in role_disciplines.items():
        if slug in disciplines:
            return known.get(role_slug)
    return None


def _attach_default_roles(root: Path, projects: list[dict], dispatch: dict) -> None:
    """Sets each project's `default_role` — the dispatch box should open on
    the role the project's own work belongs to, not the first row of a table
    it has nothing to do with (ADR-0033, closes risks.md S-09)."""
    if not dispatch.get("present"):
        for p in projects:
            p["default_role"] = None
        return
    role_disciplines = _parse_role_disciplines(
        root / "team-os" / "people" / "roles.md"
    )
    for p in projects:
        p["default_role"] = _default_role_for_team(
            p.get("team", ""), role_disciplines, dispatch["roles"]
        )


def _strip_md(cell: str) -> str:
    """Plain text from a markdown table cell — links keep their label."""
    out = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    out = out.replace("**", "").replace("`", "")
    out = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", out)
    return out.strip()
