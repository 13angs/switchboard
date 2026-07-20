"""Activity inference — 4-column session-centric model (v2).

Activity per session card is inferred from jsonl last-event state. Blocked takes
priority over Working/Awaiting (error state always visible).

Thresholds per plan D3 + D14 + D15:
  Working  — last_role=assistant + age < 15m + no error/refusal/denial
  Awaiting — last_role=user + turn_count > 0 + no error/refusal/denial
  Blocked  — stop_reason ∈ {error, refusal} OR permission_denials
  Idle     — age >= 15m OR (turn_count == 0 AND last_role is None)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .claude_store import SessionSummary

# --- activity ---
ACTIVITIES = ["Working", "Awaiting", "Blocked", "Idle"]

WORKING_WINDOW_SECONDS = 15 * 60  # D3: 15 minutes


def infer_activity(s: Optional[SessionSummary], now: Optional[datetime] = None) -> str:
    """Return one of Working | Awaiting | Blocked | Idle."""
    if s is None:
        return "Idle"

    now = now or datetime.now(timezone.utc)

    # Blocked takes priority (D14)
    if (
        s.had_error
        or s.permission_denials
        or s.last_stop_reason in ("error", "refusal")
    ):
        return "Blocked"

    age = s.age_seconds(now)
    if age is None:
        return "Idle"

    if age >= WORKING_WINDOW_SECONDS:
        return "Idle"

    if s.last_role == "user" and s.turn_count > 0:
        return "Awaiting"

    if s.last_role == "assistant":
        return "Working"

    # Fallback: no meaningful activity
    return "Idle"
