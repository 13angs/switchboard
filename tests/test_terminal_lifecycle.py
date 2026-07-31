#!/usr/bin/env python3
"""P1 session-lifecycle checks for Switchboard terminal.PtyTerminal.

Exercises the hard part of the in-memory registry design — the subscriber
swap + persistent reader + specific-pid reap + terminate — WITHOUT needing the
`claude` binary. We point spawn_claude at `cat` (echoes stdin to stdout) via
ORCH_CLAUDE_BIN, so the whole PTY lifecycle is testable with stdlib only.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_terminal_lifecycle.py
    # or, if pytest is available:  pytest projects/switchboard/repos/switchboard/tests
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# Make the `control_plane` package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["ORCH_CLAUDE_BIN"] = "cat"  # spawn `cat` instead of `claude`

import pytest  # noqa: E402

from control_plane import terminal  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Re-pin the PTY stub per test — other test modules overwrite
    ORCH_CLAUDE_BIN at import/run time (pytest imports all modules first),
    which used to spawn a nonexistent/instantly-exiting binary here."""
    monkeypatch.setenv("ORCH_CLAUDE_BIN", "cat")


class FakeSub:
    """Captures subscriber callbacks; signals an Event when data arrives."""

    def __init__(self):
        self.data = bytearray()
        self.controls = []
        self.exit_code = "UNSET"
        self.got_data = threading.Event()
        self.got_exit = threading.Event()

    def on_data(self, data: bytes) -> None:
        self.data += data
        self.got_data.set()

    def on_control(self, msg: dict) -> None:
        self.controls.append(msg)

    def on_exit(self, code) -> None:
        self.exit_code = code
        self.got_exit.set()

    def wait_for(self, needle: bytes, timeout: float = 5.0) -> bool:
        """Wait until `needle` is present in everything received so far.

        A PTY is a byte stream with no message boundaries, so one on_data
        callback is not guaranteed to carry a whole echo. Waiting on the
        first data event and asserting immediately races the tail — that
        produced an intermittent `b'ping' in bytearray(b'pin')` failure
        roughly one full-suite run in three.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.data:
                return True
            time.sleep(0.02)
        return needle in self.data


def _spawn():
    closed = {"term": None}
    term = terminal.spawn_claude(session_id=None, cwd=os.getcwd())
    term.start_reader(lambda t: closed.__setitem__("term", t))
    return term, closed


def test_reconnect_survives_detach_then_kill_cleans_up():
    """detach keeps the PTY alive; a new subscriber re-attaches and sees output;
    terminate() ends it and fires the registry on_close + subscriber on_exit."""
    term, closed = _spawn()
    try:
        # 1st connection
        sub1 = FakeSub()
        term.attach(sub1)
        term.write(b"ping\n")
        assert sub1.wait_for(b"ping"), (
            f"no complete PTY echo to first subscriber: {bytes(sub1.data)!r}"
        )
        assert term.is_alive()

        # detach — PTY must survive
        term.detach(sub1)
        time.sleep(0.3)
        assert term.is_alive(), "PTY died on detach (should survive)"

        # reconnect — new subscriber is seeded with the buffered scrollback
        # (ADR-0027 §SD3 reversed the original v2.1 "no replay" behaviour) and
        # then continues to receive live output.
        sub2 = FakeSub()
        term.attach(sub2)
        assert b"ping" in sub2.data, (
            f"replay buffer not delivered on attach: {bytes(sub2.data)!r}"
        )
        term.write(b"pong\n")
        assert sub2.wait_for(b"pong"), (
            f"reconnected subscriber got no complete echo: {bytes(sub2.data)!r}"
        )

        # kill — child dies, reader reaps, on_close + on_exit fire
        term.terminate()
        assert sub2.got_exit.wait(5), "subscriber on_exit not called after kill"
        assert not term.is_alive()
        assert closed["term"] is term, "registry on_close not called"
    finally:
        term.terminate()


def test_detach_guard_ignores_stale_subscriber():
    """detach(old_sub) must NOT detach a newer subscriber (late-close race)."""
    term, _ = _spawn()
    try:
        old = FakeSub()
        new = FakeSub()
        term.attach(old)
        term.attach(new)  # `new` is now current
        term.detach(old)  # stale detach — should be a no-op
        term.write(b"live\n")
        assert new.wait_for(b"live"), (
            f"current subscriber wrongly detached: {bytes(new.data)!r}"
        )
    finally:
        term.terminate()


def test_output_produced_while_detached_is_replayed_on_attach():
    """ADR-0027 §SD3 — the case the owner actually hits: the browser tab is
    discarded, the PTY keeps working, and the reconnecting client must not land
    on a blank screen."""
    term, _ = _spawn()
    try:
        sub1 = FakeSub()
        term.attach(sub1)
        term.write(b"before-detach\n")
        assert sub1.wait_for(b"before-detach")

        term.detach(sub1)
        # Output with nobody attached — this is what used to be dropped.
        term.write(b"while-detached\n")
        time.sleep(0.3)
        assert b"while-detached" not in sub1.data, "detached sub still receiving"

        sub2 = FakeSub()
        term.attach(sub2)
        assert b"while-detached" in sub2.data, (
            f"output produced while detached was lost: {bytes(sub2.data)!r}"
        )
    finally:
        term.terminate()


def test_replay_buffer_is_bounded():
    """The buffer is a screen aid, not a transcript — it must not grow without
    limit on a long-running session (ADR-0027 §SD3)."""
    term, _ = _spawn()
    try:
        chunk = b"x" * 4096
        written = 0
        target = terminal.REPLAY_BUFFER_BYTES * 2
        while written < target:
            term.write(chunk + b"\n")
            written += len(chunk)
            time.sleep(0.005)
        time.sleep(0.5)
        size = len(term._replay)  # noqa: SLF001 — the bound is the contract
        assert size <= terminal.REPLAY_BUFFER_BYTES, (
            f"replay buffer grew past its cap: {size}"
        )
    finally:
        term.terminate()


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
    print(f"\n{'PASS' if failures == 0 else 'FAIL'} — {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
