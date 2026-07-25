#!/usr/bin/env python3
"""Harness approval notification checks.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_notifications.py
"""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import notifications  # noqa: E402


class FakeTerm:
    def __init__(
        self,
        session_id: str = "sid-1",
        harness: str = "claude",
        provider: str = "claude",
        pid: int = 123,
    ):
        self.session_id = session_id
        self.harness = harness
        self.provider = provider
        self.pid = pid
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


def test_approval_waits_for_fresh_session_id():
    events = []
    detector = notifications.HarnessOutputDetector(
        events.append, id_wait_seconds=0.2, id_poll_seconds=0.01
    )
    term = FakeTerm(session_id=None, harness="codex", provider="openai")

    detector.inspect(term, b"Allow command? [y/n]\n")
    assert events == []

    term.session_id = "sid-fresh"
    time.sleep(0.08)

    assert len(events) == 1
    assert events[0].type == "approval_required"
    assert events[0].session_id == "sid-fresh"
    assert events[0].fingerprint.startswith("sid-fresh:codex:")

    detector.inspect(term, b"Allow command? [y/n]\n")
    assert len(events) == 1


def test_claude_approval_prompt_emits_once():
    events = []
    detector = notifications.HarnessOutputDetector(events.append)
    term = FakeTerm(harness="claude", provider="claude")

    detector.inspect(term, b"Do you want to allow this command?\n1. Yes\n2. No\n")
    detector.inspect(term, b"Do you want to allow this command?\n1. Yes\n2. No\n")

    assert len(events) == 1
    assert events[0].type == "approval_required"
    assert events[0].session_id == "sid-1"
    assert events[0].harness == "claude"


def test_codex_approval_prompt_emits_once():
    events = []
    detector = notifications.HarnessOutputDetector(events.append)
    term = FakeTerm(session_id="sid-2", harness="codex", provider="openai")

    detector.inspect(term, b"Allow command? [y/n]\n")
    detector.inspect(term, b"Allow command? [y/n]\n")

    assert len(events) == 1
    assert events[0].type == "approval_required"
    assert events[0].session_id == "sid-2"
    assert events[0].harness == "codex"


def test_normal_output_does_not_emit():
    events = []
    detector = notifications.HarnessOutputDetector(events.append)
    term = FakeTerm(harness="codex", provider="openai")

    detector.inspect(term, b"Running tests...\nAll checks passed.\n")

    assert events == []


def test_approval_detector_marks_lifecycle_approval():
    events = []
    marked = []
    detector = notifications.HarnessOutputDetector(
        events.append, on_approval=marked.append
    )
    term = FakeTerm(harness="claude", provider="claude")

    detector.inspect(term, b"Do you want to allow this command?\n1. Yes\n2. No\n")

    assert len(events) == 1
    assert marked == [term]


def test_sse_payload_format():
    event = notifications.NotificationEvent(
        type="approval_required",
        session_id="sid-3",
        harness="codex",
        provider="openai",
        title=None,
        detected_at="2026-07-16T15:00:00+00:00",
        fingerprint="fp-1",
    )

    payload = notifications.sse_payload(event)

    assert payload.startswith(b"event: approval_required\n")
    assert b'"session_id":"sid-3"' in payload
    assert payload.endswith(b"\n\n")


def test_input_ready_emits_once_after_quiet():
    events = []
    detector = notifications.HarnessLifecycleDetector(events.append, quiet_seconds=0.02)
    term = FakeTerm(session_id="sid-ready", harness="codex", provider="openai")

    detector.observe_input(term, b"go\n")
    detector.observe_output(term, b"working\n")
    time.sleep(0.08)

    assert len(events) == 1
    assert events[0].type == "input_ready"
    assert events[0].session_id == "sid-ready"
    assert events[0].fingerprint == "sid-ready:codex:input-ready:1"

    detector.observe_output(term, b"still here\n")
    time.sleep(0.08)

    assert len(events) == 1


