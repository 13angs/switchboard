#!/usr/bin/env python3
"""ADR-0055 live session interaction API checks."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


class FakeTerm:
    def __init__(self, session_id="sid-approval", harness="claude", provider="claude"):
        self.session_id = session_id
        self.harness = harness
        self.provider = provider
        self.pid = 4321
        self.writes = []
        self.alive = True

    def is_alive(self):
        return self.alive

    def write(self, data: bytes):
        self.writes.append(data)


def _request(port: int, method: str, path: str, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read())
    conn.close()
    return response.status, data


def _serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler("/tmp"))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _install(term: FakeTerm):
    with server._reg_lock:
        server._registry[term.session_id] = term


def _cleanup(term: FakeTerm):
    server._PENDING_INTERACTIONS.clear(term)
    with server._reg_lock:
        for key, value in list(server._registry.items()):
            if value is term:
                server._registry.pop(key, None)


def test_claude_approval_get_and_post_are_one_shot():
    term = FakeTerm()
    _install(term)
    interaction = server._PENDING_INTERACTIONS.record(
        term,
        "claude-allow-options",
        "1. Yes 2. No",
        "claude-allow-options:test",
    )
    httpd, thread = _serve()
    try:
        port = httpd.server_address[1]
        status, data = _request(port, "GET", f"/session/{term.session_id}/interaction")
        assert status == 200
        assert data["interaction"]["fingerprint"] == interaction.fingerprint
        assert [a["id"] for a in data["interaction"]["actions"]] == ["approve", "reject"]
        # Browser receives semantic actions only; raw PTY bytes stay server-side.
        assert "writes" not in data["interaction"]["actions"][0]

        status, data = _request(
            port,
            "POST",
            f"/session/{term.session_id}/interaction",
            {"action": "approve", "fingerprint": interaction.fingerprint},
        )
        assert status == 200, data
        assert term.writes == [b"1", b"\r"]

        status, data = _request(
            port,
            "POST",
            f"/session/{term.session_id}/interaction",
            {"action": "approve", "fingerprint": interaction.fingerprint},
        )
        assert status == 409, data
        assert term.writes == [b"1", b"\r"], "stale replay must write zero extra bytes"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        _cleanup(term)


def test_stale_fingerprint_never_writes():
    term = FakeTerm()
    _install(term)
    server._PENDING_INTERACTIONS.record(
        term,
        "claude-allow-options",
        "1. Yes 2. No",
        "claude-allow-options:new",
    )
    httpd, thread = _serve()
    try:
        port = httpd.server_address[1]
        status, _ = _request(
            port,
            "POST",
            f"/session/{term.session_id}/interaction",
            {"action": "reject", "fingerprint": "old-fingerprint"},
        )
        assert status == 409
        assert term.writes == []
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        _cleanup(term)


def test_codex_y_n_contract_writes_verified_sequence():
    term = FakeTerm(session_id="sid-codex", harness="codex", provider="openai")
    _install(term)
    interaction = server._PENDING_INTERACTIONS.record(
        term,
        "codex-allow-command",
        "Allow command? [y/n]",
        "codex-allow-command:test",
    )
    httpd, thread = _serve()
    try:
        port = httpd.server_address[1]
        status, data = _request(
            port,
            "POST",
            f"/session/{term.session_id}/interaction",
            {"action": "reject", "fingerprint": interaction.fingerprint},
        )
        assert status == 200, data
        assert term.writes == [b"n", b"\r"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        _cleanup(term)


def test_ambiguous_approval_is_notification_only():
    term = FakeTerm()
    _install(term)
    interaction = server._PENDING_INTERACTIONS.record(
        term,
        "claude-allow-tool",
        "Do you want to allow this command?",
        "claude-allow-tool:test",
    )
    httpd, thread = _serve()
    try:
        port = httpd.server_address[1]
        status, data = _request(port, "GET", f"/session/{term.session_id}/interaction")
        assert status == 200
        assert data["interaction"]["fingerprint"] == interaction.fingerprint
        assert data["interaction"]["actions"] == []
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        _cleanup(term)


def test_missing_live_pty_never_spawns():
    httpd, thread = _serve()
    try:
        port = httpd.server_address[1]
        status, data = _request(port, "GET", "/session/missing/interaction")
        assert status == 200 and data == {"interaction": None}

        status, data = _request(
            port,
            "POST",
            "/session/missing/interaction",
            {"action": "approve", "fingerprint": "fp"},
        )
        assert status == 409
        assert "active PTY" in data["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _run():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    print(f"\n{'PASS' if failures == 0 else 'FAIL'} - {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run())
