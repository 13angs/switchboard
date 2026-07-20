#!/usr/bin/env python3
"""Regression: PTY stdout must be sent as BINARY frames (opcode 0x2), control as
TEXT (0x1). A TEXT frame carrying raw PTY bytes with a split multi-byte UTF-8
sequence makes browsers reject the frame + close the socket ("Could not decode a
text frame as UTF-8") — the v2.1 WS bug. We round-trip a box-drawing char through
a `cat` PTY and assert it comes back intact as a binary frame.

Run:
    python3 projects/agent-view/repos/agent-view/tests/test_ws_framing.py
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

_ORCH = Path(__file__).resolve().parents[1]
_PORT = 8799


def _mask(payload: bytes, opcode: int = 0x1) -> bytes:
    b = bytes([0x80 | opcode])
    m = b"\xaa\xbb\xcc\xdd"
    n = len(payload)
    b += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack("!H", n)
    return b + m + bytes(c ^ m[i % 4] for i, c in enumerate(payload))


def _frame(s: socket.socket):
    s.settimeout(5)
    hdr = s.recv(2)
    op = hdr[0] & 0x0F
    ln = hdr[1] & 0x7F
    if ln == 126:
        ln = struct.unpack("!H", s.recv(2))[0]
    d = b""
    while len(d) < ln:
        d += s.recv(ln - len(d))
    return op, d


def test_pty_output_is_binary_control_is_text():
    env = dict(
        os.environ,
        ORCH_CLAUDE_BIN="cat",
        ORCH_DEFAULT_CWD="/tmp",
        ORCH_ENV_FILE="/nonexistent",
    )
    srv = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(_PORT), "--repo", "/tmp"],
        cwd=str(_ORCH),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    time.sleep(1.3)
    try:
        s = socket.create_connection(("127.0.0.1", _PORT))
        key = base64.b64encode(b"0123456789abcdef").decode()
        s.sendall(
            (
                f"GET /ws/agent HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        s.recv(1024)  # 101 switching protocols

        op, d = _frame(s)
        assert op == 0x1, f"session_id control must be TEXT(0x1), got {op:#x}"
        assert json.loads(d).get("type") == "session_id"

        payload = "┌─┐ hi ✓\n".encode("utf-8")  # multi-byte box-drawing + check
        s.sendall(_mask(payload))
        got = b""
        bin_seen = False
        for _ in range(6):
            op, d = _frame(s)
            if op == 0x2:
                bin_seen = True
                got += d
                if payload.rstrip(b"\n") in got:
                    break
        assert bin_seen, "PTY output never arrived as a BINARY frame (0x2)"
        assert b"\xe2\x94\x8c" in got, f"multi-byte bytes not intact: {got!r}"
        s.close()
    finally:
        srv.terminate()
        srv.wait()


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
