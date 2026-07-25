#!/usr/bin/env python3
"""ADR-0003: /agent page + view param, redirects, WS rename, no GET spawn.

Contract under test (docs/design/react-architecture.md § Route Design):
  - GET /agent                 -> agent.html (terminal view, default)
  - GET /agent?view=chat       -> agent.html (single agent shell)
  - GET /agent?view=<unknown>  -> agent.html (fallback = terminal)
  - GET /terminal?<qs>         -> 302 /agent?view=terminal&<qs>
  - GET /chat?<qs>             -> 302 /agent?view=chat&<qs> (NEVER spawns — the
    legacy GET-with-side-effects _chat() path is removed)
  - GET /ws/terminal           -> 404 (renamed to /ws/agent, no WS redirect)

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_agent_routes.py
"""

from __future__ import annotations

import http.client
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ORCH = Path(__file__).resolve().parents[1]
_PORT = 8801


def _get(path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", _PORT, timeout=5)
    conn.request("GET", path)
    return conn.getresponse()


def _serve(static_dir: str) -> subprocess.Popen:
    env = dict(
        os.environ,
        ORCH_STATIC_DIR=static_dir,
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
    return srv


def test_agent_routes():
    with tempfile.TemporaryDirectory() as static_dir:
        (Path(static_dir) / "agent.html").write_text("agent page")
        (Path(static_dir) / "index.html").write_text("board page")
        srv = _serve(static_dir)
        try:
            # -- /agent dispatch on view --
            r = _get("/agent?session_id=abc")
            assert (
                r.status == 200 and r.read() == b"agent page"
            ), f"/agent default: {r.status}"
            r = _get("/agent?session_id=abc&view=chat")
            assert (
                r.status == 200 and r.read() == b"agent page"
            ), f"/agent?view=chat must use the shared agent shell: {r.status}"
            r = _get("/agent?view=bogus")
            assert (
                r.status == 200 and r.read() == b"agent page"
            ), f"/agent?view=bogus must fall back to terminal view: {r.status}"

            # -- 302 redirects preserve the original query string --
            r = _get("/terminal?session_id=abc&harness=claude")
            loc = r.getheader("Location")
            assert r.status == 302, f"/terminal must 302, got {r.status}"
            assert loc == "/agent?view=terminal&session_id=abc&harness=claude", loc
            r.read()

            r = _get("/terminal")
            assert (r.status, r.getheader("Location")) == (
                302,
                "/agent?view=terminal",
            ), "bare /terminal"
            r.read()

            r = _get("/chat?session_id=abc")
            assert (r.status, r.getheader("Location")) == (
                302,
                "/agent?view=chat&session_id=abc",
            ), "chat redirect"
            r.read()

            # -- legacy GET spawn is gone: provider-only /chat redirects, never
            # spawns a PTY / returns JSON --
            r = _get("/chat?provider=claude")
            assert (r.status, r.getheader("Location")) == (
                302,
                "/agent?view=chat&provider=claude",
            ), f"/chat?provider= must redirect not spawn: {r.status}"
            r.read()

            # -- old WS path is gone (renamed to /ws/agent, no redirect) --
            conn = http.client.HTTPConnection("127.0.0.1", _PORT, timeout=5)
            conn.request(
                "GET",
                "/ws/terminal",
                headers={
                    "Upgrade": "websocket",
                    "Connection": "Upgrade",
                    "Sec-WebSocket-Key": "MDEyMzQ1Njc4OWFiY2RlZg==",
                    "Sec-WebSocket-Version": "13",
                },
            )
            r = conn.getresponse()
            assert r.status == 404, f"/ws/terminal must 404 after rename: {r.status}"
        finally:
            srv.terminate()
            srv.wait()


def _run():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    _run()
