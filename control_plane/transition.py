"""The gate for moving a card between belt stations (ADR-0044 §SD5).

One place decides, and it is not the screen: a button that looks pressable is a
picture of this module's answer, and a request that skips the UI meets the same
answer. The rules themselves are **not held here** — they are read from
`team-os/ways-of-working/row-status.md § ตารางการส่งต่อ`, the file that owns
them, the same discipline `_scan_dispatch` takes with roles.md (ADR-0029 §SD4).

What this module does NOT do, deliberately: it does not write, commit, merge, or
notify. It answers *may this transition happen, and if not why*. The write path
is a separate slice so that the decision can be reviewed on its own.

Stdlib only (tests/test_stdlib_purity.py enforces it).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from .workspace import STAGE_ORDER

# The heading the table sits under. Anchored on the heading, not on the column
# wording, for the reason every other reader in this repo is: Thai prose headers
# stay free to reword, their position under a heading is the contract.
_HEADING = "ตารางการส่งต่อ"
_RULES_FILE = ("team-os", "ways-of-working", "row-status.md")

_CODE = re.compile(r"`([^`]+)`")

# Read from workspace.py rather than restated: one list of stations, one owner
# (row-status.md § สายพาน), two readers that cannot disagree about it.
_STATIONS = frozenset(STAGE_ORDER)


def _strip_md(cell: str) -> str:
    return cell.replace("**", "").replace("`", "").strip()


def transitions(root: Path) -> dict:
    """The declared transitions, keyed by `(from, to)`.

    Fails **dark**, never open: an unreadable or reworded table yields
    `present: False` and the caller refuses every transition. The opposite
    default — "cannot read the rules, so allow it" — is how a board that writes
    files loses a register.
    """
    path = root.joinpath(*_RULES_FILE)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"present": False, "reason": f"อ่าน {path.name} ไม่ได้", "moves": {}}

    moves: dict[tuple[str, str], dict] = {}
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if in_section:
                break  # the section ended; later tables are other rules
            in_section = _HEADING in stripped
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4 or set("".join(cells)) <= set("-: "):
            continue
        src, dst = _strip_md(cells[0]), _strip_md(cells[1])
        # A data row names two declared stations. Nothing else qualifies —
        # which skips the header row without knowing a word of what it says,
        # and keeps the promise that the column wording stays free to change.
        # The station list comes from the module that owns it, not a copy.
        if src not in _STATIONS or dst not in _STATIONS:
            continue
        moves[(src, dst)] = {
            # Every role token in the cell, in declaration order. Read from the
            # backticks rather than split on `·` so a reworded separator cannot
            # silently turn two roles into one.
            "roles": _CODE.findall(cells[2]),
            # The condition, verbatim, for the refusal message. The *check* is
            # `_CHECKS` below; this is what the operator is shown.
            "requires": _strip_md(cells[3]),
        }
    if not moves:
        return {
            "present": False,
            "reason": f"ไม่พบตารางใต้หัวข้อ {_HEADING} ใน {path.name}",
            "moves": {},
        }
    return {"present": True, "reason": "", "moves": moves}


# ── the conditions, keyed by the same pair the table keys on ─────────────────
#
# The table is the source of *which pairs exist and who may press them*; the
# condition column is Thai prose and stays prose. What each condition MEANS is
# code, and `drift()` below refuses to let the two key sets diverge — the same
# law-drift guard tools/pr-guard/stop-list.mjs keeps over its copied list.


def _no_open_blockers(row: dict, form: dict) -> Optional[str]:
    blockers = row.get("blocked_by") or []
    if blockers:
        named = " · ".join(f"{b['id']}" for b in blockers)
        return f"ยังมีตัวบล็อกที่ไม่ปิด: {named}"
    return None


def _criteria_closed(row: dict, form: dict) -> Optional[str]:
    c = row.get("criteria")
    if not c:
        return "แถวนี้ยังไม่มีเกณฑ์ตรวจรับสักข้อ — เขียนแถวลูกก่อน"
    if c["done"] < c["total"]:
        return f"เกณฑ์ยังไม่ครบ ({c['done']}/{c['total']})"
    return None


def _handoff_form(row: dict, form: dict) -> Optional[str]:
    missing = [k for k in ("env", "risk") if not (form.get(k) or "").strip()]
    if missing:
        return "ฟอร์มส่งงานยังไม่ครบ: " + " · ".join(missing)
    return None


def _reason(row: dict, form: dict) -> Optional[str]:
    return None if (form.get("reason") or "").strip() else "ต้องระบุเหตุผลก่อนตีกลับ"


def _release(row: dict, form: dict) -> Optional[str]:
    return None if (form.get("release") or "").strip() else "ต้องระบุ release / tag"


_CHECKS: dict[tuple[str, str], Callable[[dict, dict], Optional[str]]] = {
    ("readydev", "inprogress"): _no_open_blockers,
    ("inprogress", "review"): lambda row, form: None,
    ("review", "readyqa"): _handoff_form,
    ("readyqa", "readydeploy"): _criteria_closed,
    ("readyqa", "inprogress"): _reason,
    ("readydeploy", "deployed"): _release,
    ("readydeploy", "inprogress"): _reason,
    ("deployed", "done"): _criteria_closed,
    ("deployed", "inprogress"): _reason,
    ("done", "inprogress"): _reason,
}


def drift(root: Path) -> list[str]:
    """Pairs the table and `_CHECKS` do not agree on.

    A pair the file declares but this module cannot check would pass with no
    condition at all; a pair this module checks but the file no longer declares
    is dead code that reads as policy. Both are reported rather than resolved —
    the file wins, always, and a human decides which side is stale.
    """
    table = transitions(root)
    if not table["present"]:
        return [table["reason"]]
    declared, implemented = set(table["moves"]), set(_CHECKS)
    out = [
        f"{s} → {d}: ตารางประกาศไว้ แต่โค้ดไม่มีตัวตรวจ"
        for s, d in sorted(declared - implemented)
    ]
    out += [
        f"{s} → {d}: โค้ดมีตัวตรวจ แต่ตารางไม่ประกาศแล้ว"
        for s, d in sorted(implemented - declared)
    ]
    return out


def evaluate(
    root: Path,
    *,
    row: dict,
    to_stage: str,
    actor_role: str,
    form: Optional[dict] = None,
) -> dict:
    """May `actor_role` move `row` to `to_stage`, and if not, why.

    Returns `{allowed, reason, roles, requires}` — `roles` and `requires` come
    straight from the table so the caller can show the operator the rule it was
    judged against rather than a paraphrase of it.
    """
    form = form or {}
    table = transitions(root)
    if not table["present"]:
        return {
            "allowed": False,
            "reason": table["reason"],
            "roles": [],
            "requires": "",
        }

    move = table["moves"].get((row.get("stage") or "", to_stage))
    if move is None:
        return {
            "allowed": False,
            # Includes skipping a station: the table is a closed set, exactly
            # like the 8 glyphs and the 9 stations.
            "reason": f"ไม่มีการส่งต่อ {row.get('stage') or '—'} → {to_stage} ในตาราง",
            "roles": [],
            "requires": "",
        }

    if actor_role not in move["roles"]:
        return {
            "allowed": False,
            "reason": "สิทธิ์นี้เป็นของ " + " / ".join(move["roles"]),
            "roles": move["roles"],
            "requires": move["requires"],
        }

    check = _CHECKS.get((row.get("stage") or "", to_stage))
    if check is None:
        # Declared in the file, unknown here. Refuse rather than wave through:
        # `drift()` exists to make this visible before it can happen.
        return {
            "allowed": False,
            "reason": "โค้ดยังไม่มีตัวตรวจของการส่งต่อนี้",
            "roles": move["roles"],
            "requires": move["requires"],
        }

    failure = check(row, form)
    return {
        "allowed": failure is None,
        "reason": failure,
        "roles": move["roles"],
        "requires": move["requires"],
    }


# ── every move a screen might offer (S43b) ──────────────────────────────────
#
# Free-text form fields that only appear once someone opens a dialog to type
# them: probing with a placeholder answers "would this role clear
# role/blocked-by/criteria", never "is the form filled" — that second question
# has no answer until there is a form to fill, and `apply()` asks it for real
# with what the operator actually typed. A button this list marks `allowed`
# can still come back 409 at submit if the real form is missing something;
# what it must never do is disagree with `evaluate()` about role/blockers/
# criteria, because that disagreement is exactly the second gate ADR-0044
# §SD5 forbids.
_FORM_PROBE = {"env": "x", "risk": "x", "reason": "x", "release": "x", "data": "x"}


def candidates(root: Path, *, row: dict, actor_role: str) -> list[dict]:
    """Every declared move out of `row`'s current stage, each evaluated for
    `actor_role` — the list a screen renders buttons from instead of holding
    its own copy of the table (slices.md S43a · S43b).

    Ordered by belt station (`STAGE_ORDER`), not by the table's own line
    order, so a caller never has to re-sort a dict to get a stable display.
    """
    stage = row.get("stage") or ""
    table = transitions(root)
    if not table["present"]:
        return []
    order = {s: i for i, s in enumerate(STAGE_ORDER)}
    moves = sorted(
        (pair for pair in table["moves"] if pair[0] == stage),
        key=lambda pair: order.get(pair[1], len(order)),
    )
    return [
        {
            "to_stage": dst,
            **evaluate(
                root, row=row, to_stage=dst, actor_role=actor_role, form=_FORM_PROBE
            ),
        }
        for _, dst in moves
    ]


# ── the write path (ADR-0044 §SD1 · §SD2) ───────────────────────────────────
#
# The board writes in the CARD'S OWN worktree, never in the main checkout: the
# Hard Rule `Worktree-Per-Task` is not bent here, it is the mechanism. What
# changed at ADR-0044 is who types, not where.

import os
import subprocess
import urllib.error
import urllib.request

# Where the workspace keeps its own worktrees — gitignored, declared by
# `.gitignore` rather than chosen here.
_WORKTREE_DIR = (".claude", "worktrees")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )


def task_slug(project: str, row_id: str) -> str:
    """The slug the card's branch and `Assignment:` share.

    Project-qualified because row ids are only unique within a file: `S1` of
    switchboard and `W1` of workspace are different pieces of work, and a
    branch name that conflated them would put two cards on one branch.
    """
    raw = f"{project}-{row_id}".lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", raw)).strip("-")[:40]


def set_cell(text: str, row_id: str, column: str, value: str) -> Optional[str]:
    """Return `text` with one cell of one row replaced, or None if not found.

    Column by header TEXT, row by its `#` — never by position. The rest of the
    line is rebuilt from its own cells, so a row this function does not touch is
    byte-identical and a row it does touch keeps every other cell as written.
    """
    lines = text.split("\n")
    header = next(
        (i for i, l in enumerate(lines) if l.startswith("|") and "สถานะ" in l), None
    )
    if header is None:
        return None
    cells = [c.strip() for c in lines[header].strip().strip("|").split("|")]
    try:
        col = next(i for i, c in enumerate(cells) if _strip_md(c).lower() == column)
    except StopIteration:
        return None

    for i in range(header + 2, len(lines)):
        if not lines[i].startswith("|"):
            break
        row = lines[i].strip().strip("|").split("|")
        if len(row) <= col or _strip_md(row[0]) != row_id:
            continue
        row[col] = f" {value} "
        lines[i] = "|" + "|".join(row) + "|"
        return "\n".join(lines)
    return None


def _worktree(root: Path, slug: str, branch: str) -> tuple[Optional[Path], str]:
    """The card's worktree, created on first use. `(path, error)`."""
    path = root.joinpath(*_WORKTREE_DIR, slug)
    if path.exists():
        return path, ""
    path.parent.mkdir(parents=True, exist_ok=True)
    made = _git(root, "worktree", "add", "-B", branch, str(path))
    if made.returncode != 0:
        return None, f"เปิด worktree ไม่ได้: {made.stderr.strip()[:200]}"
    return path, ""


