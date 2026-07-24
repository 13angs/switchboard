"""Session health score — 3-signal graduated heuristic (ADR-0016, Branch C).

Computes 3 signals (stale / loop / error) → 3-tier score (🟢 healthy / 🟡 warning
/ 🔴 unhealthy). Health is heuristic only — observe, never auto-act (C4).

Read from jsonl transcript; zero new dependencies. Health is computed server-side
during the /state poll — no new endpoint needed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class HealthScore:
    status: str  # 'healthy' | 'warning' | 'unhealthy'
    stale: str  # per-signal
    loop: str  # per-signal
    error: str  # per-signal
    stale_hrs: float  # hours idle (for UI detail)
    loop_count: int  # max consecutive same-tool (for UI detail)
    error_count: int  # errors in last 10 turns (for UI detail)
    error_total: int  # total turns in error window


# Known tool names for loop-detection text heuristic (SD4).
_KNOWN_TOOLS = frozenset(
    {
        "Read",
        "Write",
        "Edit",
        "Bash",
        "Glob",
        "Grep",
        "WebFetch",
        "WebSearch",
        "Task",
        "Agent",
        "NotebookEdit",
    }
)


def session_health(
    jsonl_path: str,
    last_ts: Optional[str],
    harness: str,
    now: Optional[datetime] = None,
) -> Optional[HealthScore]:
    """Return health score, or None when jsonl is unreadable/empty.

    Signals:
      - stale: computed from last_ts vs now (no file read needed)
      - loop: max consecutive same-tool in last 20 assistant messages
      - error: count of error/refusal stop_reasons in last 10 turns

    Overall status = worst of (stale, loop, error).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # --- Stale (cheapest — computed first) ----------------------------------
    stale, stale_hrs = _compute_stale(last_ts, now)

    # --- Read messages for loop + error -------------------------------------
    messages = _read_messages(jsonl_path, limit=20)
    if messages is None:
        return None  # unreadable

    # Short-circuit: stale=unhealthy + turn_count>0 → skip message scan
    turn_count = sum(1 for m in messages if m.get("role") == "assistant")
    if stale == "unhealthy" and turn_count > 0:
        return HealthScore(
            status="unhealthy",  # worst-signal-wins
            stale=stale,
            loop="healthy",  # not computed (can't worsen from 🔴)
            error="healthy",
            stale_hrs=stale_hrs,
            loop_count=0,
            error_count=0,
            error_total=0,
        )

    # --- Loop ---------------------------------------------------------------
    loop, loop_count = _compute_loop(messages)

    # --- Error --------------------------------------------------------------
    error, error_count, error_total = _compute_error(messages)

    # --- Overall status = worst-signal-wins ---------------------------------
    status = _worst(stale, _worst(loop, error))

    return HealthScore(
        status=status,
        stale=stale,
        loop=loop,
        error=error,
        stale_hrs=stale_hrs,
        loop_count=loop_count,
        error_count=error_count,
        error_total=error_total,
    )


# ── Signal detectors (internal) ─────────────────────────────────────────────


def _compute_stale(
    last_ts: Optional[str],
    now: datetime,
) -> tuple[str, float]:
    """Compute stale signal from last_ts.

    Thresholds (from forge):
      - 🟡 idle > 6h
      - 🔴 idle > 24h

    Returns (signal, hours).
    """
    if last_ts is None:
        return ("healthy", 0.0)

    try:
        last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ("healthy", 0.0)

    hrs = (now - last_dt).total_seconds() / 3600.0
    if hrs > 24.0:
        return ("unhealthy", hrs)
    if hrs > 6.0:
        return ("warning", hrs)
    return ("healthy", hrs)


def _compute_loop(messages: List[dict]) -> tuple[str, int]:
    """Detect loop: longest run of consecutive same-tool calls.

    Thresholds (from forge):
      - 🟡 ≥ 5 consecutive same-tool calls
      - 🔴 ≥ 10 consecutive same-tool calls

    Uses tool_use block name first, falls back to text heuristic (SD4).
    """
    max_run = 0
    current_tool = None
    current_run = 0

    for msg in messages:
        if msg.get("role") != "assistant":
            continue

        tool = _extract_tool_name(msg)
        if tool is None:
            # Non-tool assistant message resets the run
            current_tool = None
            current_run = 0
            continue

        if tool == current_tool:
            current_run += 1
        else:
            current_tool = tool
            current_run = 1

        max_run = max(max_run, current_run)

    if max_run >= 10:
        return ("unhealthy", max_run)
    if max_run >= 5:
        return ("warning", max_run)
    return ("healthy", max_run)


def _compute_error(messages: List[dict]) -> tuple[str, int, int]:
    """Count error/refusal stop_reasons in last 10 assistant turns.

    Thresholds (from forge):
      - 🟡 ≥ 3 errors in last 10 turns
      - 🔴 error rate ≥ 50% (≥ 5 of ≤ 10)

    Returns (signal, error_count, total_turns_checked).
    """
    assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
    last_10 = assistant_msgs[-10:]
    error_count = 0

    for msg in last_10:
        stop_reason = msg.get("stop_reason", "")
        if stop_reason in ("error", "refusal"):
            error_count += 1

    total = len(last_10)

    if total > 0 and error_count >= total * 0.5:
        return ("unhealthy", error_count, total)
    if error_count >= 3:
        return ("warning", error_count, total)
    return ("healthy", error_count, total)


def _extract_tool_name(msg: dict) -> Optional[str]:
    """Extract tool name from a message.

    Priority:
    1. tool_use content block → name field
    2. First word of text content → matches known tool names (SD4 heuristic)
    """
    content = msg.get("content", [])
    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name = block.get("name")
            if name:
                return str(name)

    # Fallback: text heuristic (SD4)
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text", "")
            if text:
                first_word = text.strip().split()[0] if text.strip() else None
                if first_word and first_word in _KNOWN_TOOLS:
                    return first_word

    return None


def _read_messages(jsonl_path: str, limit: int = 20) -> Optional[List[dict]]:
    """Read last `limit` messages from a jsonl file. Returns None if unreadable."""
    if not os.path.isfile(jsonl_path):
        return None

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return None

    messages = []
    # Read up to `limit` from the end (non-rich parse — performance per ADR-0016 §SD1)
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return messages


def _worst(a: str, b: str) -> str:
    """Return the worse of two health signals."""
    ORDER = {"healthy": 0, "warning": 1, "unhealthy": 2}
    return a if ORDER.get(a, 0) >= ORDER.get(b, 0) else b
