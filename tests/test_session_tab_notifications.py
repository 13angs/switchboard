#!/usr/bin/env python3
"""Regression checks for session-scoped harness-tab notifications.

Run:
    python3 projects/agent-view/repos/agent-view/tests/test_session_tab_notifications.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_session_notification_hook_filters_to_current_session():
    hook = _read(SRC / "hooks" / "useSessionNotifications.ts")

    assert "new EventSource('/events')" in hook
    assert "if (!sessionId && event.session_id)" in hook
    assert "pendingBySessionRef.current.set(event.session_id, event)" in hook
    assert "if (event.session_id !== sessionId) return" in hook
    assert "document.visibilityState" in hook
    assert "document.title =" in hook
    assert "[Needs approval]" in hook
    assert "[Ready]" in hook


def test_board_notifications_are_browser_tab_only():
    hook = _read(SRC / "hooks" / "useNotifications.ts")
    header = _read(SRC / "components" / "board" / "Header.tsx")
    board = _read(SRC / "pages" / "Board.tsx")

    assert "new EventSource('/events')" in hook
    assert "new Notification" not in hook
    assert "Notification.requestPermission" not in hook
    assert "desktopEnabled" not in hook
    assert "onEnableNotifications" not in header
    assert "desktopNotifications" not in board


def test_agent_shell_uses_session_notification_hook():
    agent = _read(SRC / "pages" / "Agent.tsx")

    assert "useSessionNotifications" in agent
    assert "sessionAlert={sessionAlert}" in agent


def _run() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {e!r}")
    print(f"\n{'PASS' if failures == 0 else 'FAIL'} - {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
