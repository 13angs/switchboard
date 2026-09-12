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
import posixpath
import re
import subprocess
from dataclasses import dataclass, asdict, field
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

# The opt-in `blocked-by` column (row-status.md § ลำดับก่อนหลัง, W22): a row's
# raw cell value is `#` ids of sibling rows in the *same* file, comma-separated
# — cross-file blockers stay prose in "ใช้งานได้จริงว่า" by design (that
# section's item 2). These three spellings all mean "no blocker" — the same
# convention every other column in these files already uses for "none".
_BLOCKED_EMPTY = {"", "-", "—"}

# The nine belt stations of the opt-in `stage` column (row-status.md § สายพาน,
# accepted 2026-09-12). This is the SECOND axis: `stage` says where a row sits
# on the belt, the 8-glyph `สถานะ` column still says whether it is open. A value
# outside this set is a route-lint Check 9 finding, not something to guess at —
# the reader drops it to "unset" so one typo cannot move a card to a station
# nobody declared.
STAGE_ORDER = (
    "backlog",
    "techdesign",
    "readydev",
    "inprogress",
    "review",
    "readyqa",
    "readydeploy",
    "deployed",
    "done",
)
_STAGES = frozenset(STAGE_ORDER)

# Which files team-os says a project should carry (ADR-0040 §SD2). Read from the
# workspace, never mirrored here — same discipline as _scan_dispatch. Anchored on
# the heading because that table's column headers are Thai prose with no pinned
# English keyword to find it by, unlike rituals.md (ADR-0036 §SD2).
_SLOTS_FILE = ("team-os", "projects", "README.md")
_SLOTS_HEADING = "ช่องที่ต้นแบบมี"

# The one declared slot whose name is not its location: the template calls the
# living HLD a file, this workspace keeps it as a folder (every context.md points
# at docs/design/*), and _scan_projects has mapped it that way since ADR-0029.
_SLOT_LOCATIONS: dict[str, tuple[str, str]] = {"hld": ("dir", "docs/design")}

# What `has` reports when the declaration cannot be read (ADR-0040 §SD4). Not a
# mirror of the table: a degraded mode that says on screen that it is degraded,
# so a reworded heading upstream cannot silently delete a shipped board badge.
FALLBACK_SLOTS = ("scope", "risks", "hld")

_SLOT_NAME = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)\.md\b")

# module-level cache: repo_root -> (head_sha, payload)
_CACHE: dict[str, tuple[str, dict]] = {}


