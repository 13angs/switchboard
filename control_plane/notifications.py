"""Harness lifecycle notifications.

This module deliberately does not use board `activity` labels. It watches live
PTY output for high-confidence approval prompts and publishes small lifecycle
events to browser listeners.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class NotificationEvent:
    type: str
    session_id: Optional[str]
    harness: str
    provider: str
    title: Optional[str]
    detected_at: str
    fingerprint: str

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "harness": self.harness,
            "provider": self.provider,
            "title": self.title,
            "detected_at": self.detected_at,
            "fingerprint": self.fingerprint,
        }


class NotificationHub:
    """In-memory pub/sub hub for SSE listeners."""

    def __init__(self):
        self._subs: list[queue.Queue[NotificationEvent]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[NotificationEvent]:
        q: queue.Queue[NotificationEvent] = queue.Queue(maxsize=100)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[NotificationEvent]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def publish(self, event: NotificationEvent) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


def _sid_or_pid(term) -> str:
    return term.session_id or f"pid:{term.pid}"


def _publish_when_identified(
    publish: Callable[[NotificationEvent], None],
    term,
    event_type: str,
    fingerprint_suffix: str,
    id_wait_seconds: float,
    id_poll_seconds: float,
) -> None:
    """Publish after a fresh PTY has a durable session_id.

    Fresh harness tabs start with `session_id=None` until the transcript store
    creates its record. Browser-tab alerts are keyed by session_id, so emitting
    before id capture makes new-session notifications impossible to route.
    """

    def build_event() -> NotificationEvent:
        sid = term.session_id
        return NotificationEvent(
            type=event_type,
            session_id=sid,
            harness=term.harness,
            provider=term.provider,
            title=None,
            detected_at=datetime.now(timezone.utc).isoformat(),
            fingerprint=f"{_sid_or_pid(term)}:{term.harness}:{fingerprint_suffix}",
        )

    if term.session_id:
        publish(build_event())
        return

    def run() -> None:
        deadline = time.time() + id_wait_seconds
        while term.session_id is None and term.is_alive() and time.time() < deadline:
            time.sleep(id_poll_seconds)
        if not term.is_alive():
            return
        publish(build_event())

    threading.Thread(target=run, daemon=True).start()


class HarnessOutputDetector:
    """Detect high-confidence approval prompts from harness PTY output."""

    _DEFAULT_PATTERNS: dict[str, list[tuple[str, str]]] = {
        "claude": [
            ("claude-allow-tool", r"\bdo you want to (?:allow|proceed|continue)\b"),
            ("claude-allow-options", r"\b1\.\s*yes\b[\s\S]{0,160}\b2\.\s*no\b"),
        ],
        "codex": [
            ("codex-allow-command", r"\ballow (?:command|this command)\?"),
            ("codex-approve", r"\bapprove[^\n]{0,120}\?"),
        ],
        "*": [
            (
                "generic-approval",
                r"\b(?:allow|approve|proceed|continue)[^\n]{0,120}\?\s*(?:\[[^\]]*[yY][^\]]*[nN][^\]]*\]|y/n|yes/no)",
            ),
        ],
    }

    def __init__(
        self,
        publish: Callable[[NotificationEvent], None],
        patterns: Optional[dict[str, list[tuple[str, str]]]] = None,
        dedupe_seconds: float = 60.0,
        on_approval: Optional[Callable[[object], None]] = None,
        id_wait_seconds: float = 30.0,
        id_poll_seconds: float = 0.25,
    ):
        self._publish = publish
        self._patterns = patterns or self._DEFAULT_PATTERNS
        self._dedupe_seconds = dedupe_seconds
        self._on_approval = on_approval
        self._id_wait_seconds = id_wait_seconds
        self._id_poll_seconds = id_poll_seconds
        self._buffers: dict[int, str] = {}
        self._last_emit: dict[str, float] = {}
        self._lock = threading.Lock()

    def inspect(self, term, data: bytes) -> None:
        text = data.decode("utf-8", errors="ignore")
        if not text:
            return
        now = time.time()
        key = id(term)
        with self._lock:
            buf = self._buffers.get(key, "")
            buf = _ANSI_RE.sub("", buf + text)
            buf = re.sub(r"\r", "\n", buf)
            buf = buf[-4000:]
            self._buffers[key] = buf

            match_result = self._match(term.harness, buf)
            if not match_result:
                return
            match_name, prompt_text = match_result

            fingerprint_suffix = self._fingerprint_suffix(match_name, prompt_text)
            fingerprint = self._fingerprint(term, fingerprint_suffix)
            pending_fingerprint = self._pending_fingerprint(term, fingerprint_suffix)
            last = max(
                self._last_emit.get(fingerprint, 0),
                self._last_emit.get(pending_fingerprint, 0),
            )
            if last and now - last < self._dedupe_seconds:
                return
            self._last_emit[fingerprint] = now
            self._last_emit[pending_fingerprint] = now

        _publish_when_identified(
            self._publish,
            term,
            "approval_required",
            fingerprint_suffix,
            self._id_wait_seconds,
            self._id_poll_seconds,
        )
        if self._on_approval:
            self._on_approval(term)

    def _match(self, harness: str, text: str) -> Optional[tuple[str, str]]:
        for name, pattern in self._patterns.get(harness, []):
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return name, match.group(0)
        for name, pattern in self._patterns.get("*", []):
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return name, match.group(0)
        return None

    def _fingerprint_suffix(self, match_name: str, prompt_text: str) -> str:
        prompt = " ".join(prompt_text.split()).lower()
        prompt_hash = str(abs(hash(prompt)) % 1_000_000_000)
        return f"{match_name}:{prompt_hash}"

    def _fingerprint(self, term, fingerprint_suffix: str) -> str:
        return f"{_sid_or_pid(term)}:{term.harness}:{fingerprint_suffix}"

    def _pending_fingerprint(self, term, fingerprint_suffix: str) -> str:
        return f"pending:{id(term)}:{term.harness}:{fingerprint_suffix}"


class HarnessLifecycleDetector:
    """Emit low-noise lifecycle events from PTY input/output boundaries."""

    def __init__(
        self,
        publish: Callable[[NotificationEvent], None],
        quiet_seconds: float = 25.0,
        id_wait_seconds: float = 30.0,
        id_poll_seconds: float = 0.25,
    ):
        self._publish = publish
        self._quiet_seconds = quiet_seconds
        self._id_wait_seconds = id_wait_seconds
        self._id_poll_seconds = id_poll_seconds
        self._states: dict[int, dict] = {}
        self._lock = threading.Lock()

    def observe_input(self, term, data: bytes) -> None:
        if not data:
            return
        key = id(term)
        with self._lock:
            state = self._states.get(key, {})
            self._cancel_timer(state)
            turn_no = int(state.get("turn_no", 0)) + 1
            self._states[key] = {
                "turn_no": turn_no,
                "input_seen": True,
                "output_seen": False,
                "approval_seen": False,
                "emitted_ready": False,
                "stopped": False,
                "timer": None,
            }

    def observe_output(self, term, data: bytes) -> None:
        if not data:
            return
        key = id(term)
        with self._lock:
            state = self._states.get(key)
            if state and state.get("stopped"):
                return
            # ADR-0010 §4: initialise on first output instead of requiring
            # prior input_seen. Covers fresh spawn (welcome text), resume
            # (status/replay), and re-attach after detach (mid-stream).
            if not state:
                state = {
                    "turn_no": 0,
                    "input_seen": False,
                    "output_seen": True,
                    "approval_seen": False,
                    "emitted_ready": False,
                    "stopped": False,
                    "timer": None,
                    "last_output": time.time(),
                }
                self._states[key] = state
            state["output_seen"] = True
            state["last_output"] = time.time()
            self._cancel_timer(state)
            if state.get("approval_seen"):
                return
            timer = threading.Timer(
                self._quiet_seconds,
                self._timer_fired,
                args=(key, term, state["turn_no"]),
            )
            timer.daemon = True
            state["timer"] = timer
            timer.start()

    def mark_approval(self, term) -> None:
        key = id(term)
        with self._lock:
            state = self._states.get(key)
            if not state or state.get("stopped"):
                return
            state["approval_seen"] = True
            self._cancel_timer(state)

    def stop(self, term) -> None:
        key = id(term)
        with self._lock:
            state = self._states.get(key)
            if not state:
                self._states[key] = {"stopped": True}
                return
            state["stopped"] = True
            self._cancel_timer(state)

    def _timer_fired(self, key: int, term, turn_no: int) -> None:
        event: Optional[NotificationEvent] = None
        with self._lock:
            state = self._states.get(key)
            if (
                not state
                or state.get("turn_no") != turn_no
                or state.get("stopped")
                or not state.get("output_seen")
                or state.get("approval_seen")
                or state.get("emitted_ready")
                or not term.is_alive()
            ):
                return
            state["emitted_ready"] = True
            state["timer"] = None
        _publish_when_identified(
            self._publish,
            term,
            "input_ready",
            f"input-ready:{turn_no}",
            self._id_wait_seconds,
            self._id_poll_seconds,
        )

    def _cancel_timer(self, state: dict) -> None:
        timer = state.get("timer")
        if timer:
            timer.cancel()
        state["timer"] = None


def sse_payload(event: NotificationEvent) -> bytes:
    body = json.dumps(event.to_dict(), separators=(",", ":"))
    return f"event: {event.type}\ndata: {body}\n\n".encode("utf-8")
