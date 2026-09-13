"""The gate for moving a card between belt stations (ADR-0044 §SD5).

One place decides, and it is not the screen: a button that looks pressable is a
picture of this module's answer, and a request that skips the UI meets the same
answer. The rules themselves are **not held here** — they are read from
`team-os/ways-of-working/row-status.md § ตารางการส่งต่อ`, the file that owns
them, the same discipline `_scan_dispatch` takes with roles.md (ADR-0029 §SD4).

The gate itself (`evaluate`/`candidates`) decides and writes nothing. Below it,
in its own clearly marked section, sits the write path ADR-0044 §SD1 settled —
write in the card's own worktree, commit, push, make sure the card's PR is open
(ADR-0048) — and the webhook of ADR-0046 §SD3. What this module still does NOT
do is **merge**: that is one press, at one station, and it is `S44`'s row.

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


def _git(cwd: Path, *args: str, timeout_s: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s
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


def _worktree(root: Path, slug: str, branch: str) -> tuple[Optional[Path], str, str]:
    """The card's worktree, created on first use. `(path, branch, error)`.

    `branch` in the return value is the name ACTUALLY checked out in that
    worktree — not necessarily the `branch` argument. row-status.md's own
    table hands a card to a different role at nearly every station
    (`developer` → `senior-developer` → `qa` → `devops` → `product-owner`,
    normal operation, not an edge case), and the `branch` argument is built
    from the CURRENT press's role — so on every press after the first, it
    names a branch that was never created. Recomputing it blindly (the bug
    this fixes, found 2026-09-13 walking a real card through every station)
    left a real commit sitting unpushed: `_git(tree, "commit", ...)` commits
    onto whatever the worktree is actually on, but `publish()` would then try
    to push under the wrong name and fail with "src refspec ... does not
    match any" — reported, never fatal, so nothing surfaced until someone
    read the PR and found a commit missing. The fix: once the worktree
    exists, ask git what it is actually on, and use THAT for everything
    downstream (`publish()`, the merge leg's `_open_pr()`) — never re-derive
    it from `actor_role` again.
    """
    path = root.joinpath(*_WORKTREE_DIR, slug)
    if path.exists():
        current = _git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if not current or current == "HEAD":
            return None, "", f"อ่าน branch ปัจจุบันของ worktree ไม่ได้: {path}"
        return path, current, ""
    path.parent.mkdir(parents=True, exist_ok=True)
    made = _git(root, "worktree", "add", "-B", branch, str(path))
    if made.returncode != 0:
        return None, "", f"เปิด worktree ไม่ได้: {made.stderr.strip()[:200]}"
    return path, branch, ""


# ── the publish leg (ADR-0048) ──────────────────────────────────────────────
#
# ADR-0044 §SD5 wrote the endpoint's sequence as "write · commit · (if it is the
# last station) merge · notify". Between commit and merge sat two verbs that
# never had a row of their own: PUSH and OPEN THE PR. Without them every press
# left its commit on a local branch and nothing carried it anywhere — measured
# 2026-09-12 on two live branches (`switchboard-s30`, `switchboard-s43`) whose
# commits said a card had moved while `main`'s register still said it had not.
#
# The credential is the one the server process already inherited from the
# owner's env: `control_plane/gh.py` has been running `gh pr list` with it since
# v2, so this is read→write on GitHub, not a board that suddenly holds a token.
# What it may do with it is two verbs and no more (ADR-0048 §SD3) — the refspec
# is built from the branch `apply()` computed, never from the request, and a
# branch that is not a task branch is refused before `git` is spawned.

# Task branches only. `apply()` builds the name itself, so this can only fire if
# someone changes that construction — which is exactly when a guard is worth
# having, because the value it guards against writing is `main`.
_BRANCH_PREFIX = "worktree-"
_BASE_BRANCH = "main"


def _gh(cwd: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """`gh` with the server's own environment — that is where the credential
    comes from (ADR-0048 §SD1) and it is never read, copied or logged here.

    `GH_PROMPT_DISABLED` because nobody is at this terminal: a `gh` that decides
    to ask a question must fail and say so, not sit on the request until the
    timeout. The token itself is not touched: it stays in the inherited env.
    """
    return subprocess.run(
        ["gh", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "GH_PROMPT_DISABLED": "1"},
    )


def _open_pr(tree: Path, branch: str) -> Optional[dict]:
    """The open PR whose head is `branch`, or None. Never raises."""
    try:
        out = _gh(
            tree,
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--limit",
            "1",
            "--json",
            "number,url",
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    try:
        rows = json.loads(out.stdout) if out.stdout.strip() else []
    except json.JSONDecodeError:
        return None
    return rows[0] if rows else None


def publish(tree: Path, branch: str, *, title: str, body: str) -> dict:
    """Push the card's branch and make sure its PR is open (ADR-0048 §SD2).

    The verb is *ensure*, not *create*: one card is one PR (ADR-0044 §SD1), so a
    second press appends to the same branch and finds the same PR rather than
    opening a second one. Calling this twice is a no-op the second time, which
    is what makes a failed push retryable by simply pressing again (§SD4).

    Returns `{pushed, pr, url, reason}`. Never raises and never rolls anything
    back: the commit already happened and carries the form's own words, so a
    network failure is reported, not undone.
    """
    blank = {"pushed": False, "pr": 0, "url": "", "reason": ""}
    if not branch.startswith(_BRANCH_PREFIX):
        return {**blank, "reason": f"ไม่ใช่ branch ของการ์ด: {branch}"}
    if _git(tree, "remote", "get-url", "origin").returncode != 0:
        # A workspace clone with no remote is a legitimate state (the test
        # fixtures are exactly that). Nothing to push to is not a failure of
        # the press.
        return {**blank, "reason": "ไม่มี remote origin"}

    try:
        # Explicit refspec, both sides built from the branch this module chose.
        # No `--force`: the board only ever appends commits to this branch, so
        # a rejected non-fast-forward means someone else wrote it and a human
        # should look — not that the board should overwrite them.
        pushed = _git(
            tree, "push", "origin", f"{branch}:refs/heads/{branch}", timeout_s=180
        )
    except subprocess.TimeoutExpired:
        return {**blank, "reason": "push ไม่จบภายในเวลาที่ให้"}
    except (subprocess.SubprocessError, OSError) as exc:
        return {**blank, "reason": f"push ไม่ขึ้น: {str(exc)[:200]}"}
    if pushed.returncode != 0:
        return {**blank, "reason": "push ไม่ขึ้น: " + pushed.stderr.strip()[:200]}

    existing = _open_pr(tree, branch)
    if existing:
        return {
            "pushed": True,
            "pr": existing.get("number", 0),
            "url": existing.get("url", ""),
            "reason": "",
        }

    try:
        made = _gh(
            tree,
            "pr",
            "create",
            "--base",
            _BASE_BRANCH,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {**blank, "pushed": True, "reason": f"เปิด PR ไม่ได้: {str(exc)[:200]}"}
    if made.returncode != 0:
        return {
            **blank,
            "pushed": True,
            "reason": "เปิด PR ไม่ได้: " + made.stderr.strip()[:200],
        }
    url = made.stdout.strip().splitlines()[-1].strip() if made.stdout.strip() else ""
    number = 0
    tail = url.rsplit("/", 1)[-1]
    if tail.isdigit():
        number = int(tail)
    return {"pushed": True, "pr": number, "url": url, "reason": ""}


def pr_body(*, project: str, row: dict, branch: str) -> str:
    """The body of the PR the board opens for one card.

    Says what opened it and points back at the register row, because the next
    reader of this PR is a person deciding whether to merge it and the one
    thing they need is which card this is. No harness attribution line: no
    harness wrote it (ADR-0048 § Consequences).
    """
    return "\n".join(
        [
            f"การ์ด **{row['id']}** ของ `projects/{project}/slices.md`",
            "",
            "ใบนี้ถูกเปิดโดยบอร์ด `/work` ตอนกดส่งต่อ — หนึ่งการ์ดหนึ่งใบ",
            "(`ADR-0044 §SD1` · `ADR-0048 §SD2`) ⇒ การกดครั้งต่อไปของการ์ดใบนี้",
            f"ต่อ commit ลง `{branch}` แล้วโตอยู่ในใบนี้ ไม่เปิดใบใหม่",
            "",
            "ก่อน merge: อ่านเช็กของใบ · สแกน diff เทียบ stop-list ·",
            "และถ้าใบนี้มี `Assignment:` มากกว่าหนึ่ง id ให้ merge ด้วย **merge commit**",
            "ไม่ใช่ squash (`ADR-0048 §SD2`)",
        ]
    )


# ── the check-reading leg (S44b · ADR-0049) ─────────────────────────────────
#
# A separate function from whoever presses (`apply()`/the still-unwritten
# merge leg of S44c): this only reads GitHub's answer and judges it, it never
# writes anything. Same `_gh()` as the publish leg above — no new credential
# (ADR-0049 §SD1).
#
# ADR-0049 §SD2: green comes from `statusCheckRollup` ITSELF, never from
# `mergeStateStatus` — this repo has no branch protection, so a red PR still
# answers `UNSTABLE`, not `BLOCKED`; `mergeStateStatus` is answering a
# different question than the one the rule asks. The one value of it this
# function still reads is `DIRTY` (a real merge conflict), which stops on its
# own regardless of the rollup.
#
# §SD3: a check name this function does not recognise is a stop, not a pass —
# renaming a job in pr-check.yml must widen the gate, never narrow it
# silently (risks.md S-01, ninth surface). `assignment` red is the one
# exception: §SD3/§SD7 read it as "pick merge commit over squash", not as a
# reason to stop — that choice is `S44a`/`S44c`'s job, not this one's.

# Exact job names from .github/workflows/pr-check.yml + the GitHub App check
# that is not a job in that file. A name outside this set is unknown (§SD3).
_KNOWN_CHECKS = frozenset(
    {
        "route-lint (links, orphans, model-id drift)",
        "stop-list (does this need the owner?)",
        "assignment (one id per PR, merge method)",
        "GitGuardian Security Checks",
    }
)

# Red here selects a merge method (§SD7) — it never stops the merge by itself.
_NON_STOPPING_CHECKS = frozenset({"assignment (one id per PR, merge method)"})

_GREEN_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})


def read_checks(tree: Path, number: int) -> dict:
    """What GitHub's checks say about PR `number`'s current head.

    Call this on the head the card carries IN, before any commit this module's
    own press would add — `apply()`/`publish()` push a new commit, which makes
    the rollup of the new head `pending` at the instant of the press (§SD5);
    reading straight after a press would never see green. That ordering is the
    caller's job, not this function's.

    Returns `{ok, green, reason}`. `reason` never says the bare "เช็กไม่ผ่าน" —
    it names the one check (or condition) that decided the answer, because
    §SD3 gives every red a different meaning and a caller that only sees
    "failed" cannot act on it.

    `ok` is False when the read itself did not produce a judgeable answer (`gh`
    failed, no such PR, unreadable JSON) — treated the same as a stop (§SD6):
    the caller never merges on `ok: False`, whatever `green` says.
    """
    try:
        out = _gh(
            tree,
            "pr",
            "view",
            str(number),
            "--json",
            "state,mergeStateStatus,statusCheckRollup",
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {
            "ok": False,
            "green": False,
            "reason": f"เรียก gh ไม่ได้: {str(exc)[:200]}",
        }
    if out.returncode != 0:
        return {
            "ok": False,
            "green": False,
            "reason": "gh pr view ล้ม: " + out.stderr.strip()[:200],
        }
    try:
        data = json.loads(out.stdout) if out.stdout.strip() else {}
    except json.JSONDecodeError:
        return {"ok": False, "green": False, "reason": "gh ตอบ JSON ที่อ่านไม่ออก"}

    # DIRTY = ชนกับ base จริง — หยุดไม่ว่ารายการเช็กจะว่างหรือเขียวแค่ไหน (§SD2)
    if data.get("mergeStateStatus") == "DIRTY":
        return {
            "ok": True,
            "green": False,
            "reason": "mergeStateStatus: DIRTY — branch ชนกับ main",
        }

    rollup = data.get("statusCheckRollup") or []
    if not rollup:
        # ว่าง = pr-check.yml ยังไม่วิ่ง หรือวิ่งไม่จบ — คือ "ไม่รู้" ไม่ใช่ "เขียว" (§SD2)
        return {
            "ok": True,
            "green": False,
            "reason": "statusCheckRollup ว่าง — ไม่รู้ ไม่ใช่เขียว",
        }

    for check in rollup:
        name = check.get("name") or "(ไม่มีชื่อ)"
        if name not in _KNOWN_CHECKS:
            return {"ok": True, "green": False, "reason": f"เช็กที่ไม่รู้จัก: {name}"}
        if name in _NON_STOPPING_CHECKS:
            continue
        status = check.get("status")
        conclusion = check.get("conclusion")
        if status != "COMPLETED" or conclusion not in _GREEN_CONCLUSIONS:
            return {
                "ok": True,
                "green": False,
                "reason": f"{name}: {conclusion or status or 'ไม่มีผล'}",
            }

    return {"ok": True, "green": True, "reason": ""}


# ── the merge leg (S44c · ADR-0049 §SD4 · §SD5 · §SD7) ──────────────────────
#
# `read_checks()` above answers what GitHub says about a head that already
# exists. This is the piece that was still missing: `deployed → done` is the
# one press that MERGES (ADR-0044 §SD3), and the trap ADR-0049 §SD5 names is
# that this press writes its own commit and pushes it — so the PR's head
# changes at the press, and its rollup is `pending` at the very instant this
# function could be asked to look. Reading it here would never see green.
# `apply()` below reads `read_checks()` BEFORE the press's own commit exists
# and hands the answer in as `pre_press`; this function never calls
# `read_checks()` itself.
#
# What this function DOES check locally, on the commit the press itself just
# made, is the two gates §SD5 names for that narrower job (stop-list and the
# single-file confirmation), plus route-lint and the merge-method choice
# §SD4/§SD7 assign to this leg. Refusing to merge is never an error (S44d):
# the row is already written and pushed on the card's own branch either way,
# and a refusal here just means a person merges it by hand.


def _run_node(
    tree: Path, script: str, *args: str
) -> Optional[subprocess.CompletedProcess]:
    """`node <script> <args>` in `tree`, or None if it could not even be
    spawned (node missing, timeout) — distinct from the script running and
    saying no."""
    try:
        return subprocess.run(
            ["node", script, *args],
            cwd=str(tree),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return None


def _stop_list_clear(tree: Path) -> tuple[bool, str]:
    """`tools/pr-guard/stop-list.mjs --json` against `origin/main`, run in
    `tree` on the commit the press just made. `(clear, reason)` — exit 2 (the
    script's own law-drift guard) is read as NOT clear, same as a hit: this
    function must never treat "the check errored" as "the check passed"."""
    out = _run_node(
        tree, "tools/pr-guard/stop-list.mjs", "--base", "origin/main", "--json"
    )
    if out is None:
        return False, "เรียก stop-list.mjs ไม่ได้"
    if out.returncode == 2:
        return False, "stop-list.mjs ผิดพลาด: " + out.stderr.strip()[:300]
    try:
        data = json.loads(out.stdout) if out.stdout.strip() else {}
    except json.JSONDecodeError:
        return False, "stop-list.mjs ตอบ JSON ที่อ่านไม่ออก"
    hit_paths = data.get("hitPaths") or []
    if hit_paths:
        return False, "stop-list HIT: " + ", ".join(hit_paths[:5])
    return True, ""


def _route_lint_clean(tree: Path) -> tuple[bool, str]:
    """`tools/route-lint/route-lint.mjs` against the tree that is about to be
    merged — the same script CI runs, measured at 0.58s with no network
    (ADR-0049 §SD5). `(clean, reason)`."""
    out = _run_node(tree, "tools/route-lint/route-lint.mjs")
    if out is None:
        return False, "เรียก route-lint.mjs ไม่ได้"
    if out.returncode != 0:
        return False, "route-lint ไม่ผ่าน: " + (out.stderr or out.stdout).strip()[:300]
    return True, ""


def _touches_only(tree: Path, rel: Path) -> Optional[str]:
    """None if the press's own commit (`HEAD` against its one parent) touches
    exactly `rel` — the cheap half of ADR-0049 §SD5's two local gates. The
    board composes this diff itself with `set_cell()`, but an assumption that
    is never checked is exactly the one that breaks quietly."""
    out = _git(tree, "diff", "--name-only", "HEAD~1", "HEAD")
    if out.returncode != 0:
        return "อ่าน diff ของ commit ล่าสุดไม่ได้: " + out.stderr.strip()[:200]
    touched = [p for p in out.stdout.splitlines() if p.strip()]
    want = str(rel).replace("\\", "/")
    if touched != [want]:
        shown = ", ".join(touched) if touched else "(ไม่มี)"
        return f"commit ของการกดแตะมากกว่าไฟล์เดียว: {shown}"
    return None


def _assignment_verdict(tree: Path) -> dict:
    """`tools/pr-guard/assignment.mjs --json` against `origin/main`, run in
    `tree` on the commit the press just made — the merge-method choice
    §SD7 hands to `merge_if_clear()`. Exit 2 (role-table drift) reads the
    same as an unreadable answer: `mergeMethod: None`."""
    out = _run_node(
        tree, "tools/pr-guard/assignment.mjs", "--base", "origin/main", "--json"
    )
    if out is None:
        return {
            "verdict": None,
            "mergeMethod": None,
            "reason": "เรียก assignment.mjs ไม่ได้",
        }
    if out.returncode == 2:
        return {
            "verdict": None,
            "mergeMethod": None,
            "reason": "assignment.mjs ผิดพลาด: " + out.stderr.strip()[:300],
        }
    try:
        data = json.loads(out.stdout) if out.stdout.strip() else {}
    except json.JSONDecodeError:
        return {
            "verdict": None,
            "mergeMethod": None,
            "reason": "assignment.mjs ตอบ JSON ที่อ่านไม่ออก",
        }
    data.setdefault("reason", "")
    return data


# `assignment.mjs --json`'s `mergeMethod` values, straight to the `gh` flag
# that means them (§SD7) — never a default the button picks on its own.
_MERGE_FLAG = {"squash": "--squash", "merge-commit": "--merge"}


def merge_if_clear(tree: Path, *, pr: int, rel: Path, pre_press: dict) -> dict:
    """The 6th press's merge leg — `deployed → done` only.

    `pre_press` is what `read_checks()` answered about the PR's head BEFORE
    this press wrote its own commit (§SD5) — `apply()` reads that earlier and
    hands it in; this function never calls `read_checks()` itself, so it can
    never be tempted to read the (always-pending) post-push head. From there
    it closes the two gates that answer is still missing: the commit the
    press itself just made (stop-list · route-lint · touches-one-file, in
    that order — first hit wins) and, only once all three are clear, the
    merge-method choice (§SD7).

    Returns `{merged, method, reason}`. `merged: False` is never raised as an
    error — S44d is the same outcome reached a different way: the row stands
    committed and pushed on the card's own branch, and a human merges it
    (`gh pr merge`) once they have looked.
    """
    blank = {"merged": False, "method": None, "reason": ""}
    if not pr:
        return {**blank, "reason": "ไม่มี PR ที่เปิดอยู่ของการ์ดนี้ให้ merge"}
    if not (pre_press.get("ok") and pre_press.get("green")):
        return {
            **blank,
            "reason": pre_press.get("reason") or "อ่านผลเช็กของ head ก่อนกดไม่ได้",
        }

    clear, reason = _stop_list_clear(tree)
    if not clear:
        return {**blank, "reason": reason}

    clean, reason = _route_lint_clean(tree)
    if not clean:
        return {**blank, "reason": reason}

    touch_err = _touches_only(tree, rel)
    if touch_err:
        return {**blank, "reason": touch_err}

    verdict = _assignment_verdict(tree)
    method = verdict.get("mergeMethod")
    if method not in _MERGE_FLAG:
        return {
            **blank,
            "reason": verdict.get("reason")
            or f"assignment verdict: {verdict.get('verdict')}",
        }

    try:
        merged = _gh(tree, "pr", "merge", str(pr), _MERGE_FLAG[method])
    except (subprocess.SubprocessError, OSError) as exc:
        return {
            "merged": False,
            "method": method,
            "reason": f"เรียก gh pr merge ไม่ได้: {str(exc)[:200]}",
        }
    if merged.returncode != 0:
        return {
            "merged": False,
            "method": method,
            "reason": "gh pr merge ล้ม: " + merged.stderr.strip()[:300],
        }
    return {"merged": True, "method": method, "reason": ""}


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

    Returns `{ok, reason, branch, commit, worktree, published, merge}`.
    Nothing is written unless `evaluate()` allowed it — the UI's disabled
    button is a picture of this call's answer, and a request that skips the
    UI meets the same gate here.

    Only the `stage` cell moves. The `role` cell — who holds the row next — is
    still a human edit: who *presses* a transition and who *works* the next
    station are different questions (row-status.md § ตารางการส่งต่อ), and the
    board has no table that answers the second one yet.

    `deployed → done` is the one press that also merges (ADR-0044 §SD3):
    `merge["merged"]` says whether it did, and when it did not, `merge
    ["reason"]` says why — never an error, because the write above still
    happened either way (S44d).
    """
    form = form or {}
    verdict = evaluate(
        root, row=row, to_stage=to_stage, actor_role=actor_role, form=form
    )
    if not verdict["allowed"]:
        return {"ok": False, "reason": verdict["reason"], **_blank()}

    is_merge_station = row.get("stage") == "deployed" and to_stage == "done"

    slug = task_slug(project, row["id"])
    # Only a NEW worktree is named from this press's own role — an existing
    # one keeps whichever branch its first press already picked (see
    # `_worktree()`'s docstring for why re-deriving it here was the bug).
    tree, branch, err = _worktree(root, slug, f"worktree-{client}-{actor_role}-{slug}")
    if tree is None:
        return {"ok": False, "reason": err, **_blank()}

    # ADR-0049 §SD5's trap: this press is about to write its own commit and
    # push it, which makes the PR's head — and its rollup — change. Reading
    # `read_checks()` after that would see `pending` every single time. The
    # only honest moment to read it is right now, before a single byte of
    # this press exists, off whatever PR is open on the card's branch today.
    pre_press: Optional[dict] = None
    pr_before = 0
    if is_merge_station:
        found = _open_pr(tree, branch)
        pr_before = found.get("number", 0) if found else 0
        pre_press = (
            read_checks(tree, pr_before)
            if found
            else {
                "ok": False,
                "green": False,
                "reason": "ยังไม่มี PR เปิดอยู่ของการ์ดนี้",
            }
        )

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
    # Carry it out of the machine (ADR-0048 §SD1). A failure here is reported,
    # never rolled back: the commit above is real and holds the words the
    # operator typed into the handoff form, and the next press pushes it — the
    # ensure is idempotent (§SD4).
    published = publish(
        tree,
        branch,
        title=message.split("\n", 1)[0],
        body=pr_body(project=project, row=row, branch=branch),
    )
    merge = {"merged": False, "method": None, "reason": ""}
    if is_merge_station:
        # `published["pr"]` is 0 when this press's own push failed — fall back
        # to the PR found before the press, since a push failure does not mean
        # the PR stopped existing.
        merge = merge_if_clear(
            tree,
            pr=published.get("pr") or pr_before,
            rel=rel,
            pre_press=pre_press or {},
        )
    return {
        "ok": True,
        "reason": None,
        "branch": branch,
        "commit": head,
        "worktree": str(tree),
        "published": published,
        "merge": merge,
    }


def _blank() -> dict:
    return {
        "branch": "",
        "commit": "",
        "worktree": "",
        # Same keys in every answer: a caller that reads `published` (or
        # `merge`) off a refused press gets "nothing happened", not a
        # KeyError.
        "published": {"pushed": False, "pr": 0, "url": "", "reason": ""},
        "merge": {"merged": False, "method": None, "reason": ""},
    }


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