@dataclass
class Slice:
    id: str
    title: str
    day: str
    column: str
    note: str
    # Raw text of an opt-in `role` column cell (ADR-0035), or "" when the file
    # has no such column / the row leaves it blank. Resolved into an actual
    # `dispatch.roles[].role` value by `_attach_default_roles` before this
    # reaches the payload — by the time a card renders, it is never the raw
    # cell text, only the effective role or the project's own default.
    role: str = ""
    # Sibling rows (same file) this one is still waiting on — `[]` when the
    # file has no `blocked-by` column, the cell is empty, or every id it named
    # has since closed (S38: the board must tell "not started" apart from
    # "cannot start yet", not just repeat the raw cell). Each entry is
    # `{"id", "title"}` so a card can name the row it is waiting for, not just
    # print an id. Does **not** change `column` — the 8-glyph status set stays
    # closed (row-status.md § ลำดับก่อนหลัง item 1).
    blocked_by: list[dict] = field(default_factory=list)
    # `{"from", "to"}` when this row's resolved role differs from what it
    # resolved to at the parent of the last commit that touched this file,
    # else `None` (S40: a role handoff is a change the board can see in the
    # `role` cell itself — no second store needed, git history already holds
    # the "before"). Set by `_attach_default_roles`, never by `_parse_slices`:
    # the raw cell alone cannot say what it resolved to last time.
    handoff: Optional[dict] = None
    # Opt-in `stage` column — one of STAGE_ORDER, or "" when the file has no
    # such column / the cell is blank / the value is not a declared station.
    stage: str = ""
    # Opt-in `part-of` column — the `#` of the row this one is an acceptance
    # criterion OF. "" when this row is a card in its own right.
    part_of: str = ""
    # Set on a PARENT row from the rows whose `part-of` names it: how many of
    # its criteria are closed out of how many exist. `None` when no row points
    # here — which is not the same as 0/0 (row-status.md § สายพาน: the count is
    # assembled at DISPLAY time; the file still holds one criterion per row).
    criteria: Optional[dict] = None
    # The two axes disagreeing (meta/adr-slices-stage-axis-2026-09.md §SD3):
    # "stuck-open"  — belt says done, the glyph says the row is still open
    # "skipped-gate" — the glyph says closed from a station that is not `done`
    # `None` when they agree or when one of them is not declared. The board
    # prints this; it never writes a correction back.
    axis_conflict: Optional[str] = None


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

    slots = project_slots(root)
    projects = _scan_projects(root, slots)
    dispatch = _scan_dispatch(root)
    _attach_default_roles(root, projects, dispatch)
    # ADR-0041 §SD1 — both belt columns are functions of HEAD, so they ride the
    # HEAD-cached payload and the page joins them to the windowed
    # /roles/activity itself. The register they are attributed to is the same
    # roles.md table dispatch already read.
    ownership = _parse_ownership(root / "team-os" / "people" / "roles.md")
    payload = {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "repo": str(root),
        "head": head,
        "stale_by": "one merged PR — this view reads committed files only",
        "slots": slots,
        "projects": projects,
        "totals": _totals(projects),
        "gaps": _scan_gaps(root),
        "dispatch": dispatch,
        "pipeline": pipeline_surfaces(root, ownership),
        # ADR-0043 §SD2 — the same `ownership` walk, one screen further: the
        # belt asks which stages a role holds, the register asks who the role
        # is. Both ride the HEAD-cached payload because both are functions of
        # the tree, and the page joins the register to `dispatch` itself.
        "register": role_register(root, ownership),
        # S40 — every row whose resolved `role` differs from its own last
        # commit's, flattened across projects so the board can announce a
        # handoff without the reader having to walk every column of every
        # project's cards to notice one changed.
        "handoffs": _collect_handoffs(projects),
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


def model_tier(repo_root: str, model: str) -> Optional[str]:
    """Which tier name a model id belongs to, or None when it isn't declared.

    The spawn path uses this to enforce ADR-0032 §SD6: `--effort` must never
    reach a `light`-tier session, so it needs the model → tier direction too,
    not just the tier → model map `allowed_models()` already exposes.
    """
    try:
        payload = workspace_overview(repo_root)
    except ValueError:
        return None
    dispatch = payload.get("dispatch") or {}
    if not dispatch.get("present"):
        return None
    for tier, m in dispatch.get("tiers", {}).items():
        if m == model:
            return tier
    return None


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


def _previous_slices_text(root: Path, slices_path: Path) -> Optional[str]:
    """`slices_path`'s content at the parent of its own last commit, or None.

    S40 needs a "before" for the handoff check without a second storage layer:
    ADR-0029 already treats git history as the record of everything this
    board reads, so "before" here means "at the parent of the last commit
    that touched this file", not wall-clock time. None covers every case
    where there is nothing to compare against — not a git checkout, the file
    has no commits yet, or the commit that last touched it is the one that
    created it (no parent had the file at all).
    """
    try:
        rel = slices_path.relative_to(root).as_posix()
    except ValueError:
        return None
    try:
        log = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", rel],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    last_commit = log.stdout.strip() if log.returncode == 0 else ""
    if not last_commit:
        return None
    try:
        show = subprocess.run(
            ["git", "show", f"{last_commit}~1:{rel}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return show.stdout if show.returncode == 0 else None


def _collect_handoffs(projects: list[dict]) -> list[dict]:
    """Every row across every project whose `role` just changed (S40).

    Flattened here rather than left for the page to walk every column of
    every project's cards — the whole point is that a handoff is visible
    without anyone going looking for it.
    """
    out: list[dict] = []
    for p in projects:
        for s in p["slices"]:
            handoff = s.get("handoff")
            if not handoff:
                continue
            out.append(
                {
                    "project": p["name"],
                    "id": s["id"],
                    "title": s["title"],
                    "from": handoff["from"],
                    "to": handoff["to"],
                }
            )
    return out


def _scan_projects(root: Path, slots: Optional[dict] = None) -> list[dict]:
    """Every projects/<name>/ that carries a slices.md, plus what it is missing.

    `has` follows the slots team-os declares (ADR-0040 §SD4) rather than three
    hardcoded names, so the card's "ยังไม่มี:" badge and the gap dialog are one
    list. When the declaration cannot be read it falls back to the original
    three and `payload["slots"]["source"]` says so.
    """
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return []
    if slots is None:
        slots = project_slots(root)

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
                "has": _slot_presence(child, slots["slots"]),
            }
        )
    return found


def project_slots(root: Path) -> dict:
    """The files a project is expected to carry, read from team-os (§SD2).

    Returns `{"source", "slots": [...], "unmapped": [...], "reason"}`. A slot is
    a row of `team-os/projects/README.md § ช่องที่ต้นแบบมี …` whose first cell
    names a `*.md` file; the row that names the *router's* status table has no
    such token and is reported under `unmapped` rather than dropped, because a
    template slot the board does not check is a fact the screen should carry.

    The section's "มีกี่โปรเจกต์" column is deliberately not read: it was
    measured 2026-09-05 and is already stale (it says slices.md is 1/31 while
    six projects carry one today). Counting is the board's job, not the table's.
    """
    path = root.joinpath(*_SLOTS_FILE)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _fallback_slots(f"ไม่พบ {'/'.join(_SLOTS_FILE)}")

    found: list[str] = []
    unmapped: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if in_section:
                break  # section ended; later tables are a different axis
            in_section = _SLOTS_HEADING in stripped
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        first = _strip_md(cells[0])
        if not first or set(first) <= set("-: "):
            continue  # separator row
        match = _SLOT_NAME.search(first)
        if match is None:
            if first != "ช่อง":  # the header cell itself
                unmapped.append(first)
            continue
        key = match.group(1)
        if key not in found:
            found.append(key)

    if not found:
        return _fallback_slots(
            f"ไม่พบตารางช่องใต้หัวข้อ {_SLOTS_HEADING} ใน {path.name}"
        )
    return {
        "source": "declared",
        "reason": "",
        "slots": [_slot(key) for key in found],
        "unmapped": unmapped,
    }


def _fallback_slots(reason: str) -> dict:
    return {
        "source": "fallback",
        "reason": reason,
        "slots": [_slot(key) for key in FALLBACK_SLOTS],
        "unmapped": [],
    }


def _slot(key: str) -> dict:
    kind, where = _SLOT_LOCATIONS.get(key, ("file", f"{key}.md"))
    return {"key": key, "kind": kind, "where": where}


def _slot_presence(project_dir: Path, slots: list[dict]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for slot in slots:
        target = project_dir / slot["where"]
        out[slot["key"]] = (
            target.is_dir() if slot["kind"] == "dir" else target.is_file()
        )
    return out


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

    Two columns are the exception: an optional trailing `role` column
    (ADR-0035) and an optional `blocked-by` column (row-status.md § ลำดับ
    ก่อนหลัง, W22) are opt-in per file, so neither can follow a fixed index —
    some files have 5 cells, some 6, some 7. Both are found instead by
    scanning the header row for a cell whose text is exactly `role` /
    `blocked-by` (English, case-insensitive) — a keyword deliberately left
    untranslated so it can never collide with the Thai prose headers that
    stay free to reword.

    `blocked-by` needs a second pass: its value names sibling ids in the same
    file, and whether a blocker is still open depends on that sibling's own
    `column` — which is only known once every row has been walked once.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return _parse_slices_text(text)


def _parse_slices_text(text: str) -> list[Slice]:
    """Same table walk as `_parse_slices`, given text instead of a path.

    Split out for S40: a handoff check needs to run this same parse against a
    `git show` snapshot of an older commit, which has no path on disk.
    """
    # Rows are dicts rather than a widening tuple: this walk already grew from
    # 5 positional cells to 7 and then to 9, and positional coupling in this
    # file has drawn blood once already (a `|` inside a cell shifted every
    # column after it — row-status.md § ที่เกิดขึ้นจริงตอนเปิดคอลัมน์นี้).
    raw_rows: list[dict] = []
    in_table = False
    role_col: Optional[int] = None
    blocked_col: Optional[int] = None
    stage_col: Optional[int] = None
    part_of_col: Optional[int] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table and raw_rows:
                break  # first table only — later tables are other content
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        if set("".join(cells)) <= set("-: "):
            in_table = True  # separator row
            continue
        if not in_table:
            # header row — look for the opt-in `role` / `blocked-by` columns
            for i, cell in enumerate(cells):
                name = _strip_md(cell).strip().lower()
                if name == "role":
                    role_col = i
                elif name == "blocked-by":
                    blocked_col = i
                elif name == "stage":
                    stage_col = i
                elif name == "part-of":
                    part_of_col = i
            continue

        ident = _strip_md(cells[0])
        title = _strip_md(cells[1])
        day = _strip_md(cells[2])
        status_cell = cells[3]
        note = _strip_md(cells[4]) if len(cells) > 4 else ""
        role = (
            _strip_md(cells[role_col])
            if role_col is not None and role_col < len(cells)
            else ""
        )
        blocked_raw = (
            _strip_md(cells[blocked_col])
            if blocked_col is not None and blocked_col < len(cells)
            else ""
        )

        stage = (
            _strip_md(cells[stage_col]).strip().lower()
            if stage_col is not None and stage_col < len(cells)
            else ""
        )
        part_of = (
            _strip_md(cells[part_of_col]).strip()
            if part_of_col is not None and part_of_col < len(cells)
            else ""
        )

        raw_rows.append(
            {
                "id": ident,
                "title": title,
                "day": day,
                "status_cell": status_cell,
                "note": note,
                "role": role,
                "blocked_raw": blocked_raw,
                "stage": stage if stage in _STAGES else "",
                "part_of": "" if part_of in _BLOCKED_EMPTY else part_of,
            }
        )

    # Second pass: resolve `blocked-by` against this file's own rows, now that
    # every row's column is knowable.
    columns_by_id = {r["id"]: _column_for(r["status_cell"], r["note"]) for r in raw_rows}
    titles_by_id = {r["id"]: r["title"] for r in raw_rows}
    criteria_by_parent = _criteria(raw_rows, columns_by_id)

    rows: list[Slice] = []
    for r in raw_rows:
        column = _column_for(r["status_cell"], r["note"])
        rows.append(
            Slice(
                id=r["id"],
                title=r["title"],
                day=r["day"],
                column=column,
                note=r["note"],
                role=r["role"],
                blocked_by=_open_blockers(r["blocked_raw"], columns_by_id, titles_by_id),
                stage=r["stage"],
                part_of=r["part_of"],
                criteria=criteria_by_parent.get(r["id"]),
                axis_conflict=_axis_conflict(r["stage"], column),
            )
        )
    return rows


def _criteria(raw_rows: list[dict], columns_by_id: dict[str, str]) -> dict[str, dict]:
    """parent id → {done, total}, counted from the rows that name it.

    A row is a criterion of its parent, and its own glyph is the tick: `✅`
    (and `❌`, which closes a criterion by cancelling it) counts as done. The
    parent keeps one status of its own — this count is assembled here, never
    written back into the file (row-status.md § สายพาน).

    A `part-of` naming a row that is not in this table, or naming itself, is a
    route-lint Check 10 finding; it is dropped here rather than counted, the
    same way `_open_blockers` drops a dangling blocker.
    """
    out: dict[str, dict] = {}
    for r in raw_rows:
        parent = r["part_of"]
        if not parent or parent == r["id"] or parent not in columns_by_id:
            continue
        tally = out.setdefault(parent, {"done": 0, "total": 0})
        tally["total"] += 1
        if columns_by_id[r["id"]] == "done":
            tally["done"] += 1
    return out


def _axis_conflict(stage: str, column: str) -> Optional[str]:
    """Which way the two axes disagree, or None when they do not.

    Only ever computed from two declared values: a file without a `stage`
    column has one axis, and one axis cannot contradict itself.
    """
    if not stage:
        return None
    if stage == "done" and column != "done":
        return "stuck-open"
    if column == "done" and stage != "done":
        return "skipped-gate"
    return None


def _open_blockers(
    raw: str, columns_by_id: dict[str, str], titles_by_id: dict[str, str]
) -> list[dict]:
    """The still-open ids a `blocked-by` cell names, each with its title.

    An id the cell names but this file does not contain is a route-lint
    concern (Check 7 — a dangling or self/circular reference), not something
    this reader guesses at: it is silently dropped here rather than surfaced
    as a fake blocker. Same for a blocker that has since closed (`column ==
    "done"`) — row-status.md says a closed blocker must be cleared from the
    cell, and a stale id left behind must not keep holding the row back.
    """
    if raw.strip() in _BLOCKED_EMPTY:
        return []
    out: list[dict] = []
    for token in raw.split(","):
        bid = token.strip()
        if not bid or bid not in columns_by_id:
            continue
        if columns_by_id[bid] == "done":
            continue
        out.append({"id": bid, "title": titles_by_id[bid]})
    return out


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

    # S37 — the office segment of `Assignment:` comes off the same roles.md
    # table, two columns to the left of the tier the dispatch row already
    # carries. Reading it here rather than in the browser keeps the id's four
    # segments resolved by the one parser that owns that table: a prompt that
    # prints `-` for office is a prompt whose commit `.githooks/commit-msg`
    # rejects, and the operator then edits the id by hand — which is exactly
    # how work lands under the wrong office.
    #
    # `""` (not `-`) when the role is missing from § แกนความเป็นเจ้าของ: the
    # convention's own "not resolved" marker is written by the caller that
    # formats the id, not smuggled in as data.
    # Joined by slug, not by the cell text: § โมเดลต่อ role writes `CTO` and
    # § แกนความเป็นเจ้าของ writes `cto`. The ritual path (`_rituals`) already
    # joins them this way — one convention, not two.
    ownership = _parse_ownership(roles_file)
    for row in roles:
        row["office"] = (ownership.get(_slug(row["role"])) or {}).get("office", "")

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


#  effort values the CLI itself accepts (ADR-0032 §SD2) — anything else is a
#  typo in roles.md, not a value to guess a mapping for.
VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

# The tier whose model rejects `--effort` outright (ADR-0032 §SD6) — Haiku
# still uses `budget_tokens`, so a role parked there must never carry effort.
_NO_EFFORT_TIER = "light"


def _parse_role_tiers(path: Path, tiers: dict[str, str]) -> list[dict]:
    """role → default tier (+ effort, when the column is present), from the
    table under roles.md § โมเดลต่อ role.

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

        # ADR-0032 §SD1: positional, column 3 — same rationale as the tier
        # column itself (§SD5: an older 2-column table must keep dispatching
        # on model alone, so a missing/unknown cell means "no effort", not a
        # parse failure). §SD6: light never carries effort, full stop.
        effort: Optional[str] = None
        if tier != _NO_EFFORT_TIER and len(cells) > 2:
            candidate = _strip_md(cells[2]).lower()
            if candidate in VALID_EFFORTS:
                effort = candidate

        out.append({"role": role, "tier": tier, "model": tiers[tier], "effort": effort})
    return out


_OWNERSHIP_HEADING = "แกนความเป็นเจ้าของ"

# A discipline is a folder name under `team/`, and the register writes them as
# back-ticked slugs and nothing else. Matched by the shape we *accept* rather
# than by the shapes we reject (ADR-0043 §SD5): `qa`'s cell is an em-dash
# followed by a parenthesised pointer at `ways-of-working/`, which the split on
# `·` used to hand back as two disciplines that never existed. A reject-list
# written against today's wording of that pointer would let tomorrow's through.
_DISCIPLINE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# `dev` is read by both `senior-developer` and `developer` in the ownership
# table (roles.md § แกนความเป็นเจ้าของ) — the escalation is a judgment call the
# row itself does not carry, so an unqualified `team: dev` lands on the role
# that does routine in-slice work, not the one that owns system-wide calls.
_DISCIPLINE_TIE_BREAK = {"dev": "developer"}


def _slug(text: str) -> str:
    return re.sub(r"[\s_]+", "-", text.strip().lower())


def _parse_ownership(path: Path) -> dict[str, dict]:
    """role slug → the whole row, from roles.md § แกนความเป็นเจ้าของ.

    Same anchored-on-heading approach as `_parse_role_tiers`: the table's
    prose wording is free to change, but its position under this heading is
    the contract. Read once per call rather than cached alongside the tier
    table — this table is small and dispatch already re-reads roles.md.

    Every column comes off one walk because they are one row of one table: the
    office is the second cell of the same line the disciplines sit on, and
    reading them apart would be two parsers that can disagree about which rows
    are rows (ADR-0036 §SD3 leans on the office being right here, not guessed).
    ADR-0043 §SD2 extends that to the fourth cell — `บันทึกผลลงที่`, which had
    no reader at all until S33 — for exactly the same reason.

    Keys per role:
      office, disciplines             — unchanged, `_stations()` and
                                        role_activity.py stand on them
      disciplines_raw                 — the cell as roles.md writes it
      disciplines_dropped             — tokens that are not discipline-shaped,
                                        reported rather than silently binned
      records_raw                     — the fourth cell, verbatim. Resolving it
                                        against the tree needs the workspace
                                        root, which this function does not
                                        have; `role_register()` does that.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    out: dict[str, dict] = {}
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
        office = _slug(_strip_md(cells[1]))
        disciplines_cell = _strip_md(cells[2])
        if not role or set(role) <= set("-: "):
            continue  # separator row
        if role in ("role", "แกนความเป็นเจ้าของ"):
            continue  # header row
        disciplines: list[str] = []
        dropped: list[str] = []
        for token in disciplines_cell.split("·"):
            slug = _slug(token)
            if not slug or slug == "—":
                continue  # the register's own "this role holds none"
            (disciplines if _DISCIPLINE_SLUG.match(slug) else dropped).append(slug)
        out[role] = {
            "office": "" if set(office) <= set("-: ") else office,
            "disciplines": disciplines,
            "disciplines_raw": disciplines_cell,
            "disciplines_dropped": dropped,
            "records_raw": _cell_raw(cells, 3).strip(),
        }
    return out


def _parse_role_disciplines(path: Path) -> dict[str, list[str]]:
    """role slug → disciplines it owns. Thin view over `_parse_ownership`."""
    return {role: row["disciplines"] for role, row in _parse_ownership(path).items()}


# ── role register (ADR-0043) ─────────────────────────────────────────────────
#
# The seven-row table S33 asks the board to print. Nothing new is *measured*
# here: § แกนความเป็นเจ้าของ already had a reader (it just threw the fourth
# column away) and § โมเดลต่อ role already reaches the page as `dispatch`. What
# this adds is the fourth column's own answer — *does the place this role
# records into exist yet* — which needs the tree, and so cannot happen in the
# browser where the two registers are joined.

_ROLE_REGISTER_FILE = ("team-os", "people", "roles.md")

# `projects/<n>/docs/adr/` — the register writes a project name as a
# placeholder, the way the whole workspace does. Turned into a glob before it
# is resolved, and the resolved form travels next to the raw token so the
# substitution is never invisible on screen (ADR-0043 §SD3).
_PLACEHOLDER = re.compile(r"<[^>]*>")

# How many resolved paths a target carries into the payload before it just says
# how many more there are. This column is evidence that a pattern points at
# something real, not a file browser.
_PATH_SAMPLE = 6


def role_register(root: Path, ownership: Optional[dict] = None) -> dict:
    """The register as one table: seven roles, every column of the row.

    Args:
        root: absolute workspace root
        ownership: `_parse_ownership()` output, passed in when the caller
            already has it — `workspace_overview` reads it for the belt first.

    Row order is § แกนความเป็นเจ้าของ's own, not § โมเดลต่อ role's: the two
    tables are genuinely ordered differently, and this screen is the first one
    that prints both halves of a row side by side (ADR-0043 §SD7).
    """
    source = "/".join(_ROLE_REGISTER_FILE)
    if ownership is None:
        ownership = _parse_ownership(root.joinpath(*_ROLE_REGISTER_FILE))
    roles = [
        {
            "role": slug,
            "office": row.get("office", ""),
            "disciplines": row.get("disciplines", []),
            "disciplines_raw": row.get("disciplines_raw", ""),
            "disciplines_dropped": row.get("disciplines_dropped", []),
            "records": _resolve_records(root, row.get("records_raw", "")),
        }
        for slug, row in ownership.items()
    ]
    return {
        "source": source,
        "section": _OWNERSHIP_HEADING,
        "present": bool(roles),
        "reason": "" if roles else f"ไม่พบตาราง § {_OWNERSHIP_HEADING} ใน {source}",
        "roles": roles,
        # ADR-0043 §SD6 — S19's column, declared unread rather than parsed on
        # the way past. The statement is the reader's about itself, which is
        # why it is safe to hold here: `_parse_role_tiers` walks columns 1–3 of
        # § โมเดลต่อ role and skips this one on purpose (its own comment says
        # so). Anything about *what the column contains* stays in roles.md.
        "heavy_when": {
            "readable": False,
            "column": "ขึ้น heavy เมื่อ",
            "slice": "S19",
            "reason": (
                "`_parse_role_tiers()` อ่าน `§ โมเดลต่อ role` แค่คอลัมน์ 1–3 "
                "(role · default · effort) แล้วข้ามคอลัมน์นี้โดยเจตนา "
                "⇒ บอร์ดยังปักหมุด tier ตาม default เสมอ"
            ),
        },
    }


_SURFACE_PREFIX = "surface:"


def _resolve_records(root: Path, raw: str) -> dict:
    """The `บันทึกผลลงที่` cell, and whether the tree carries what it names.

    The distinction this function exists for (ADR-0043 §SD4): a cell with no
    path-shaped token is the register saying *this role does not record into a
    file* — `senior-developer` and `developer` close in a commit body, `qa` in
    a PR thread — which is a different answer from *the file is not written
    yet*, and rendering both as an empty cell sends a reader off to create
    files roles.md never asked for. `kind` carries that apart.

    ADR-0043 Amendment (2) — `kind` moved from the cell to each *target*
    (§A5): a cell can name both a file and a non-file surface at once
    (`tech-lead`'s ADR/HLD + a signed-off artifact URL), and the old
    all-or-nothing `kind` could only ever print half of such a row. The cell
    keeps a summary value — `'files'` · `'not-files'` · the new `'mixed'` — so
    a reader written against the two original values still gets a true answer
    for every row that only ever named one kind.
    """
    targets: list[dict] = []
    rejected: list[str] = []
    consumed: list[str] = []
    for token in _CODE_TOKEN.findall(raw):
        # Checked before `_looks_like_path()` (§A6) so a slug that happens to
        # contain `/` in the future is never mistaken for a glob.
        if token.startswith(_SURFACE_PREFIX):
            targets.append(_resolve_surface_target(token))
            consumed.append(token)
            continue
        if not _looks_like_path(token):
            continue  # `platform-core` in devops' cell is a project, not a place — stays in note (§A7)
        if not _safe_glob(token):
            rejected.append(token)
            consumed.append(token)
            continue
        targets.append(_resolve_record_target(root, token))
        consumed.append(token)

    kinds = {t["kind"] for t in targets}
    if not targets:
        kind = "not-files"
    elif kinds == {"file"}:
        kind = "files"
    else:
        kind = "mixed"

    # §A7 — only the backtick spans that became a target or were rejected are
    # cut from the note; a token that fell through both checks above (neither
    # path-shaped nor a surface) is prose, and must survive here or it
    # vanishes from the screen with nothing saying so.
    note_source = raw
    for token in consumed:
        note_source = note_source.replace(f"`{token}`", "", 1)

    return {
        "raw": raw,
        "text": _strip_md(raw),
        "kind": kind,
        "targets": targets,
        "rejected": rejected,
        # Same shape `_signatures` carries its leftover prose in: the wording
        # reaches the screen from roles.md rather than from a sentence retyped
        # here (`ADR เมื่อการตัดสินผูกทั้งระบบ` is the whole of what makes
        # senior-developer's line different from developer's).
        "note": _strip_md(note_source).strip(" ·—-"),
    }


def _resolve_surface_target(token: str) -> dict:
    """A `surface:<slug>` token (ADR-0043 Amendment (2) §A6) — a place this
    role records into that is not a file in the tree (a PR body, an external
    Sheet/Doc). Not glob'd, not counted (§A8): the board reads only files
    committed at HEAD, and this one's real location is not one. No slug list
    is hard-coded here — the meaning comes from the prose in the cell."""
    return {
        "token": token,
        "kind": "surface",
        "slug": token[len(_SURFACE_PREFIX) :],
    }


def _resolve_record_target(root: Path, token: str) -> dict:
    """One pattern, resolved at **both** levels — always.

    ADR-0043 §SD3, and the one place this reader deliberately parts company
    with `_count_target()` (ADR-0041 §SD6): that one tries the root, and stops
    if it matches, on the measured grounds that no § 7.2 token collides. This
    register is where the collision is real — `docs/runbooks/` exists at the
    workspace root *and* under a project — so stopping at the first hit would
    delete half the answer with nothing on screen saying it had been deleted.
    """
    pattern = _PLACEHOLDER.sub("*", token).strip().rstrip("/")
    at_root = _rel_matches(root, root, pattern)
    hits: list[str] = []
    holders: list[dict] = []
    projects_dir = root / "projects"
    if projects_dir.is_dir():
        for child in sorted(projects_dir.iterdir()):
            if not child.is_dir():
                continue
            found = _rel_matches(root, child, pattern)
            if found:
                holders.append({"project": child.name, **_sample(found)})
                hits.extend(found)
    project = _sample(sorted(hits))
    project["projects"] = len(holders)
    # ADR-0043 Amendment (S34) — the per-project split, so the picker can
    # answer "this project's ⑤" exactly. Kept beside the aggregate rather than
    # replacing it: the register itself is workspace-level (seven roles, one
    # table), and a filtered view must never look like the whole answer. The
    # browser cannot derive this from the aggregate — `paths` is capped, so
    # filtering a sample would undercount and quietly say zero.
    project["by_project"] = holders
    return {
        "token": token,
        "kind": "file",
        # Printed beside the token: a `<n>` that became `*` is a substitution
        # the reader gets to see, not one they have to know about.
        "glob": pattern,
        "levels": {"workspace": _sample(at_root), "project": project},
    }


def _rel_matches(root: Path, base: Path, pattern: str) -> list[str]:
    """Workspace-relative posix paths `pattern` matches under `base`."""
    if not pattern:
        return []
    try:
        found = list(base.glob(pattern))
    except (ValueError, OSError):
        return []
    out = []
    for match in found:
        try:
            out.append(match.relative_to(root).as_posix())
        except ValueError:
            continue
    return sorted(out)


def _sample(paths: list[str]) -> dict:
    return {
        "have": len(paths),
        "paths": paths[:_PATH_SAMPLE],
        "more": max(0, len(paths) - _PATH_SAMPLE),
    }


def _default_role_for_team(
    team: str, role_disciplines: dict[str, list[str]], roles: list[dict]
) -> Optional[str]:
    """Which of `dispatch.roles` a role-ish string points to.

    Despite the name, this resolves any string written the way `team:`
    frontmatter is — sometimes a discipline (`dev`, `forge`), sometimes a role
    name directly (`product-owner`) — which is also exactly the latitude a
    row's optional `role` cell gets (ADR-0035 §SD2: one resolver for both, not
    a second table that can drift from this one). Both forms are tried; an
    unrecognised value resolves to nothing rather than a guess, so the caller
    falls back to its own default (ADR-0033 §SD1 for the file level, ADR-0035
    §SD3 for the row level).
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
    """Sets each project's `default_role`, then each of its slices' `role`.

    `default_role`: the dispatch box should open on the role the project's own
    work belongs to, not the first row of a table it has nothing to do with
    (ADR-0033, closes risks.md S-09).

    Each slice's `role` starts as the raw text of its optional `role` column
    cell (`_parse_slices`) and is overwritten here with the resolved role —
    that row's own override when it resolves, else the project's
    `default_role` (ADR-0035 §SD3). A card's `role` is therefore always either
    a real `dispatch.roles[].role` value or `None`, never unresolved text.

    Also sets each slice's `handoff` (S40): the raw cell is still on hand here
    — and only here, before it is overwritten below — so this is the one place
    that can resolve both "role now" and "role at the parent of this file's
    last commit" through the same `dispatch`/`role_disciplines` map and tell
    whether they differ.
    """
    if not dispatch.get("present"):
        for p in projects:
            p["default_role"] = None
            for s in p["slices"]:
                s["role"] = None
        return
    role_disciplines = _parse_role_disciplines(root / "team-os" / "people" / "roles.md")
    for p in projects:
        default_role = _default_role_for_team(
            p.get("team", ""), role_disciplines, dispatch["roles"]
        )
        p["default_role"] = default_role
        slices_path = root / "projects" / p["name"] / "slices.md"
        prev_text = _previous_slices_text(root, slices_path)
        prev_raw_by_id = (
            {row.id: row.role for row in _parse_slices_text(prev_text)}
            if prev_text is not None
            else None
        )
        for s in p["slices"]:
            raw = s.get("role", "")
            effective = (
                _default_role_for_team(raw, role_disciplines, dispatch["roles"])
                or default_role
            )
            if prev_raw_by_id is not None and s["id"] in prev_raw_by_id:
                prev_effective = (
                    _default_role_for_team(
                        prev_raw_by_id[s["id"]], role_disciplines, dispatch["roles"]
                    )
                    or default_role
                )
                if prev_effective and effective and prev_effective != effective:
                    s["handoff"] = {"from": prev_effective, "to": effective}
            s["role"] = effective


def _strip_md(cell: str) -> str:
    """Plain text from a markdown table cell — links keep their label."""
    out = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    out = out.replace("**", "").replace("`", "")
    out = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", out)
    return out.strip()


# ── ritual registry (ADR-0036) ───────────────────────────────────────────────
#
# The second table the board may dispatch from. ADR-0036 §SD1 replaced the
# hard-coded answer to "what can this board hand out" ("rows of slices.md") with
# three criteria; team-os/ways-of-working/rituals.md is the second register that
# meets them. Everything below reads that file — it is never mirrored here, for
# the same reason `_scan_dispatch` refuses to mirror roles.md.

_RITUALS_FILE = ("team-os", "ways-of-working", "rituals.md")

# rituals.md pins these three column headers as untranslated English keywords —
# the Thai prose around them stays free to reword (its own § note, and the rule
# ADR-0035 §SD1 set for the `role` header of slices.md). So the table is found
# by its header cells, not by the heading above it: a reworded heading must not
# turn dispatch off, but a renamed column legitimately does.
_RITUAL_COLUMNS = ("key", "role", "client")

# Header of the column holding each ritual's definition pointer (runbook + step
# numbers). Thai, because no English keyword was declared for it — so it is read
# as best-effort: ADR-0036 §SD3 lists exactly three button-blocking columns, and
# this is not one of them. A missing pointer degrades the prompt, never the button.
_RITUAL_READS_COLUMN = "นิยามอยู่ที่"


@dataclass
class Ritual:
    key: str  # matched against a calendar bar's label (ADR-0036 §SD2)
    name: str  # the จังหวะ cell, for display
    role: Optional[str]  # a real dispatch.roles[].role, or None
    client: str
    office: Optional[str]
    assignment: Optional[str]  # <client>/<office>/<role>/<key-slug> — 4 full segments
    reads: str  # the ritual's own definition pointer, "" when unresolved
    dispatchable: bool
    missing: list[str]  # which of key/role/client did not resolve


def ritual_registry(repo_root: str, use_cache: bool = True) -> dict:
    """Daily rituals a calendar bar can dispatch (ADR-0036 §SD1 · §SD3).

    Only rows that declare a `key` are returned: a ritual with no key is not
    reachable from a bar *and that is correct* — three of the eight have no
    clock at all, so they are never drawn. Rows that declare a key but cannot
    resolve a role or a client come back with `dispatchable=False` and the
    missing fields named, because §SD6 still has to report them as declared
    but unmapped. There is no default for any field: guessing `internal` for a
    missing client would file the work under the wrong account permanently.
    """
    root = Path(repo_root)
    path = root.joinpath(*_RITUALS_FILE)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {
            "present": False,
            "reason": f"ไม่พบ {'/'.join(_RITUALS_FILE)}",
            "source": "/".join(_RITUALS_FILE),
            "rituals": [],
        }

    tables = _markdown_tables(text)
    found = _ritual_table(tables)
    if found is None:
        return {
            "present": False,
            "reason": (
                "ไม่พบตารางที่มีคอลัมน์ "
                + " · ".join(f"`{c}`" for c in _RITUAL_COLUMNS)
                + f" ใน {path.name}"
            ),
            "source": "/".join(_RITUALS_FILE),
            "rituals": [],
        }
    index, rows = found

    try:
        payload = workspace_overview(repo_root, use_cache=use_cache)
    except ValueError:
        payload = {}
    dispatch = payload.get("dispatch") or {}
    roles = dispatch.get("roles", []) if dispatch.get("present") else []

    ownership = _parse_ownership(root / "team-os" / "people" / "roles.md")
    disciplines = {role: row["disciplines"] for role, row in ownership.items()}
    definitions = _ritual_definitions(tables)

    out: list[dict] = []
    for cells in rows:
        key = _cell(cells, index["key"])
        if not key or key == "—":
            continue  # no key = not reachable from a bar, by design (§SD3 note 3)
        name = _strip_md(cells[0]) if cells else key
        client = _cell(cells, index["client"])
        role = _default_role_for_team(_cell(cells, index["role"]), disciplines, roles)
        office = (ownership.get(_slug(role or ""), {}) or {}).get("office") or ""

        missing = [
            field
            for field, value in (("role", role), ("client", client), ("office", office))
            if not value or value == "—"
        ]
        assignment = (
            f"{client}/{office}/{_slug(role or '')}/{_task_slug(key)}"
            if not missing
            else None
        )
        out.append(
            asdict(
                Ritual(
                    key=key,
                    name=name or key,
                    role=role,
                    client=client,
                    office=office or None,
                    assignment=assignment,
                    reads=_ritual_reads(definitions, key),
                    dispatchable=not missing,
                    missing=missing,
                )
            )
        )

    return {
        "present": True,
        "source": "/".join(_RITUALS_FILE),
        "rituals": out,
    }


def _cell(cells: list[str], i: int) -> str:
    return _strip_md(cells[i]) if i < len(cells) else ""


def _task_slug(text: str) -> str:
    """`EOD checkpoint` → `eod-checkpoint` — the id's fourth segment.

    Same shape as the slug src/lib/dispatch-prompt.ts builds for a slice, so
    both registers produce ids `git log --grep '^Assignment:'` can chase.
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """(header cells, body rows) for every pipe table in a document.

    rituals.md holds two tables that matter and several that do not, and which
    is which is decided by their *columns* — so this hands back all of them and
    lets the caller choose, rather than anchoring on a heading whose Thai wording
    the file explicitly reserves the right to reword.
    """
    tables: list[tuple[list[str], list[list[str]]]] = []
    header: Optional[list[str]] = None
    rows: list[list[str]] = []
    in_body = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_body and header is not None and set("".join(cells)) <= set("-: "):
                in_body = True  # separator row — the header above it is real
                continue
            if in_body:
                rows.append(cells)
            else:
                header, rows = cells, []
            continue
        if in_body and header is not None:
            tables.append((header, rows))
        header, rows, in_body = None, [], False

    if in_body and header is not None:
        tables.append((header, rows))
    return tables


def _ritual_table(
    tables: list[tuple[list[str], list[list[str]]]],
) -> Optional[tuple[dict[str, int], list[list[str]]]]:
    """The first table whose header names all three pinned columns."""
    for header, rows in tables:
        index: dict[str, int] = {}
        for i, cell in enumerate(header):
            name = _strip_md(cell).strip().lower()
            if name in _RITUAL_COLUMNS and name not in index:
                index[name] = i
        if all(column in index for column in _RITUAL_COLUMNS):
            return index, rows
    return None


def _ritual_definitions(
    tables: list[tuple[list[str], list[list[str]]]],
) -> list[tuple[str, str]]:
    """(ritual name, definition pointer) from the table carrying that column."""
    for header, rows in tables:
        for i, cell in enumerate(header):
            if _strip_md(cell).strip() == _RITUAL_READS_COLUMN:
                return [
                    (_strip_md(r[0]), _reads_pointer(r[i]) if i < len(r) else "")
                    for r in rows
                    if r
                ]
    return []


_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def _reads_pointer(cell: str) -> str:
    """A definition cell, rewritten so its paths are workspace-root relative.

    The cell is written for a human reading rituals.md, so its links point out
    of `team-os/ways-of-working/` with `../../`, and some show a bare file name
    as the link text. A dispatched session opens files from the workspace root,
    so the *target* is what it needs — resolved against the file the link was
    written in, and kept alongside whatever prose follows it (the step numbers).
    """

    def rewrite(m: re.Match) -> str:
        target = m.group(2).strip()
        if not target or target.startswith(("http://", "https://", "#")):
            return m.group(1)
        return posixpath.normpath(posixpath.join("team-os/ways-of-working", target))

    return _strip_md(_LINK_RE.sub(rewrite, cell))


def _ritual_reads(definitions: list[tuple[str, str]], key: str) -> str:
    """The definition pointer for one ritual, joined on the key — not the name.

    The two tables name the same ritual with different prose (`EOD-prep +
    forge pre-check` vs `EOD-prep + forge pre-check F1–F5`), so joining on the
    name would drop a row on a wording change nobody would notice. The key is
    the identifier both tables carry, and it is matched the same way §SD2
    matches a bar: as a substring, and ambiguity yields nothing rather than a
    pick.
    """
    hits = [reads for name, reads in definitions if key.lower() in name.lower()]
    return hits[0] if len(hits) == 1 else ""


# ── pipeline surfaces (ADR-0041) ─────────────────────────────────────────────
#
# The two belt-shaped columns of the /work panel: which station a role holds,
# and where it signs when a project closes. Both are read from
# docs/sops/sop-pipeline-handoff.md and are functions of HEAD, which is why they
# ride this module's payload rather than the windowed one (§SD1) — role_activity
# stays a count over a *period*, exactly as ADR-0039 §SD1 drew the line.
#
# Nothing about the belt is pinned here. The station `close` was written into
# that SOP on 2026-09-10, one day before this reader existed; a list held in
# this file would already have been wrong, and wrong silently.

_PIPELINE_FILE = ("docs", "sops", "sop-pipeline-handoff.md")

# The stage table is found by its *column headers*, not by the heading above it
# (`## Scope — the pipeline and its batons`) — the same choice ADR-0036 §SD2
# made for rituals.md and the opposite of _SLOTS_HEADING. `From stage` / `To
# stage` are English keywords the table itself owns; the heading is prose the
# SOP's author may re-word. A renamed column legitimately turns this column off;
# a re-worded heading must not.
_STAGE_COLUMNS = ("from stage", "to stage")

# § 7.2's seven signatures. `ลงที่ไหน` is Thai because no English keyword was
# declared for it — read as-is rather than guessed at from position, since the
# `role` column alone would also match the stage table's neighbours.
_SIGNATURE_COLUMNS = ("role", "ลงที่ไหน")

_CODE_TOKEN = re.compile(r"`([^`]+)`")


def pipeline_surfaces(root: Path, ownership: Optional[dict] = None) -> dict:
    """The belt as two tables: stations per role, and closing surfaces per role.

    Args:
        root: absolute workspace root
        ownership: `_parse_ownership()` output, passed in when the caller
            already has it. The register is always roles.md — never the set of
            stage names this SOP happens to contain (ADR-0039 §SD2).

    The two halves fail independently (§SD7): a renamed stage column blanks the
    station column and leaves the signatures readable, because they are two
    tables that merely share a file.
    """
    source = "/".join(_PIPELINE_FILE)
    path = root.joinpath(*_PIPELINE_FILE)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        missing = f"ไม่พบ {source}"
        return {
            "source": source,
            "stations": {
                "present": False,
                "reason": missing,
                "stages": [],
                "per_role": {},
            },
            "signatures": {
                "present": False,
                "reason": missing,
                "rows": [],
                "projects": _project_count(root),
                "enforcement": _no_enforcement(),
            },
        }

    if ownership is None:
        ownership = _parse_ownership(root / "team-os" / "people" / "roles.md")

    return {
        "source": source,
        "stations": _stations(text, ownership, source),
        "signatures": _signatures(text, root, source),
    }


def _stations(text: str, ownership: dict[str, dict], source: str) -> dict:
    """Stage → the roles that declare its discipline, in the SOP's own order.

    The map is `_parse_ownership()` turned around: a stage belongs to *every*
    role whose discipline list names it, so `dev` lands on both
    `senior-developer` and `developer` (§SD3). `_DISCIPLINE_TIE_BREAK` is
    deliberately not used — it answers "which single role does an unqualified
    `team: dev` dispatch to", and narrowing a register to one row would print a
    screen that argues with roles.md.
    """
    found = _table_by_columns(_markdown_tables(text), _STAGE_COLUMNS)
    if found is None:
        return {
            "present": False,
            "reason": (
                "ไม่พบตารางที่มีคอลัมน์ "
                + " · ".join(f"`{c}`" for c in _STAGE_COLUMNS)
                + f" ใน {source}"
            ),
            "stages": [],
            "per_role": {},
        }
    index, rows = found

    # Both columns, because the far end of the belt appears as somebody's `To`
    # one revision before it earns a row of its own — which is exactly how
    # `close` entered on 2026-09-10.
    stages: list[str] = []
    for cells in rows:
        for column in _STAGE_COLUMNS:
            i = index[column]
            if i >= len(cells):
                continue
            for token in _CODE_TOKEN.findall(cells[i]):
                if token not in stages:
                    stages.append(token)

    holders = {
        stage: [
            slug
            for slug, row in ownership.items()
            if stage in row.get("disciplines", [])
        ]
        for stage in stages
    }
    return {
        "present": True,
        "reason": "",
        # A stage nobody holds keeps its row: `close` is a gate, not a
        # discipline (the SOP says so itself), and dropping it off screen is
        # the same silence ADR-0039 §SD2 refused for a role with zero commits.
        "stages": [{"stage": s, "roles": holders[s]} for s in stages],
        "per_role": {
            slug: [s for s in stages if slug in holders[s]] for slug in ownership
        },
    }


def _signatures(text: str, root: Path, source: str) -> dict:
    """Each role's closing surface, and whether that surface exists yet.

    A cell's back-ticked tokens are its targets *when they are shaped like a
    path*; the prose left over is the row's note, carried verbatim so §7.3's
    "another file may answer this line" reaches the screen from the SOP rather
    than from a sentence retyped here. `qa`'s cell names `product-owner`
    mid-sentence, which is why shape decides and back-ticks alone do not.
    """
    block = _signature_block(text)
    total = _project_count(root)
    if block is None:
        return {
            "present": False,
            "reason": (
                "ไม่พบตารางที่มีคอลัมน์ "
                + " · ".join(f"`{c}`" for c in _SIGNATURE_COLUMNS)
                + f" ใน {source}"
            ),
            "rows": [],
            "projects": total,
            "enforcement": _no_enforcement(),
        }
    index, rows, trailer = block

    out: list[dict] = []
    for cells in rows:
        role = _slug(_strip_md(_cell(cells, index["role"])))
        if not role or set(role) <= set("-: "):
            continue
        cell = _cell_raw(cells, index[_SIGNATURE_COLUMNS[1]])
        targets: list[dict] = []
        rejected: list[str] = []
        for token in _CODE_TOKEN.findall(cell):
            if not _looks_like_path(token):
                continue  # a role name, a flag, anything that is not a location
            if not _safe_glob(token):
                rejected.append(token)
                continue
            targets.append(_count_target(root, token, total))
        out.append(
            {
                "role": role,
                "closes": _strip_md(_cell(cells, index["role"] + 1)),
                "targets": targets,
                "rejected": rejected,
                "note": _strip_md(_CODE_TOKEN.sub("", cell)).strip(" ·—-"),
            }
        )

    return {
        "present": True,
        "reason": "",
        "rows": out,
        "projects": total,
        "enforcement": _enforcement(root, trailer, [r["role"] for r in out]),
    }


def _signature_block(
    text: str,
) -> Optional[tuple[dict[str, int], list[list[str]], str]]:
    """The §7.2 table plus the paragraph that follows it.

    Walked line by line rather than through `_markdown_tables`, because the
    prose *after* the table is half the answer — it is where the SOP declares
    which of the seven lines a machine actually enforces — and a table parser
    that returns only cells has thrown that position away.
    """
    lines = text.splitlines()
    index: Optional[dict[str, int]] = None
    rows: list[list[str]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            candidate: dict[str, int] = {}
            for n, cell in enumerate(cells):
                name = _strip_md(cell).strip().lower()
                for column in _SIGNATURE_COLUMNS:
                    if name == column and column not in candidate:
                        candidate[column] = n
            if all(column in candidate for column in _SIGNATURE_COLUMNS):
                index = candidate
                i += 1
                break
        i += 1
    if index is None:
        return None

    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if not set("".join(cells)) <= set("-: "):  # skip the separator row
            rows.append(cells)
        i += 1

    trailer: list[str] = []
    while i < len(lines) and not lines[i].strip().startswith("#"):
        trailer.append(lines[i])
        i += 1
    return index, rows, "\n".join(trailer).strip()


def _no_enforcement() -> dict:
    return {
        "declared": False,
        "roles": [],
        "mechanism": "",
        "mechanism_exists": False,
        "enforced": 0,
        "unenforced": 0,
        "total": 0,
    }


def _enforcement(root: Path, trailer: str, roles: list[str]) -> dict:
    """Which of the seven lines a machine actually holds — read from §7.2's own
    warning paragraph.

    Two things come out of it: the role slugs it names, and the first link it
    points at (the mechanism). The count of *unenforced* lines is then computed
    from those names — never lifted from the sentence's own "5 ใน 7". The day
    the hook covers one more line, the screen moves because the named roles
    moved, not because somebody remembered to edit a number in prose.
    """
    if not trailer:
        return _no_enforcement()
    named = [
        role
        for role in roles
        if any(_slug(t) == role for t in _CODE_TOKEN.findall(trailer))
    ]
    links = _LINK_RE.findall(trailer)
    mechanism = ""
    if links:
        target = links[0][1].strip()
        if not target.startswith(("http://", "https://", "#")):
            mechanism = posixpath.normpath(
                posixpath.join(posixpath.dirname("/".join(_PIPELINE_FILE)), target)
            )
    return {
        "declared": bool(named or mechanism),
        "roles": named,
        "mechanism": mechanism,
        # Reported, not asserted: a mechanism the SOP names but the tree does
        # not carry is a finding, and it belongs on screen rather than as a
        # silent False in the enforced column.
        "mechanism_exists": bool(mechanism) and (root / mechanism).exists(),
        "enforced": len(named),
        "unenforced": len(roles) - len(named),
        "total": len(roles),
    }


def _looks_like_path(token: str) -> bool:
    """A location, not a name. `docs/design/*` and `rollout.md` are; the
    `product-owner` sitting mid-sentence in `qa`'s cell is not."""
    return "/" in token or "*" in token or token.endswith(".md")


def _safe_glob(token: str) -> bool:
    """Refuse anything that could walk out of the workspace. The pattern comes
    from a tracked file, so this is a guard against a mistake rather than an
    attacker — but a reader that globs `../../../etc/*` on a re-word is not a
    mistake anyone would catch by reading the diff of a markdown file."""
    return not token.startswith(("/", "~")) and ".." not in token.split("/")


def _count_target(root: Path, token: str, total: int) -> dict:
    """How much of the workspace already carries this surface.

    The level is decided by trying the workspace root first: a pattern that
    matches there is a workspace-level location (`meta/adr-*.md`), and anything
    else is read as project-relative (`rollout.md`, `docs/design/*`). Measured
    2026-09-10: none of the project-relative tokens in §7.2 collide with a real
    path at the root, so the order never has to be argued about — and if one
    ever does, the payload says which level it resolved at.
    """
    at_root = list(root.glob(token))
    if at_root:
        return {
            "token": token,
            "level": "workspace",
            "have": len(at_root),
            "total": None,  # a file count has no denominator
        }
    projects_dir = root / "projects"
    have = 0
    if projects_dir.is_dir():
        for child in sorted(projects_dir.iterdir()):
            if child.is_dir() and any(child.glob(token)):
                have += 1
    return {"token": token, "level": "project", "have": have, "total": total}


def _project_count(root: Path) -> int:
    """Every folder under projects/, not only the ones the board draws a card
    for. The close gate is every project's (§7.1 measures itself the same way,
    at 3/33) — the one place the board counts past the edge of its own cards,
    and the screen says so."""
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return 0
    return sum(1 for child in projects_dir.iterdir() if child.is_dir())


def _cell_raw(cells: list[str], i: int) -> str:
    return cells[i] if i < len(cells) else ""


def _table_by_columns(
    tables: list[tuple[list[str], list[list[str]]]], columns: tuple[str, ...]
) -> Optional[tuple[dict[str, int], list[list[str]]]]:
    """First table whose header *starts* a cell with each named column.

    Prefix-matched rather than exact, unlike `_ritual_table`: these headers
    carry a parenthetical gloss the SOP wrote for its human readers
    (`From stage (discipline)`), and that gloss is prose which may be re-worded.
    """
    for header, rows in tables:
        index: dict[str, int] = {}
        for i, cell in enumerate(header):
            name = _strip_md(cell).strip().lower()
            for column in columns:
                if name.startswith(column) and column not in index:
                    index[column] = i
        if all(column in index for column in columns):
            return index, rows
    return None
