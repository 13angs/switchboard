"""Who actually shipped — the board's third data source (ADR-0039).

Single public entry: role_activity() — reads `git log` across the workspace and
the project code repos, pulls the `Assignment:` trailer's role slot out of each
commit, and pairs the count with the rows that role still holds on the board.

Why a third source: `workspace.py` reads committed *files*, so it answers "which
rows are left, and who holds them" and nothing about who moved. Answering
"which role shipped nothing at all this fortnight" (team-os/GAPS.md `G-25`)
meant running `git log --grep '^Assignment: '` by hand, every time.

Design constraints (ADR-0039):
  - Own module, one public entry, compute on the fly — the analytics.py pattern
    (ADR-0013 §SD1), same as workspace.py.
  - Standard library only — tests/test_stdlib_purity.py enforces this.
  - The register of roles is roles.md, never the set of values git log happens
    to contain (§SD2): a role with no commits has to be a *zero row*, not an
    absent one, or the gap disappears along with it.
  - Every number carries how it was read (§SD3 · §SD5): a count resolved from a
    pre-cutover discipline slug, and a commit that carried no id at all, are
    both visible rather than folded into the total.
  - No cache (§SD7): a window is not a function of one HEAD, and the whole scan
    measured 0.47s over ~1,300 commits.

Known limit, inherited from `git log --since` and not worked around: git stops
walking a branch once it meets a commit older than the cutoff, so a commit
whose date sits *behind* one of its own descendants — a rebase that kept an old
author date, a machine with a wrong clock — can be missed. The workspace's own
manual query (`git log --grep '^Assignment: '`) has exactly the same behaviour,
so this reads what the by-hand count reads; it is a floor on the number, never
an inflation of it.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import workspace

# The owner's day is the unit a person reads, not the container's UTC —
# docs/workspace-operating-rules.md § Time / clock.
_OWNER_TZ = timezone(timedelta(hours=7))
_OWNER_OFFSET = "+07:00"

# Board columns that mean "this row is still someone's to finish". `done` and
# `off` are excluded: a finished piece and a day with no work are not backlog.
OPEN_COLUMNS = ("todo", "running", "next", "owner")

# The id's shape — `<client>/<office>/<role>/<task-slug>`, four segments, the
# role in the third (docs/sops/sop-work-ownership.md). Anchored per line so a
# body that merely *quotes* the trailer mid-paragraph does not count.
_ASSIGNMENT = re.compile(r"^Assignment:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

_RECORD_SEP = "\x1f"
_FIELD_SEP = "\x1e"

DEFAULT_DAYS = 14
MAX_DAYS = 365


def role_activity(repo_root: str, days: int = DEFAULT_DAYS) -> dict:
    """Commits shipped per role in the last `days`, paired with rows still open.

    Args:
        repo_root: absolute workspace root
        days: window length in owner-timezone days, inclusive of today

    Raises:
        ValueError: repo_root is not a directory, or `days` is out of range.
    """
    root = Path(repo_root)
    if not root.is_dir():
        raise ValueError(f"repo_root is not a directory: {repo_root}")
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")

    roles_file = root / "team-os" / "people" / "roles.md"
    ownership = workspace._parse_ownership(roles_file)
    if not ownership:
        # Fail dark, like _scan_dispatch: without the register there is no list
        # of roles to be silent *about*, and inventing one from git log is the
        # exact failure §SD2 exists to prevent.
        return {
            "present": False,
            "reason": f"ไม่พบตาราง role → office ใน {roles_file.name}",
            "source": "team-os/people/roles.md",
            "repo": str(root),
        }

    since = _since_date(days)
    scanned, per_role, unresolved = _scan_repos(root, ownership, since)
    open_rows, open_unassigned = _open_rows(root, ownership)

    roles = []
    for slug, row in ownership.items():
        counted = per_role.get(slug, {})
        direct = counted.get("direct", 0)
        legacy = counted.get("legacy", 0)
        open_counts = open_rows.get(slug, {c: 0 for c in OPEN_COLUMNS})
        open_total = sum(open_counts.values())
        roles.append(
            {
                "role": slug,
                "office": row.get("office") or None,
                "commits": direct + legacy,
                "direct": direct,
                "legacy": legacy,
                "legacy_slugs": counted.get("slugs", {}),
                "open": open_counts,
                "open_total": open_total,
                "silent": direct + legacy == 0 and open_total == 0,
            }
        )

    return {
        "present": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(root),
        "source": "team-os/people/roles.md",
        "window": {"days": days, "since": since, "tz": _OWNER_OFFSET},
        "roles": roles,
        "repos": scanned,
        "unresolved": unresolved,
        "open_unassigned": open_unassigned,
        "totals": {
            "commits": sum(r["commits"] for r in scanned),
            "with_assignment": sum(r["with_assignment"] for r in scanned),
        },
    }


# ── internals ────────────────────────────────────────────────────────────────


def _since_date(days: int) -> str:
    """First day of the window, inclusive, as the owner's calendar reads it."""
    today = datetime.now(_OWNER_TZ).date()
    return (today - timedelta(days=days - 1)).isoformat()


