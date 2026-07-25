#!/usr/bin/env python3
"""Bulk archive/dismiss contracts for Switchboard.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_archive_batch.py
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import archive  # noqa: E402

_ORCH = Path(__file__).resolve().parents[1]
_PORT = 8814


def test_dismiss_many_persists_once():
    tmp = Path(tempfile.mkdtemp())
    original_archive_dir = archive.ARCHIVE_DIR
    original_save = archive._save
    archive.ARCHIVE_DIR = tmp
    saves = {"n": 0}

    def _counting_save(repo_root, entries):
        saves["n"] += 1
        original_save(repo_root, entries)

    archive._save = _counting_save

    try:
        changed = archive.dismiss_many(["s1", "s2", "s1"], "/repo")

        assert changed == ["s1", "s2"]
        assert saves["n"] == 1
        entries = archive.load("/repo")
        assert set(entries) == {"s1", "s2"}
        assert all(e.dismissed and not e.auto for e in entries.values())
    finally:
        archive._save = original_save
        archive.ARCHIVE_DIR = original_archive_dir


def test_undismiss_many_persists_once():
    tmp = Path(tempfile.mkdtemp())
    original_archive_dir = archive.ARCHIVE_DIR
    original_save = archive._save
    archive.ARCHIVE_DIR = tmp
    archive.dismiss_many(["s1", "s2", "s3"], "/repo")
    saves = {"n": 0}

    def _counting_save(repo_root, entries):
        saves["n"] += 1
        original_save(repo_root, entries)

    archive._save = _counting_save

    try:
        changed = archive.undismiss_many(["s1", "s3", "missing"], "/repo")

        assert changed == ["s1", "s3"]
        assert saves["n"] == 1
        assert set(archive.load("/repo")) == {"s2"}
    finally:
        archive._save = original_save
        archive.ARCHIVE_DIR = original_archive_dir


def _serve(static_dir: str, archive_dir: str) -> subprocess.Popen:
    env = dict(
        os.environ,
        ORCH_STATIC_DIR=static_dir,
        ORCH_ARCHIVE_DIR=archive_dir,
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


def _post(path: str, payload: dict) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", _PORT, timeout=5)
    body = json.dumps(payload).encode()
    conn.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    res = conn.getresponse()
    raw = res.read()
    return res.status, json.loads(raw.decode() or "{}")


def test_bulk_dismiss_routes_accept_many_session_ids():
    with tempfile.TemporaryDirectory() as static_dir, tempfile.TemporaryDirectory() as archive_dir:
        (Path(static_dir) / "agent.html").write_text("agent page")
        (Path(static_dir) / "index.html").write_text("board page")
        srv = _serve(static_dir, archive_dir)
        try:
            status, body = _post("/sessions/dismiss", {"session_ids": ["s1", "s2", "s1"]})
            assert status == 200, f"dismiss route status {status}: {body}"
            assert body == {"ok": True, "session_ids": ["s1", "s2"], "count": 2}

            status, body = _post("/sessions/undismiss", {"session_ids": ["s1"]})
            assert status == 200, f"undismiss route status {status}: {body}"
            assert body == {"ok": True, "session_ids": ["s1"], "count": 1}

            status, body = _post("/sessions/dismiss", {"session_ids": []})
            assert status == 400
            assert "session_ids" in body.get("error", "")
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
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {e!r}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    _run()
