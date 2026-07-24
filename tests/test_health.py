#!/usr/bin/env python3
"""Branch C — session health score (3-signal graduated heuristic, ADR-0016).

Run:
    python3 projects/agent-view/repos/agent-view/tests/test_health.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from control_plane import health  # noqa: E402

# --- Helpers ----------------------------------------------------------------


def _write_jsonl(path: str, messages: list[dict]) -> None:
    """Write a list of message dicts as a jsonl file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


# --- Test: HealthScore dataclass --------------------------------------------


def test_healthscore_fields():
    h = health.HealthScore(
        status="healthy",
        stale="healthy",
        loop="healthy",
        error="healthy",
        stale_hrs=0.5,
        loop_count=0,
        error_count=0,
        error_total=0,
    )
    assert h.status == "healthy"
    assert h.stale == "healthy"
    assert h.loop == "healthy"
    assert h.error == "healthy"


# --- Test: session_health — empty / unreadable ------------------------------


def test_session_health_empty_jsonl(tmp_path):
    """Empty jsonl → None (no messages, no last_ts)."""
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    result = health.session_health(str(p), last_ts=None, harness="claude")
    # Healthy newborn: no last_ts → stale healthy; no messages → loop/error healthy
    assert result is not None
    assert result.status == "healthy"
    assert result.stale == "healthy"


def test_session_health_nonexistent_file():
    """Missing jsonl → None."""
    result = health.session_health(
        "/nonexistent/path.jsonl", last_ts=None, harness="claude"
    )
    assert result is None


# --- Test: Stale signal -----------------------------------------------------


@pytest.mark.parametrize(
    "hrs_ago, expected",
    [
        (0.5, "healthy"),  # ≤ 6h
        (6.0, "healthy"),  # boundary — NOT warning (strict > 6h)
        (6.1, "warning"),  # > 6h
        (12.0, "warning"),  # > 6h, < 24h
        (23.9, "warning"),  # > 6h, < 24h
        (24.0, "warning"),  # boundary — NOT unhealthy (strict > 24h)
        (24.1, "unhealthy"),  # > 24h
        (100.0, "unhealthy"),
    ],
)
def test_stale_signal(tmp_path, hrs_ago, expected):
    """Stale signal computed from last_ts relative to now."""
    from datetime import datetime, timedelta, timezone

    p = tmp_path / "stale.jsonl"
    _write_jsonl(p, [{"role": "user", "content": [{"type": "text", "text": "hello"}]}])

    now = datetime.now(timezone.utc)
    last_ts_dt = now - timedelta(hours=hrs_ago)
    last_ts = last_ts_dt.isoformat()

    result = health.session_health(str(p), last_ts=last_ts, harness="claude", now=now)
    assert result is not None
    assert result.stale == expected
    assert result.stale_hrs == pytest.approx(hrs_ago, rel=0.1)  # ~hours ago


def test_stale_no_last_ts(tmp_path):
    """No last_ts → newborn → healthy stale."""
    p = tmp_path / "newborn.jsonl"
    _write_jsonl(p, [{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
    result = health.session_health(str(p), last_ts=None, harness="claude")
    assert result is not None
    assert result.stale == "healthy"
    assert result.stale_hrs == 0.0


# --- Test: Loop signal ------------------------------------------------------


def test_loop_no_consecutive_same_tool(tmp_path):
    """Different tools each turn → no loop."""
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Read", "id": "1", "input": {}},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash", "id": "2", "input": {}},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Write", "id": "3", "input": {}},
            ],
        },
    ]
    p = tmp_path / "no_loop.jsonl"
    _write_jsonl(p, msgs)
    result = health.session_health(
        str(p), last_ts="2026-07-25T00:00:00Z", harness="claude"
    )
    assert result is not None
    assert result.loop == "healthy"
    assert result.loop_count <= 1  # max run ≤ 1


def test_loop_5_consecutive_same_tool(tmp_path):
    """5 consecutive same tool → 🟡 warning."""
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash", "id": str(i), "input": {}}
            ],
        }
        for i in range(7)
    ]
    p = tmp_path / "loop5.jsonl"
    _write_jsonl(p, msgs)
    result = health.session_health(
        str(p), last_ts="2026-07-25T00:00:00Z", harness="claude"
    )
    assert result is not None
    assert result.loop == "warning"  # 7 ≥ 5 → warning
    assert result.loop_count == 7


def test_loop_10_consecutive_same_tool(tmp_path):
    """10+ consecutive same tool → 🔴 unhealthy."""
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash", "id": str(i), "input": {}}
            ],
        }
        for i in range(12)
    ]
    p = tmp_path / "loop10.jsonl"
    _write_jsonl(p, msgs)
    result = health.session_health(
        str(p), last_ts="2026-07-25T00:00:00Z", harness="claude"
    )
    assert result is not None
    assert result.loop == "unhealthy"  # 12 ≥ 10 → unhealthy
    assert result.loop_count == 12