def _git(cwd: Path, *args: str) -> Optional[str]:
    """stdout of a git command, or None when it cannot run / fails.

    A missing git, a directory that is not a checkout, and a git that errors
    all collapse to the same answer on purpose: the caller's next move is
    identical — skip this candidate rather than report a zero that looks real.
    """
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _repos(root: Path) -> list[Path]:
    """The workspace plus every project code repo that is its own git root.

    ADR-0039 §SD4. Candidates come from `projects/README.md § Repos` — the
    `repos/<name>/` standard and the grandfathered `repo/` — and are then
    filtered by their *git common dir*, resolved to an absolute path, with the
    first sighting winning and `root` always first.

    That single filter catches two different impostors:
      - a plain directory that merely sits inside the workspace (measured
        2026-09-09: `projects/claude-code-webapp/repo` and
        `projects/marketplace-automation/repo` both are) — its common dir is
        the workspace's own `.git`, so `git log` there returns the workspace's
        history and would count every commit a second time;
      - a linked worktree under `repos/<name>.worktrees/` — its common dir is
        the parent repo's, so it shares every commit.

    `--show-toplevel` would catch only the first: a worktree answers with
    itself and walks straight through.
    """
    candidates = [root]
    projects_dir = root / "projects"
    if projects_dir.is_dir():
        for child in sorted(projects_dir.iterdir()):
            if not child.is_dir():
                continue
            repos_dir = child / "repos"
            if repos_dir.is_dir():
                candidates += sorted(p for p in repos_dir.iterdir() if p.is_dir())
            grandfathered = child / "repo"
            if grandfathered.is_dir():
                candidates.append(grandfathered)

    seen: set[str] = set()
    found: list[Path] = []
    for candidate in candidates:
        out = _git(candidate, "rev-parse", "--git-common-dir")
        if out is None:
            continue
        # git answers relative to the cwd it ran in, so resolve it there.
        common = str((candidate / out.strip()).resolve())
        if common in seen:
            continue
        seen.add(common)
        found.append(candidate)
    return found