def test_input_ready_resets_quiet_timer_on_more_output():
    events = []
    detector = notifications.HarnessLifecycleDetector(events.append, quiet_seconds=0.05)
    term = FakeTerm()

    detector.observe_input(term, b"go\n")
    detector.observe_output(term, b"part 1\n")
    time.sleep(0.03)
    detector.observe_output(term, b"part 2\n")
    time.sleep(0.03)

    assert events == []

    time.sleep(0.05)
    assert len(events) == 1
    assert events[0].type == "input_ready"


def test_input_ready_requires_output():
    events = []
    detector = notifications.HarnessLifecycleDetector(events.append, quiet_seconds=0.02)
    term = FakeTerm()

    detector.observe_input(term, b"go\n")
    time.sleep(0.08)

    assert events == []


def test_input_ready_suppressed_after_approval_same_turn():
    events = []
    detector = notifications.HarnessLifecycleDetector(events.append, quiet_seconds=0.02)
    term = FakeTerm()

    detector.observe_input(term, b"go\n")
    detector.mark_approval(term)
    detector.observe_output(
        term, b"Do you want to allow this command?\n1. Yes\n2. No\n"
    )
    time.sleep(0.08)

    assert events == []


def test_input_ready_does_not_emit_after_stop():
    events = []
    detector = notifications.HarnessLifecycleDetector(events.append, quiet_seconds=0.02)
    term = FakeTerm()

    detector.observe_input(term, b"go\n")
    detector.observe_output(term, b"working\n")
    detector.stop(term)
    time.sleep(0.08)

    assert events == []


def test_input_ready_emits_without_prior_input():
    """ADR-0010 §4: detector initialises on first output, not first input.
    Covers resume + fresh spawn where harness output arrives before user types."""
    events = []
    detector = notifications.HarnessLifecycleDetector(events.append, quiet_seconds=0.02)
    term = FakeTerm(session_id="sid-no-input", harness="claude", provider="claude")

    # Output arrives without any prior observe_input() — resume/replay case.
    detector.observe_output(term, b"Session resumed.\nAssistant: I'll help.\n")
    time.sleep(0.08)

    assert len(events) == 1
    assert events[0].type == "input_ready"
    assert events[0].session_id == "sid-no-input"


def test_input_ready_still_works_after_input_too():
    """Detector still works normally when input DOES precede output (regression)."""
    events = []
    detector = notifications.HarnessLifecycleDetector(events.append, quiet_seconds=0.02)
    term = FakeTerm(session_id="sid-normal", harness="codex", provider="openai")

    # Normal flow: input first, then output.
    detector.observe_input(term, b"fix the bug\n")
    detector.observe_output(term, b"Looking at the file...\n")
    time.sleep(0.08)

    assert len(events) == 1
    assert events[0].type == "input_ready"
    assert events[0].session_id == "sid-normal"
    # Verify the turn_no incremented from input.
    assert "turn_no:1" in events[0].fingerprint or events[0].fingerprint.endswith(":1")


def test_input_ready_waits_for_fresh_session_id():
    events = []
    detector = notifications.HarnessLifecycleDetector(
        events.append, quiet_seconds=0.02, id_wait_seconds=0.2, id_poll_seconds=0.01
    )
    term = FakeTerm(session_id=None, harness="codex", provider="openai")

    detector.observe_output(term, b"Codex ready.\n")
    time.sleep(0.04)
    assert events == []

    term.session_id = "sid-fresh-ready"
    time.sleep(0.08)

    assert len(events) == 1
    assert events[0].type == "input_ready"
    assert events[0].session_id == "sid-fresh-ready"
    assert events[0].fingerprint == "sid-fresh-ready:codex:input-ready:0"


def test_hub_publish_to_subscriber():
    hub = notifications.NotificationHub()
    sub = hub.subscribe()
    event = notifications.NotificationEvent(
        type="approval_required",
        session_id="sid-4",
        harness="claude",
        provider="claude",
        title=None,
        detected_at="2026-07-16T15:00:00+00:00",
        fingerprint="fp-2",
    )

    hub.publish(event)

    assert sub.get(timeout=1) is event
    hub.unsubscribe(sub)
    try:
        sub.get_nowait()
    except queue.Empty:
        pass


def _run():
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
