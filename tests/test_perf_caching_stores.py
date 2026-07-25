#!/usr/bin/env python3
"""Perf caching contracts (PR-2) — codex/agy stores + archive + direct card
lookup must not re-parse everything on every 3s discover / transcript poll.

Covers:
  - codex_store mtime summary cache (F3)
  - agy_store._load_json mtime memoization (F4)
  - archive.auto_archive_candidates persists once for K candidates (F6 N+1)
  - discovery.card_for_session resolves a card directly, no full scan (F6 / ADR-0010 §2)

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_perf_caching_stores.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="orch-perf2-")
os.environ["ORCH_SESSION_ROOT"] = _TMP

import pytest  # noqa: E402

from control_plane import (  # noqa: E402
    agy_store,
    archive,
    claude_store,
    codex_store,
    discovery,
    gh,
)

# --- F3: codex mtime summary cache -------------------------------------------


def test_codex_read_session_cached_and_self_heals(monkeypatch, tmp_path):
    calls = {"n": 0}
    real = codex_store.read_session

    def _counting(p):
        calls["n"] += 1
        return real(p)

    monkeypatch.setattr(codex_store, "read_session", _counting)
    codex_store.invalidate_summary_cache()

    f = tmp_path / "sess.jsonl"
    f.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"session_id": "c1", "cwd": "/x"},
                "timestamp": "2026-07-19T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    codex_store._read_session_cached(f)
    codex_store._read_session_cached(f)
    assert calls["n"] == 1, "unchanged codex jsonl must not re-parse"

    os.utime(f, (time.time() + 10, time.time() + 10))
    codex_store._read_session_cached(f)
    assert calls["n"] == 2, "changed mtime must re-parse"


# --- F4: agy metadata json memoization ---------------------------------------


def test_agy_load_json_cached_by_mtime(tmp_path):
    with agy_store._JSON_CACHE_LOCK:
        agy_store._JSON_CACHE.clear()
    f = tmp_path / "meta.json"
    f.write_text('{"a": 1}', encoding="utf-8")

    d1 = agy_store._load_json(f)
    d2 = agy_store._load_json(f)
    assert d1 is d2, "unchanged metadata json must return the cached dict"
    assert d1 == {"a": 1}

    f.write_text('{"a": 2}', encoding="utf-8")
    os.utime(f, (time.time() + 10, time.time() + 10))
    d3 = agy_store._load_json(f)
    assert d3 is not d1 and d3 == {"a": 2}, "changed mtime must re-read"


# --- F6: archive auto-archive batches its writes -----------------------------


class _FakeSummary:
    def __init__(self, session_id, age):
        self.session_id = session_id
        self._age = age

    def age_seconds(self, now=None):
        return self._age


def test_auto_archive_persists_once_for_many_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path)
    saves = {"n": 0}
    real_save = archive._save

    def _counting_save(repo_root, entries):
        saves["n"] += 1
        return real_save(repo_root, entries)

    monkeypatch.setattr(archive, "_save", _counting_save)

    old = archive.AUTO_ARCHIVE_DAYS * 86400.0 + 1_000  # > 7d idle
    sessions = [_FakeSummary(f"s{i}", old) for i in range(5)]
    n = archive.auto_archive_candidates(sessions, "/repo")

    assert n == 5, "all idle sessions should auto-archive"
    assert saves["n"] == 1, "K candidates must persist with a single write, not K"
    # Ledger really reflects the batch.
    assert len(archive.load("/repo")) == 5


def test_auto_archive_no_candidates_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path)
    saves = {"n": 0}
    monkeypatch.setattr(
        archive, "_save", lambda *a, **k: saves.__setitem__("n", saves["n"] + 1)
    )
    fresh = [_FakeSummary("s0", 10.0)]  # 10s old → not idle
    assert archive.auto_archive_candidates(fresh, "/repo") == 0
    assert saves["n"] == 0, "no candidates → no write"


# --- F6: direct single-card lookup (ADR-0010 §2) -----------------------------


def test_card_for_session_direct_lookup(monkeypatch, tmp_path):
    # Isolate every store side + keep gh non-blocking.
    monkeypatch.setattr(claude_store, "PROJECTS_DIR", Path(_TMP))
    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(gh, "list_prs", lambda *a, **k: [])
    claude_store.invalidate_summary_cache()

    sid = "direct-lookup-sid"
    cwd = "/tmp/orch-perf2-direct"
    path = Path(_TMP) / claude_store.encode_cwd(cwd) / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": sid,
                "cwd": cwd,
                "timestamp": "2026-07-19T00:00:00Z",
                "message": {"role": "user", "content": "hi"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # Ensure the discovery cache is empty so a hit proves the DIRECT path, not a scan.
    discovery.invalidate_cache()
    card = discovery.card_for_session(sid, cwd)
    assert card is not None
    assert card.session_id == sid
    assert card.harness == "claude"

    assert discovery.card_for_session("no-such-id", cwd) is None


def _run():
    import inspect

    failures = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        if any(
            p in inspect.signature(fn).parameters for p in ("monkeypatch", "tmp_path")
        ):
            print(f"skip {name} (needs pytest fixtures)")
            continue
        try:
            fn()
            print(f"ok   {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{'PASS' if failures == 0 else 'FAIL'} — {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