def apply(
    root: Path,
    *,
    project: str,
    row: dict,
    to_stage: str,
    actor_role: str,
    office: str = "-",
    client: str = "internal",
    form: Optional[dict] = None,
) -> dict:
    """Run the gate, then write the row in the card's own worktree.

    Returns `{ok, reason, branch, commit, worktree}`. Nothing is written unless
    `evaluate()` allowed it — the UI's disabled button is a picture of this
    call's answer, and a request that skips the UI meets the same gate here.

    Only the `stage` cell moves. The `role` cell — who holds the row next — is
    still a human edit: who *presses* a transition and who *works* the next
    station are different questions (row-status.md § ตารางการส่งต่อ), and the
    board has no table that answers the second one yet.
    """
    form = form or {}
    verdict = evaluate(
        root, row=row, to_stage=to_stage, actor_role=actor_role, form=form
    )
    if not verdict["allowed"]:
        return {"ok": False, "reason": verdict["reason"], **_blank()}

    slug = task_slug(project, row["id"])
    branch = f"worktree-{client}-{actor_role}-{slug}"
    tree, err = _worktree(root, slug, branch)
    if tree is None:
        return {"ok": False, "reason": err, **_blank()}

    rel = Path("projects") / project / "slices.md"
    target = tree / rel
    try:
        before = target.read_text(encoding="utf-8")
    except OSError:
        return {"ok": False, "reason": f"อ่าน {rel} ใน worktree ไม่ได้", **_blank()}

    after = set_cell(before, row["id"], "stage", to_stage)
    if after is None:
        return {
            "ok": False,
            # A row or column the file does not have is not something to create:
            # the register is written by people, this only moves one cell of it.
            "reason": f"ไม่พบแถว {row['id']} หรือคอลัมน์ stage ใน {rel.name}",
            **_blank(),
        }
    if after == before:
        return {"ok": False, "reason": "แถวนี้อยู่สถานีนั้นอยู่แล้ว", **_blank()}

    target.write_text(after, encoding="utf-8")
    message = commit_message(
        project=project,
        row=row,
        to_stage=to_stage,
        actor_role=actor_role,
        office=office,
        client=client,
        slug=slug,
        form=form,
    )
    add = _git(tree, "add", str(rel))
    if add.returncode != 0:
        _git(tree, "reset", "--hard", "HEAD")
        return {"ok": False, "reason": add.stderr.strip()[:200], **_blank()}
    done = _git(tree, "commit", "-m", message)
    if done.returncode != 0:
        # A refused commit (the workspace's own `.githooks/commit-msg`, most
        # often) must not leave the write sitting staged-but-uncommitted —
        # the NEXT call reads `before` off this same worktree, would see the
        # target stage already there, and answer "แถวนี้อยู่สถานีนั้นอยู่แล้ว"
        # for a move that never actually committed (found on first real use,
        # 2026-09-12: an office-less commit was blocked, and every retry after
        # the fix landed read the stale staged write as "already done").
        # `reset --hard HEAD` returns the worktree to its last real commit so
        # a retry starts clean, exactly as if this call had never touched it.
        _git(tree, "reset", "--hard", "HEAD")
        return {"ok": False, "reason": done.stderr.strip()[:300], **_blank()}
    head = _git(tree, "rev-parse", "--short", "HEAD").stdout.strip()
    return {
        "ok": True,
        "reason": None,
        "branch": branch,
        "commit": head,
        "worktree": str(tree),
    }