def test_loop_fallback_text_heuristic(tmp_path):
    """When no tool_use blocks, fall back to text heuristic (first-word check)."""
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Read the file\n\ncontent here"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Read another file"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Read third file"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Read fourth file"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Read fifth file"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Bash something"}],
        },
    ]
    p = tmp_path / "loop_text.jsonl"
    _write_jsonl(p, msgs)
    result = health.session_health(
        str(p), last_ts="2026-07-25T00:00:00Z", harness="claude"
    )
    assert result is not None
    # First 5 = Read → run of 5 = warning
    assert result.loop == "warning"
    assert result.loop_count == 5


# --- Test: Error signal -----------------------------------------------------


def test_error_no_errors(tmp_path):
    """No error/refusal stop_reasons → healthy."""
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "ok too"}],
            "stop_reason": "end_turn",
        },
    ]
    p = tmp_path / "no_error.jsonl"
    _write_jsonl(p, msgs)
    result = health.session_health(
        str(p), last_ts="2026-07-25T00:00:00Z", harness="claude"
    )
    assert result is not None
    assert result.error == "healthy"
    assert result.error_count == 0


def test_error_3_of_last_10(tmp_path):
    """3 errors in last 10 turns → 🟡 warning."""
    msgs = []
    for i in range(10):
        is_error = i < 3  # first 3 = error
        msgs.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": f"turn {i}"}],
                "stop_reason": "error" if is_error else "end_turn",
            }
        )
    p = tmp_path / "error3.jsonl"
    _write_jsonl(p, msgs)
    result = health.session_health(
        str(p), last_ts="2026-07-25T00:00:00Z", harness="claude"
    )
    assert result is not None
    assert result.error == "warning"
    assert result.error_count == 3
    assert result.error_total == 10


def test_error_5_of_last_10(tmp_path):
    """5 errors in last 10 turns → 🔴 unhealthy (≥ 50%)."""
    msgs = []
    for i in range(10):
        is_error = i < 5  # first 5 = error
        msgs.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": f"turn {i}"}],
                "stop_reason": "error" if is_error else "end_turn",
            }
        )
    p = tmp_path / "error5.jsonl"
    _write_jsonl(p, msgs)
    result = health.session_health(
        str(p), last_ts="2026-07-25T00:00:00Z", harness="claude"
    )
    assert result is not None
    assert result.error == "unhealthy"
    assert result.error_count == 5


def test_error_refusal_counts(tmp_path):
    """stop_reason=refusal also counts as error."""
    msgs = []
    # 3 refusals + 5 ok = 8 turns → 3/8 = 37.5% < 50% → warning (not unhealthy)
    for i in range(3):
        msgs.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": f"no {i}"}],
                "stop_reason": "refusal",
            }
        )
    for i in range(5):
        msgs.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": f"ok {i}"}],
                "stop_reason": "end_turn",
            }
        )
    p = tmp_path / "error_refusal.jsonl"
    _write_jsonl(p, msgs)
    result = health.session_health(
        str(p), last_ts="2026-07-25T00:00:00Z", harness="claude"
    )
    assert result is not None
    # 3 refusals out of 8 = 37.5% < 50% → warning (not unhealthy)
    assert result.error == "warning"  # 3 ≥ 3 → warning
    assert result.error_count == 3
    assert result.error_total == 8


# --- Test: Overall status (worst-signal-wins) -----------------------------


def test_overall_status_worst_signal(tmp_path):
    """Helper to build a session with a loop of length N + stale hrs."""
    from datetime import datetime, timedelta, timezone

    # Build: stale 25h (= unhealthy) + no loop/error
    p = tmp_path / "worst.jsonl"
    _write_jsonl(p, [{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
    last_ts_dt = datetime.now(timezone.utc) - timedelta(hours=25)
    result = health.session_health(
        str(p),
        last_ts=last_ts_dt.isoformat(),
        harness="claude",
        now=datetime.now(timezone.utc),
    )
    assert result is not None
    # stale=unhealthy should dominate
    assert result.status == "unhealthy"


def test_overall_healthy_when_all_healthy(tmp_path):
    """All signals healthy → overall healthy."""
    from datetime import datetime, timedelta, timezone

    p = tmp_path / "all_healthy.jsonl"
    _write_jsonl(p, [{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
    last_ts_dt = datetime.now(timezone.utc) - timedelta(hours=0.5)
    result = health.session_health(
        str(p),
        last_ts=last_ts_dt.isoformat(),
        harness="claude",
        now=datetime.now(timezone.utc),
    )
    assert result is not None
    assert result.status == "healthy"


# --- Run ---------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