def _scan_repos(
    root: Path, ownership: dict[str, dict], since: str
) -> tuple[list[dict], dict[str, dict], dict]:
    """Walk every countable repo once, tallying roles and what did not resolve."""
    disciplines = {slug: row["disciplines"] for slug, row in ownership.items()}
    # `_default_role_for_team` returns whatever string it was handed as the
    # role name, so feeding it slugs makes it answer in slugs — the form the
    # id's third slot is written in (§SD2).
    role_list = [{"role": slug} for slug in ownership]

    scanned: list[dict] = []
    per_role: dict[str, dict] = {}
    unresolved: list[dict] = []

    for repo in _repos(root):
        log = _git(
            repo,
            "log",
            "--no-merges",
            f"--since={since} 00:00:00 {_OWNER_OFFSET}",
            f"--pretty=format:%H{_FIELD_SEP}%B{_RECORD_SEP}",
        )
        if log is None:
            continue

        commits = 0
        with_assignment = 0
        for record in log.split(_RECORD_SEP):
            if _FIELD_SEP not in record:
                continue
            sha, body = record.split(_FIELD_SEP, 1)
            sha = sha.strip()
            commits += 1

            ids = _ASSIGNMENT.findall(body)
            if ids:
                with_assignment += 1

            # A commit that names two roles counts once for each, never twice
            # for one — PRs with several ids exist (the reason the workspace
            # merges those rather than squashing them).
            credited: set[str] = set()
            for ident in ids:
                slug, kind, source = _resolve(ident, ownership, disciplines, role_list)
                if slug is None:
                    unresolved.append(
                        {
                            "sha": sha[:8],
                            "repo": _rel(root, repo),
                            "assignment": ident,
                        }
                    )
                    continue
                if slug in credited:
                    continue
                credited.add(slug)
                bucket = per_role.setdefault(
                    slug, {"direct": 0, "legacy": 0, "slugs": {}}
                )
                bucket[kind] += 1
                if kind == "legacy":
                    bucket["slugs"][source] = bucket["slugs"].get(source, 0) + 1

        scanned.append(
            {
                "path": _rel(root, repo),
                "commits": commits,
                "with_assignment": with_assignment,
            }
        )

    return (
        scanned,
        per_role,
        {"count": len(unresolved), "samples": unresolved[:5]},
    )


def _rel(root: Path, repo: Path) -> str:
    try:
        rel = repo.relative_to(root)
    except ValueError:
        return str(repo)
    return str(rel) if str(rel) != "." else "."


def _resolve(
    ident: str,
    ownership: dict[str, dict],
    disciplines: dict[str, list[str]],
    role_list: list[dict],
) -> tuple[Optional[str], str, str]:
    """`<client>/<office>/<role>/<slug>` → (role slug, how it was read, source).

    Two readings, and the payload keeps them apart (§SD3):
      - `direct` — the third slot already names one of the 7 roles.
      - `legacy` — it names a `team/` discipline, as every id written before
        the 2026-09-05 team→role cutover does (measured 2026-09-09: 10 of the
        16 distinct values in the last 30 days). Resolved through the *same*
        resolver a card's `role` column goes through, so the two can never
        disagree about who owns `ops`.

    An id with the wrong number of segments, or a third slot that matches
    neither, resolves to None — reported, never dropped and never guessed.
    """
    parts = ident.split("/")
    if len(parts) != 4 or not all(parts[:3]):
        return None, "", ident
    slot = parts[2]
    if slot in ownership:
        return slot, "direct", slot
    resolved = workspace._default_role_for_team(slot, disciplines, role_list)
    if resolved:
        return resolved, "legacy", slot
    return None, "", ident


def _open_rows(
    root: Path, ownership: dict[str, dict]
) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Rows each role still holds, per board column (§SD6).

    Read through `workspace_overview()` rather than re-parsing slices.md: the
    effective role of a row is already decided there (ADR-0033 at file level,
    ADR-0035 at row level), and a second parser would eventually disagree with
    the cards the owner is looking at.

    `S26` says "⬜ rows", which is the `todo` column — but the owner mark beats
    the status glyph (`workspace._column_for`), so an unstarted row waiting on
    a person sits in `owner` instead. Reporting only `todo` would have shown
    `product-owner` holding 13 rows while it holds another 10 (measured
    2026-09-09), so every unfinished column comes back and the screen decides
    which one leads.
    """
    per_role: dict[str, dict[str, int]] = {
        slug: {column: 0 for column in OPEN_COLUMNS} for slug in ownership
    }
    unassigned = {column: 0 for column in OPEN_COLUMNS}

    try:
        payload = workspace.workspace_overview(str(root))
    except ValueError:
        return per_role, unassigned

    for project in payload.get("projects", []):
        for row in project.get("slices", []):
            column = row.get("column")
            if column not in OPEN_COLUMNS:
                continue
            slug = workspace._slug(row.get("role") or "")
            if slug in per_role:
                per_role[slug][column] += 1
            else:
                unassigned[column] += 1

    return per_role, unassigned