def _blank() -> dict:
    return {"branch": "", "commit": "", "worktree": ""}


def commit_message(
    *,
    project: str,
    row: dict,
    to_stage: str,
    actor_role: str,
    office: str,
    client: str,
    slug: str,
    form: dict,
) -> str:
    """The commit body for one transition.

    The form's own words go here rather than into a cell of the table: the file
    holds *state*, the commit holds *what happened* (ADR-0046 §SD2). A cell
    would be overwritten next round and the reason for this round would vanish.
    """
    head = f"docs(slices): {row['id']} {row.get('stage') or '—'} → {to_stage}"
    lines = [
        head,
        "",
        f"what: ย้ายสถานีของแถว {row['id']} ใน projects/{project}/slices.md",
    ]
    for key, label in (
        ("env", "ทดสอบที่"),
        ("risk", "ข้อควรระวัง"),
        ("data", "ข้อมูลทดสอบ"),
        ("release", "release"),
        ("reason", "เหตุผล"),
    ):
        if (form.get(key) or "").strip():
            lines.append(f"{label}: {form[key].strip()}")
    lines += [
        "",
        "why: กดส่งต่อจากบอร์ด /work — ด่านของ control_plane/transition.py ตรวจผ่านแล้ว",
        "ตามตารางใน team-os/ways-of-working/row-status.md § ตารางการส่งต่อ",
        "",
        "side-effects: เฉพาะเซลล์ stage ของแถวนี้ · คอลัมน์ role ยังเป็นการแก้ด้วยมือ",
        "",
        "verification: ด่านเดียวกันถูกเรียกซ้ำที่ endpoint ⇒ คำขอที่ข้าม UI ก็ไม่ผ่าน ·",
        "no automated checks: การเปลี่ยนสถานะของแถวไม่มีเทสให้รันนอกจากตัวด่านเอง",
        "",
        f"Assignment: {client}/{office}/{actor_role}/{slug}",
    ]
    return "\n".join(lines)


