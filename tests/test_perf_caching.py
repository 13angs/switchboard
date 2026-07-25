#!/usr/bin/env python3
"""Perf caching contracts (PR-1) — the discover hot path must not re-read every
session jsonl / .env / spawn `gh` on every 3s /state poll.

Covers:
  - config.load_env_file() is mtime-cached (F5)
  - claude_store provider resolution is folded into the mtime summary cache so
    detect_provider runs once per file revision, and self-heals on mtime change (F1)
  - gh.list_prs_cached() never blocks on `gh`; it serves a snapshot and refreshes
    in the background (F2)

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_perf_caching.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Pin the session store at a temp dir BEFORE importing (config freezes it at
# import time), mirroring test_provider.py.
_TMP = tempfile.mkdtemp(prefix="orch-perf-")
os.environ["ORCH_SESSION_ROOT"] = _TMP

import pytest  # noqa: E402

from control_plane import claude_store, config, gh  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    monkeypatch.setenv("ORCH_SESSION_ROOT", _TMP)
    monkeypatch.setattr(claude_store, "PROJECTS_DIR", Path(_TMP))
    # Start each test from clean caches.
    claude_store.invalidate_summary_cache()
    with config._ENV_CACHE_LOCK:
        config._ENV_CACHE.clear()
    with gh._PR_CACHE_LOCK:
        gh._PR_CACHE.clear()
        gh._PR_REFRESHING.clear()


# --- F5: .env mtime cache ----------------------------------------------------


def test_load_env_file_caches_and_reparses_on_mtime_change():
    p = Path(_TMP) / "cache.env"
    p.write_text("ORCH_DEEPSEEK_MODEL=v1\n")
    first = config.load_env_file(p)
    second = config.load_env_file(p)
    assert first is second, "unchanged .env must return the cached dict object"
    assert first["ORCH_DEEPSEEK_MODEL"] == "v1"

    # Rewrite with a strictly newer mtime → cache must invalidate + re-parse.
    p.write_text("ORCH_DEEPSEEK_MODEL=v2\n")
    os.utime(p, (time.time() + 10, time.time() + 10))
    third = config.load_env_file(p)
    assert third is not first, "changed .env mtime must bust the cache"
    assert third["ORCH_DEEPSEEK_MODEL"] == "v2"


def test_load_env_file_missing_returns_empty():
    assert config.load_env_file(Path(_TMP) / "nope.env") == {}


# --- F1: provider folded into the mtime summary cache ------------------------


def _write_session(session_id: str, cwd: str) -> Path:
    path = Path(_TMP) / claude_store.encode_cwd(cwd) / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "cwd": cwd,
                "timestamp": "2026-07-19T00:00:00Z",
                "message": {"role": "user", "content": "hi"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_provider_resolved_once_per_mtime_and_self_heals(monkeypatch):
    calls = {"n": 0}

    def _counting_detect(session_id, env_file):
        calls["n"] += 1
        return None  # → falls through to the "claude" default

    monkeypatch.setattr(claude_store, "detect_provider", _counting_detect)
    path = _write_session("sess-perf", "/tmp/orch-perf-cwd")

    s1 = claude_store._read_session_cached(path)
    s2 = claude_store._read_session_cached(path)
    assert s1.provider == "claude"
    assert s2 is s1, "unchanged mtime must return the cached summary"
    assert calls["n"] == 1, "provider must resolve once per file revision"

    # Session grows (mtime advances) → provider re-resolves (self-heal path).
    os.utime(path, (time.time() + 10, time.time() + 10))
    claude_store._read_session_cached(path)
    assert calls["n"] == 2


def test_all_sessions_for_repo_does_not_reresolve_provider(monkeypatch):
    calls = {"n": 0}

    def _counting_detect(session_id, env_file):
        calls["n"] += 1
        return None

    monkeypatch.setattr(claude_store, "detect_provider", _counting_detect)
    _write_session("sess-repo", "/tmp/orch-perf-repo")

    claude_store.all_sessions_for_repo("/tmp/orch-perf-repo")
    after_first_poll = calls["n"]
    assert after_first_poll >= 1  # cold cache resolves provider once
    claude_store.all_sessions_for_repo("/tmp/orch-perf-repo")
    assert (
        calls["n"] == after_first_poll
    ), "a second poll (unchanged mtimes) must add zero provider re-reads"


# --- F2: gh PR cache is non-blocking ----------------------------------------


def test_list_prs_cached_is_nonblocking_and_populates(monkeypatch):
    started = []

    def _slow_list_prs(repo_dir, states=("open", "merged")):
        started.append(1)
        time.sleep(0.3)  # simulate a slow `gh` subprocess
        return [gh.PullRequest(1, "feat/x", "OPEN", "t", "http://x")]

    monkeypatch.setattr(gh, "list_prs", _slow_list_prs)

    t0 = time.time()
    first = gh.list_prs_cached("/repo")
    elapsed = time.time() - t0
    assert first == [], "first call returns an empty snapshot immediately"
    assert elapsed < 0.2, "list_prs_cached must not block on the gh subprocess"

    # Background refresh lands within a bounded wait.
    deadline = time.time() + 2.0
    while time.time() < deadline and not gh._PR_CACHE.get("/repo"):
        time.sleep(0.02)
    second = gh.list_prs_cached("/repo")
    assert len(second) == 1 and second[0].branch == "feat/x"
    assert sum(started) == 1, "exactly one background refresh should have fired"


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
