#!/usr/bin/env python3
"""ADR-0028: a reconnect finds the PTY it left, even before the session has an id.

The bug this pins down: a *fresh* session has no session_id until the harness
writes its jsonl, so a WebSocket reconnect during that window carried no
identity at all — and the server, seeing what looked like a first connect,
spawned a second PTY. The tab came back to a blank, brand-new session while the
original kept running, unreachable, in the registry.

The fix is an `attach_key` issued at spawn. These tests hold the two halves of
that contract: the key re-attaches, and a key that resolves to nothing is an
error rather than a silent new session.

Uses `cat` as the PTY child (ORCH_CLAUDE_BIN) — no `claude` binary needed.

Run:
    python3 projects/switchboard/repos/switchboard/tests/test_attach_key.py
    # or:  pytest projects/switchboard/repos/switchboard/tests/test_attach_key.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

os.environ["ORCH_CLAUDE_BIN"] = "cat"  # spawn `cat` instead of `claude`

import pytest  # noqa: E402

import server  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """Re-pin the PTY stub (other modules overwrite it at import time) and make
    sure no PTY outlives its test."""
    monkeypatch.setenv("ORCH_CLAUDE_BIN", "cat")
    server._registry.clear()
    yield
    for term in set(server._registry.values()):
        try:
            term.terminate()
        except Exception:
            pass
    server._registry.clear()


@pytest.fixture()
def cwd():
    """A directory with no harness project dir, so id capture finds nothing and
    the session stays in the pending window for the whole test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_fresh_spawn_issues_a_key_that_re_attaches(cwd):
    term, reused = server._get_or_spawn(None, cwd)
    assert reused is False
    assert term.attach_key and term.attach_key.startswith("attach:")
    assert server._registry[term.attach_key] is term
    # The session has no id yet — the key is the only handle that exists.
    assert term.session_id is None

    again, reused_again = server._get_or_spawn(None, cwd, attach_key=term.attach_key)
    assert reused_again is True
    assert again is term
    assert again.pid == term.pid, "reconnect must not spawn a second PTY"


def test_reconnect_without_a_key_still_spawns_a_new_session(cwd):
    """The first connect of a genuinely new session has no key — and must spawn.
    This is the behaviour the fix has to preserve while killing the duplicate."""
    first, _ = server._get_or_spawn(None, cwd)
    second, reused = server._get_or_spawn(None, cwd)
    assert reused is False
    assert second is not first


def test_unknown_key_is_an_error_and_spawns_nothing(cwd):
    before = dict(server._registry)
    with pytest.raises(server.AttachKeyUnknown):
        server._get_or_spawn(None, cwd, attach_key="attach:doesnotexist")
    assert server._registry == before, "a dead key must not leave a new PTY behind"


def test_key_still_resolves_after_the_session_id_is_discovered(cwd):
    """Id capture adds the session_id as a second alias; it must not invalidate
    the key a browser is already holding (a reconnect can race discovery)."""
    term, _ = server._get_or_spawn(None, cwd)
    key = term.attach_key

    # What _start_id_capture does once the harness writes its jsonl.
    term.session_id = "sid-discovered"
    with server._reg_lock:
        server._registry["sid-discovered"] = term

    by_id, reused_id = server._get_or_spawn("sid-discovered", cwd)
    by_key, reused_key = server._get_or_spawn(None, cwd, attach_key=key)
    assert (reused_id, reused_key) == (True, True)
    assert by_id is term and by_key is term


def test_stale_key_does_not_hijack_a_resume(monkeypatch, cwd):
    """A session with a real id that died while detached must still resume via
    the harness — the key is the identity of last resort, not a veto."""

    class _FakeTerm:
        pid = 4242
        session_id = None
        attach_key = None

        def is_alive(self):
            return True

        def start_reader(self, _cb):
            self.started = True

    fake = _FakeTerm()
    monkeypatch.setattr(server.terminal, "spawn_harness", lambda *a, **k: fake)
    monkeypatch.setattr(server.lock, "external_holder", lambda *a, **k: None)

    term, reused = server._get_or_spawn(
        "sid-that-died", cwd, attach_key="attach:stale"
    )
    assert reused is False
    assert term is fake
    assert server._registry["sid-that-died"] is fake


# --- client contract (ADR-0028 §SD1) --------------------------------------
# The server half is useless if the browser never sends the key back. These read
# the source rather than run a browser, in the style of test_react_migration.py.


def test_client_sends_the_key_while_the_session_has_no_id():
    agent = (_ROOT / "src" / "pages" / "Agent.tsx").read_text()
    assert "attach_key" in agent, "the reconnect URL must be able to carry the key"
    assert "ctl.type === 'attach'" in agent, "the client must record the issued key"


def test_client_resolves_the_reconnect_url_per_attempt():
    """A URL captured once at effect setup cannot carry a key that arrived
    later — which is exactly the pending window this ADR is about."""
    hook = (_ROOT / "src" / "hooks" / "useWebSocket.ts").read_text()
    assert "resolveUrlRef.current?.()" in hook


# --- dispatch surface carries the key too (S13, closes risks.md S-11) -----
# `_get_or_spawn` has issued an attach_key to every PTY since 0028, but
# `POST /session/start` — the dispatch surface's only spawn path — never put
# it on the wire, and the browser never carried it back. Dispatch would type
# the prompt into one PTY, then navigate to a page with no identity at all,
# which spawned a second, blank PTY (S-11's "PTY ลอยเกินมาหนึ่งใบ").


def test_session_start_returns_the_attach_key_it_already_issued():
    src = (_ROOT / "server.py").read_text()
    # Isolate the handler so this doesn't pass by matching some unrelated
    # "attach_key" string elsewhere in the file.
    start = src.index("def _session_start(")
    end = src.index("\n        def ", start + 1)
    body = src[start:end]
    assert body.count('"attach_key": term.attach_key') == 2, (
        "both the 200 (id known) and 202 (id-less window) branches must "
        "return the key — the id-less window is exactly when dispatch needs it"
    )


def test_dispatch_dialog_forwards_the_key_it_was_given():
    dialog = (_ROOT / "src" / "pages" / "DispatchDialog.tsx").read_text()
    assert "res.attach_key" in dialog, (
        "dispatch must read attach_key off the /session/start response and "
        "put it on the URL it navigates to, or the terminal page it opens "
        "has no identity to attach with"
    )


def test_agent_page_seeds_the_first_connect_from_the_url_attach_key():
    agent = (_ROOT / "src" / "pages" / "Agent.tsx").read_text()
    assert "qs.get('attach_key')" in agent, (
        "the page dispatch navigates to must read attach_key from its own "
        "URL, not only from a control frame received after connecting"
    )
    assert "attachKeyRef = useRef<string | null>(initialAttachKey)" in agent, (
        "the ref must be seeded before the first connect attempt — arriving "
        "later is too late for buildWsUrl's initial call"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