# ── telling the next station (ADR-0046 §SD3) ────────────────────────────────


def payload(
    *, project: str, row: dict, to_stage: str, actor_role: str, form: dict
) -> dict:
    """What goes out when a card changes station. Shape is the contract."""
    return {
        "event": "card.stage_changed",
        "task_key": row["id"],
        "project": project,
        "from": row.get("stage") or "",
        "to": to_stage,
        "actor": actor_role,
        # Only the fields the form declares — never a whole cell of the table,
        # which is a paragraph by nature (S41 measured 1,894 characters).
        "handoff": {
            k: form[k] for k in ("env", "risk", "release", "reason") if form.get(k)
        },
    }


def notify(body: dict, url: str = "") -> dict:
    """POST `body` to `url`. No URL configured is not an error.

    Telling the next station is *passing the word on*, not part of passing the
    work: a handoff that wrote the row has happened whether or not anyone was
    listening (ADR-0046 §SD3).
    """
    target = url or os.environ.get("WORK_WEBHOOK_URL", "")
    if not target:
        return {"sent": False, "reason": "ไม่ได้ตั้ง URL"}
    req = urllib.request.Request(
        target,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return {"sent": 200 <= res.status < 300, "reason": "", "status": res.status}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # Never raised to the caller: the write already happened.
        return {"sent": False, "reason": str(exc)[:200]}
