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
    out = [f"{s} → {d}: ตารางประกาศไว้ แต่โค้ดไม่มีตัวตรวจ" for s, d in sorted(declared - implemented)]
    out += [f"{s} → {d}: โค้ดมีตัวตรวจ แต่ตารางไม่ประกาศแล้ว" for s, d in sorted(implemented - declared)]
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
        return {"allowed": False, "reason": table["reason"], "roles": [], "requires": ""}

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
