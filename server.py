#!/usr/bin/env python3
"""Switchboard — session-centric kanban + in-browser harness terminal.

Serves the kanban board + JSON API + WebSocket terminal. No daemon loop:
the browser polls /state. Zero external dependencies (Python 3 stdlib only).

Run:
    python3 projects/switchboard/repos/switchboard/server.py [--port 8787] [--repo /workspaces/my-projects]

Endpoints:
    GET  /                            -> board (index.html)
    GET  /agent?session_id=<id>&view=terminal|chat
                                      -> agent page (single shell; view is
                                         handled client-side)
    GET  /terminal?<qs>               -> 302 /agent?view=terminal&<qs> (compat)
    GET  /chat?<qs>                   -> 302 /agent?view=chat&<qs> (compat; the
                                         legacy spawn-on-GET is removed — POST
                                         /session/start is the only spawn surface)
    GET  /state                       -> {generated_at, repo, sessions[], activities[]}
    GET  /events                      -> Server-Sent Events for lifecycle notifications
    GET  /health                      -> {ok: true}
    GET  /work                        -> work board page (work.html) (ADR-0029)
    GET  /workspace                   -> {head, projects[], totals, gaps} (ADR-0029)
    GET  /session/<id>/transcript     -> {session_id, messages:[{role,text,ts}]}  (?since= optional)
    GET  /session/<id>/timeline       -> {session_id, harness, entries:[{tool, category,
                                         args_summary, args, ts, duration_ms,
                                         duration_state, result_summary, result_ts}]}
    POST /session/start               -> spawn a fresh harness PTY
                                         body {harness?, provider?, model?, prompt?}
                                         model pins the tier (ADR-0030); prompt is
                                         typed into the PTY and left unsent
    POST /session/<id>/message        -> write text to PTY stdin  body {text}
    POST /session/<id>/dismiss        -> {ok, session_id}
    POST /session/<id>/undismiss      -> {ok, session_id}
    POST /sessions/dismiss            -> {ok, session_ids[], count}
    POST /sessions/undismiss          -> {ok, session_ids[], count}
    POST /session/<id>/kill           -> {ok, session_id, killed}
    GET  /ws/agent?session_id=<id>    -> WebSocket upgrade → PTY (registry-backed;
         /ws/agent?attach_key=<key>      /ws/shell reserved for the future board
                                         shell terminal, ADR-0003)

Session lifecycle (v2.1): PTYs live in an in-memory registry keyed by session_id,
decoupled from WebSocket connections. Closing a WebSocket *detaches* (PTY keeps
running); an explicit kill terminates it; a reconnect re-attaches to the live PTY
or, if it died while detached, respawns via `claude --resume`.

v2.4 (ADR-0028): a *fresh* session has no session_id until the harness writes its
jsonl, so the registry also keys every PTY by a server-assigned `attach_key`
handed to the browser at attach. A reconnect during that window carries the key
and re-attaches; without it the reconnect looked like a first connect and spawned
a second PTY, orphaning the first.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import select
import struct
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from control_plane import (
    agy_store,
    analytics,
    archive,
    claude_store,
    config,
    codex_store,
    discovery,
    harness,
    lock,
    notifications,
    pricing,
    state,
    terminal,
    workspace,
    ws_handler,
)

HERE = Path(__file__).resolve().parent
STATIC = Path(os.environ.get("ORCH_STATIC_DIR", str(HERE / "dist")))
# Legacy static directory — fallback during migration (Phases 1-3).
# After Phase 3, static/ is empty/deleted. Kept for backward compat:
# if dist/ doesn't exist (npm run build not yet run), fall back to
# any remaining static/ pages.
_STATIC_LEGACY = HERE / "static"


def _serve_static(path: str) -> Optional[Path]:
    """Return the file path to serve for a given route, checking STATIC first
    (dist/) then _STATIC_LEGACY (static/) as fallback. Returns None if neither
    has the file."""
    for base in (STATIC, _STATIC_LEGACY):
        f = base / path
        if f.is_file():
            return f
    return None


# --- session registry ----------------------------------------------------
# In-memory only (ephemeral by decision — not persisted across server restart;
# pid/fd can't be serialized anyway). Keyed by session_id once known, and by an
# `attach_key` ("attach:<uuid>") for the whole life of every PTY — the two are
# aliases for the same object, and `_drop_from_registry` removes both by
# identity when the child exits.
#
# ADR-0028 §SD1: the attach_key exists because session_id does not, yet. A fresh
# session's id only appears when the harness writes its jsonl (first prompt, or
# never for an idle session), and until then the browser has nothing to reconnect
# *with* — which is what made every reconnect in that window spawn a duplicate.
_registry: dict[str, terminal.PtyTerminal] = {}
_reg_lock = threading.Lock()


class AttachKeyUnknown(Exception):
    """A reconnect presented an attach_key no live PTY answers to — the session
    died while detached. Never spawn on this: the client asked for *that* PTY,
    and silently handing it a brand-new one is the bug ADR-0028 fixes."""


# ADR-0028 §SD2 — id-capture poll cadence. Fast while a first prompt is
# plausibly imminent, then slow forever: an idle session must still get its id
# whenever it finally produces one, and the cost of waiting is one glob.
_ID_CAPTURE_POLL_S = 0.5
_ID_CAPTURE_IDLE_POLL_S = 3.0
_ID_CAPTURE_FAST_WINDOW_S = 30

# ADR-0027 §SD1 — how often the server pings an attached client. Bounds how long
# a connection lost without a close frame keeps a thread and socket alive.
_WS_PING_INTERVAL_S = 30.0

# Model-provider config from projects/switchboard/repos/switchboard/.env — read ONCE at start (C3).
_ENV_FILE = config.load_env_file()
_NOTIFICATION_HUB = notifications.NotificationHub()
_LIFECYCLE_DETECTOR = notifications.HarnessLifecycleDetector(_NOTIFICATION_HUB.publish)
_OUTPUT_DETECTOR = notifications.HarnessOutputDetector(
    _NOTIFICATION_HUB.publish,
    on_approval=_LIFECYCLE_DETECTOR.mark_approval,
)


def _observe_terminal_output(term: terminal.PtyTerminal, data: bytes) -> None:
    _OUTPUT_DETECTOR.inspect(term, data)
    _LIFECYCLE_DETECTOR.observe_output(term, data)


def build_state(repo_root: str) -> dict:
    """Build /state response: all cards + activity list + available providers.
    Uses cached discovery (ADR-0010 §1) to avoid re-parsing all jsonl every 5s."""
    cards = discovery.discover_cached(repo_root)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo_root,
        "activities": state.ACTIVITIES,
        "providers": config.available_providers(_ENV_FILE),
        "launchers": harness.available_launchers(_ENV_FILE),
        "sessions": [c.to_dict() for c in cards],
    }


def find_card_by_session(
    session_id: str, repo_root: str
) -> Optional[discovery.SessionCard]:
    """Direct store lookup for a single session card (ADR-0010 §2).

    Resolves the card directly from the owning store's exact-match
    find_session_path + overlay — no full discovery scan. Falls back to the
    cached scan only when no store owns the id (legacy session that appears via
    discovery but has no own transcript file).
    """
    card = discovery.card_for_session(session_id, repo_root)
    if card is not None:
        return card

    # Fallback: cached scan (session might only appear via discovery).
    for c in discovery.discover_cached(repo_root):
        if c.session_id == session_id:
            return c
    return None


def _resolve_session_runtime(
    session_id: Optional[str],
    repo_root: str,
    qs: Optional[dict] = None,
) -> tuple[str, str, str]:
    """Return `(harness, provider, cwd)` for a fresh or existing session."""
    qs = qs or {}
    cwd = config.DEFAULT_CWD
    if not cwd or not os.path.isdir(cwd):
        cwd = repo_root

    requested_harness = (qs.get("harness") or [None])[0]
    requested_provider = (qs.get("provider") or [None])[0]

    if session_id:
        card = find_card_by_session(session_id, repo_root)
        if card:
            h = card.harness or "claude"
            # Resolve default provider per harness.
            if card.provider:
                p = card.provider
            elif h == "codex":
                p = "openai"
            elif h == "agy":
                p = "google"
            else:
                p = "claude"
            return h, p, card.worktree_path or cwd

        if codex_store.find_session_path(session_id):
            cpath = codex_store.find_session_path(session_id)
            if cpath:
                summary = codex_store.read_session(cpath)
                return "codex", summary.provider or "openai", summary.cwd or cwd

        if agy_store.find_session_path(session_id):
            cpath = agy_store.find_session_path(session_id)
            if cpath:
                summary = agy_store.read_session(cpath)
                return "agy", summary.provider or "google", summary.cwd or cwd

        cpath = claude_store.find_session_path(session_id)
        if cpath:
            summary = claude_store.read_session(cpath)
            provider = (
                claude_store.read_provider(session_id)
                or claude_store.detect_provider(session_id, _ENV_FILE)
                or summary.provider
                or "claude"
            )
            return "claude", provider, summary.cwd or cwd

        return (
            "claude",
            claude_store.read_provider(session_id)
            or claude_store.detect_provider(session_id, _ENV_FILE)
            or "claude",
            cwd,
        )

    h, p = harness.resolve(requested_provider, requested_harness)
    return h, p, cwd


def _session_from_path(path: str, suffix: str) -> Optional[str]:
    """Extract session_id from /session/<id>/<suffix>. Returns None on mismatch."""
    parts = [p for p in path.split("/") if p]
    if len(parts) == 3 and parts[0] == "session" and parts[2] == suffix:
        return unquote(parts[1])
    return None


def _read_json_body(
    request: BaseHTTPRequestHandler,
) -> tuple[Optional[dict], Optional[str]]:
    content_length = int(request.headers.get("Content-Length", 0))
    if content_length <= 0:
        return None, "empty body"
    try:
        body = json.loads(request.rfile.read(content_length))
    except (json.JSONDecodeError, ValueError):
        return None, "invalid JSON"
    if not isinstance(body, dict):
        return None, "JSON body must be an object"
    return body, None


def _chat_message_payload(text: str, harness_name: str) -> bytes:
    """Encode chat input as the submit key sequence expected by the harness."""
    submit = "\r" if harness_name == "codex" else "\n"
    return (text + submit).encode("utf-8")


# How long to let a freshly spawned TUI draw its input box before typing into
# it. Below this the keystrokes land before the box exists and are swallowed.
_PROMPT_SETTLE_S = 1.5


def _type_prompt(term: terminal.PtyTerminal, prompt: str) -> bool:
    """Type a prepared prompt into a fresh PTY — and deliberately not send it.

    The submit key is withheld on purpose (ADR-0030 SD3). The board's job is to
    assemble the context; deciding that the work should actually start is a
    person's, and the difference between the two is exactly one keystroke this
    server does not press. `_chat_message_payload` appends that keystroke; this
    path must never call it.

    Returns whether the text reached the PTY. A failure here is not fatal to the
    spawn: the session is already live and usable, the operator just has an
    empty input box, so it is reported rather than raised.
    """
    if not term.is_alive():
        return False
    time.sleep(_PROMPT_SETTLE_S)
    try:
        term.write(prompt.encode("utf-8"))
    except OSError:
        return False
    return True


def _transcript_source(
    session_id: str, repo_root: str
) -> tuple[str, Path] | tuple[None, None]:
    """Find the transcript reader + jsonl path for a session id.

    Primary path stays repo-scoped discovery. Fallback is an exact store lookup,
    which covers resume links for real sessions whose cwd no longer appears in
    the current board discovery result.
    """
    card = find_card_by_session(session_id, repo_root)
    if card and card.jsonl_path:
        return card.harness or "claude", Path(card.jsonl_path)

    cpath = claude_store.find_session_path(session_id)
    if cpath:
        return "claude", cpath

    xpath = codex_store.find_session_path(session_id)
    if xpath:
        return "codex", xpath

    apath = agy_store.find_session_path(session_id)
    if apath:
        return "agy", apath

    return None, None


# --- timeline (ADR-0017, amended by ADR-0025) -----------------------------

# Arguments that identify what a call acted on, most specific first. Anything
# not listed falls back to the first string value in the dict.
_ARGS_SUMMARY_KEYS = (
    "file_path",
    "notebook_path",
    "command",
    "path",
    "pattern",
    "url",
    "query",
    "description",
    "prompt",
)

_ARGS_SUMMARY_MAX = 120
_RESULT_SUMMARY_MAX = 200


def _tool_category(tool_name: str) -> str:
    """Filter-chip bucket for a tool name (ADR-0017 §SD2 + §SD3 override).

    FILE_TOOLS is imported, never re-declared — a third server-side copy of that
    map is exactly what §SD2 exists to prevent. The one deviation is Bash: the
    map calls it 'edit' (it *can* write files), but a filter chip that lumps
    shell commands in with file edits is useless for reading a session back.
    """
    if tool_name in ("Bash", "exec_command"):
        return "bash"
    return analytics.FILE_TOOLS.get(tool_name, "other")


def _args_summary(args: dict) -> str:
    """One-line label for a call's arguments."""
    if not isinstance(args, dict) or not args:
        return ""
    value = None
    for key in _ARGS_SUMMARY_KEYS:
        if isinstance(args.get(key), str) and args[key]:
            value = args[key]
            break
    if value is None:
        for candidate in args.values():
            if isinstance(candidate, str) and candidate:
                value = candidate
                break
    if value is None:
        return ""
    collapsed = " ".join(value.split())
    if len(collapsed) > _ARGS_SUMMARY_MAX:
        collapsed = collapsed[: _ARGS_SUMMARY_MAX - 1] + "…"
    return collapsed


def _result_summary(content) -> Optional[str]:
    """Truncated text of a tool_result's content (str or list of text blocks)."""
    if isinstance(content, str):
        return content[:_RESULT_SUMMARY_MAX] if content else None
    if isinstance(content, list):
        text = " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        return text[:_RESULT_SUMMARY_MAX] if text else None
    return None


def _build_timeline(store_module, jsonl_path) -> list[dict]:
    """Extract tool_use→tool_result pairs from a transcript.

    `ts` is an opaque per-harness ordering key, not a timestamp (ADR-0025 §SD1):
    it is handed to the *store's own* `_parse_ts`, and a duration is computed
    only when both ends parse. `duration_state` then names why a duration is
    missing, so a harness with no clock ('unsupported') never reads as a tool
    that is still running ('pending') — see ADR-0025 §SD2.

    Entries come back in store order. There is deliberately no sort: store order
    is already chronological, whereas sorting on `ts` lexicographically is only
    correct while every harness happens to emit a uniform UTC suffix and a
    fixed-width step counter (ADR-0025 §SD1).
    """
    messages = store_module.read_messages_rich(Path(jsonl_path))
    parse_ts = store_module._parse_ts
    entries: list[dict] = []
    pending: dict[str, dict] = {}  # tool_use id → entry awaiting its result

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name", "?")
                args = block.get("input", {})
                entry = {
                    "tool": name,
                    "category": _tool_category(name),
                    "args_summary": _args_summary(args),
                    "args": args,
                    "ts": msg.get("ts"),
                    "duration_ms": None,
                    "duration_state": "pending",
                    "result_summary": None,
                    "result_ts": None,
                }
                use_id = block.get("id") or ""
                if use_id:
                    # An id-less call can never be paired, so it must not sit in
                    # `pending` under a shared "" key — the next id-less result
                    # would otherwise attach to an unrelated call. It stays
                    # visible in `entries` and reads as "pending" forever, which
                    # is the honest outcome.
                    pending[use_id] = entry
                entries.append(entry)
            elif btype == "tool_result":
                result_id = block.get("tool_use_id") or ""
                # pop, not lookup: one result closes one call. A repeated id
                # cannot silently overwrite an already-timed entry.
                entry = pending.pop(result_id, None) if result_id else None
                if entry is None:
                    # Orphan result — a `since=` window can slice a transcript
                    # between a call and its result. Nothing to attach it to.
                    continue
                entry["result_ts"] = msg.get("ts")
                entry["result_summary"] = _result_summary(block.get("content", ""))
                t1 = parse_ts(entry["ts"])
                t2 = parse_ts(entry["result_ts"])
                if t1 and t2:
                    entry["duration_ms"] = int((t2 - t1).total_seconds() * 1000)

    for entry in entries:
        if parse_ts(entry["ts"]) is None:
            # Decided per entry from the ts itself, not from a hardcoded harness
            # list — a store that gains real timestamps starts reporting
            # "measured" with no change here (ADR-0025 §SD2).
            entry["duration_state"] = "unsupported"
        elif entry["duration_ms"] is not None:
            entry["duration_state"] = "measured"

    return entries


# --- registry helpers -----------------------------------------------------


def _drop_from_registry(term: terminal.PtyTerminal) -> None:
    """Remove a terminal from the registry by identity (called on child exit)."""
    with _reg_lock:
        for key, val in list(_registry.items()):
            if val is term:
                _registry.pop(key, None)


def _store_for(harness_name: str):
    if harness_name == "codex":
        return codex_store
    if harness_name == "agy":
        return agy_store
    return claude_store


def _start_id_capture(term: terminal.PtyTerminal, cwd: str) -> None:
    """For a fresh session (no id at spawn), poll the project dir for the new
    jsonl, then register the real session_id and push it to the browser.
    Resolves HLD open-question #1.

    ADR-0028 §SD2 — this poll runs for as long as the child lives. It used to
    give up after 30 seconds, which is shorter than a person can plausibly take
    to type their first prompt: a session opened and left idle past the deadline
    never got an id at all, so the board could not list it, the transcript could
    not load, and (before §SD1) every reconnect spawned a duplicate. The poll
    backs off instead of stopping — an idle PTY costs one `glob` every few
    seconds, and stopping costs the session its identity.

    The pre-spawn `attach_key` stays in the registry as an alias so a reconnect
    that raced the id discovery still lands on this same PTY.
    """
    store = _store_for(term.harness)
    pre_existing = store.existing_session_ids_for_cwd(cwd)
    fast_until = time.time() + _ID_CAPTURE_FAST_WINDOW_S

    def run():
        while term.is_alive() and term.session_id is None:
            sid = store.newest_session_id_for_cwd(cwd, exclude=pre_existing)
            if sid:
                term.session_id = sid
                with _reg_lock:
                    _registry[sid] = term
                # Persist Claude provider lock next to the new session's jsonl.
                if term.harness == "claude":
                    claude_store.write_provider(sid, cwd, term.provider)
                term.notify_session_id(sid)
                return
            time.sleep(
                _ID_CAPTURE_POLL_S
                if time.time() < fast_until
                else _ID_CAPTURE_IDLE_POLL_S
            )

    threading.Thread(target=run, daemon=True).start()


def _get_or_spawn(
    session_id: Optional[str],
    cwd: str,
    harness_name: str = "claude",
    provider: str = "claude",
    child_env: Optional[dict] = None,
    attach_key: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[terminal.PtyTerminal, bool]:
    """Return (terminal, reused). Reuse a live registered terminal for
    session_id or attach_key; else spawn (injecting the provider env) +
    register + start reader (+ id capture for fresh).

    v2.3: Before spawning a *resume* (session_id given, not in registry),
    probes for an external live holder of the same session in `cwd` — a native
    harness session or another server instance already writing the same jsonl.
    Raises `lock.SessionBusy(pid)` if found (prevents two-writer corruption).

    v2.4 (ADR-0028 §SD1): resolution order is session_id → attach_key → spawn.
    An attach_key that resolves to nothing raises `AttachKeyUnknown` rather than
    falling through to a spawn — a client holding a key is reconnecting, and the
    only honest answers are "here is your PTY" or "it is gone".

    v3.0 (ADR-0030): `model` pins the tier, and applies to a *fresh* spawn only.
    A resume re-enters a session that already has a model; re-pinning it here
    would silently change the tier of work already in flight."""
    with _reg_lock:
        term = _registry.get(session_id) if session_id else None
        if term is not None and not term.is_alive():
            # Stale: child died while detached (B7) — drop and respawn.
            _registry.pop(session_id, None)
            term = None
        if term is None and attach_key and not session_id:
            # Only consulted while the session has no id of its own. With an id
            # in hand, a missing registry entry means "died while detached" and
            # the resume path below is the right answer — not an error.
            candidate = _registry.get(attach_key)
            if candidate is not None and not candidate.is_alive():
                _registry.pop(attach_key, None)
                candidate = None
            if candidate is None:
                # No live PTY answers to this key. The session ended while the
                # browser was away; say so instead of spawning a stranger.
                raise AttachKeyUnknown(
                    "the session this tab was attached to is no longer running"
                )
            term = candidate
        if term is not None:
            return term, True

        # v2.3 single-writer guard: before a *resume*, check for a foreign
        # live holder of this same session. A sibling session in the same cwd
        # is allowed; a fresh spawn (no session_id) has no jsonl to corrupt yet.
        if session_id:
            holder = lock.external_holder(
                cwd,
                session_id=session_id,
                harness_name=harness_name,
            )
            if holder is not None:
                raise lock.SessionBusy(
                    f"session {session_id} is live in another process "
                    f"(pid {holder}) on {cwd} — close it before attaching here"
                )

        term = terminal.spawn_harness(
            harness_name,
            session_id=session_id,
            cwd=cwd,
            provider=provider,
            model=None if session_id else model,
            env=child_env,
            output_observer=_observe_terminal_output,
            input_observer=_LIFECYCLE_DETECTOR.observe_input,
            close_observer=_LIFECYCLE_DETECTOR.stop,
        )
        # Every PTY gets an attach_key, resume or fresh — the browser then has
        # one handle that is valid from the first byte (ADR-0028 §SD1).
        term.attach_key = f"attach:{uuid.uuid4().hex[:12]}"
        _registry[term.attach_key] = term
        if session_id:
            _registry[session_id] = term

    term.start_reader(_drop_from_registry)
    if not session_id:
        _start_id_capture(term, cwd)
    return term, False


class _WsSubscriber:
    """Server-side Subscriber: writes PTY output + control frames to one
    WebSocket socket. Thread-safe — the PTY read thread and the connection's
    own main loop both write through the shared lock."""

    def __init__(self, sock):
        self._sock = sock
        self._lock = threading.Lock()
        self.alive = True

    def _send(self, frame: bytes) -> None:
        with self._lock:
            if not self.alive:
                return
            try:
                self._sock.sendall(frame)
            except OSError:
                self.alive = False

    def on_data(self, data: bytes) -> None:
        # PTY output is raw bytes — send as a BINARY frame, never TEXT. A 4096
        # read can split a multi-byte UTF-8 sequence (Claude's TUI box-drawing /
        # emoji); a TEXT frame with a partial sequence makes the browser reject
        # the frame and close the socket ("Could not decode a text frame as
        # UTF-8"). Binary frames carry arbitrary bytes; xterm.js reassembles.
        self._send(ws_handler.encode_frame(ws_handler._OP_BINARY, data))

    def on_control(self, msg: dict) -> None:
        self._send(ws_handler.encode_text(json.dumps(msg)))

    def on_exit(self, code: Optional[int]) -> None:
        self._send(ws_handler.encode_text(json.dumps({"type": "exit", "code": code})))
        self._send(ws_handler.encode_close(1000))

    def pong(self, payload: bytes) -> None:
        self._send(ws_handler.encode_pong(payload))

    def ping(self, payload: bytes = b"") -> None:
        self._send(ws_handler.encode_ping(payload))


def _handle_ws_upgrade(request: BaseHTTPRequestHandler, repo_root: str) -> None:
    """Handle WebSocket upgrade for /ws/agent → attach to a registry PTY."""
    parsed = urlparse(request.path)
    qs = parse_qs(parsed.query)
    session_id = (qs.get("session_id") or [None])[0]
    # ADR-0028 §SD1 — the reconnect handle for a session whose id does not exist
    # yet. Sent back by the browser exactly as it was issued.
    attach_key = (qs.get("attach_key") or [None])[0]

    # Validate upgrade headers
    if request.headers.get("Upgrade", "").lower() != "websocket":
        request.send_response(400)
        request.end_headers()
        request.wfile.write(b"Upgrade: websocket required")
        return
    ws_key = request.headers.get("Sec-WebSocket-Key", "")
    if not ws_key:
        request.send_response(400)
        request.end_headers()
        request.wfile.write(b"Sec-WebSocket-Key required")
        return

    # HTTP 101 Switching Protocols
    request.send_response(101)
    request.send_header("Upgrade", "websocket")
    request.send_header("Connection", "Upgrade")
    request.send_header("Sec-WebSocket-Accept", ws_handler.compute_accept_key(ws_key))
    request.end_headers()
    request.wfile.flush()

    sock = request.connection
    if sock is None:
        return

    sub = _WsSubscriber(sock)

    harness_name, provider, cwd = _resolve_session_runtime(session_id, repo_root, qs)
    try:
        child_env = harness.provider_env(harness_name, provider, _ENV_FILE)
    except ValueError as e:
        # Misconfigured provider — tell the browser, don't spawn a bad session.
        sub.on_control({"type": "error", "message": str(e)})
        sub.on_exit(None)
        return

    # Attach to (or spawn) the session's PTY.
    # v2.3: catch SessionBusy (live external holder) → honest error.
    try:
        term, _reused = _get_or_spawn(
            session_id, cwd, harness_name, provider, child_env, attach_key
        )
    except lock.SessionBusy as e:
        sub.on_control({"type": "error", "message": str(e)})
        sub.on_exit(None)
        return
    except AttachKeyUnknown as e:
        # ADR-0028 §SD1 — the PTY this tab was attached to is gone. Report it
        # and close; do NOT spawn a replacement, which is what made a returning
        # tab land on a blank, brand-new session.
        sub.on_control({"type": "error", "message": str(e)})
        sub.on_exit(None)
        return
    discovery.invalidate_cache(repo_root)
    term.attach(sub)

    # The reconnect handle, valid from the first byte — before any session_id
    # exists (ADR-0028 §SD1). Sent first so a socket that dies moments later
    # still leaves the browser able to find its way back.
    sub.on_control({"type": "attach", "key": term.attach_key})

    # Tell the browser the session_id (known for resume; None for a brand-new
    # session until capture pushes it via notify_session_id).
    sub.on_control({"type": "session_id", "id": term.session_id})

    # Main loop: read WebSocket frames → write to PTY.
    buf = b""
    last_ping = time.monotonic()
    try:
        while sub.alive and term.is_alive():
            ready, _, _ = select.select([sock], [], [], 0.5)
            # ADR-0027 §SD1 — a peer that vanished without a close frame leaves
            # this loop reading a socket that will never speak again. A periodic
            # ping turns that into a write error, which marks the subscriber
            # dead and detaches; the PTY itself is untouched and waits for the
            # client's reconnect.
            now = time.monotonic()
            if now - last_ping >= _WS_PING_INTERVAL_S:
                last_ping = now
                sub.ping()
            if not ready:
                continue
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while sub.alive:
                opcode, payload = ws_handler.read_frame(buf)
                if opcode is None and payload is None:
                    break  # need more data
                consumed = _frame_bytes(buf)
                if consumed <= 0:
                    break
                buf = buf[consumed:]
                if opcode == ws_handler._OP_CLOSE:
                    sub.alive = False
                    break
                elif opcode == ws_handler._OP_PING:
                    sub.pong(payload or b"")
                elif opcode == ws_handler._OP_TEXT and payload:
                    text = payload.decode("utf-8", errors="replace")
                    if text.startswith("{") and '"type"' in text:
                        try:
                            ctl = json.loads(text)
                            if ctl.get("type") == "resize":
                                term.resize(ctl.get("rows", 24), ctl.get("cols", 80))
                                continue
                            # ADR-0027 §SD2 — the browser cannot send RFC 6455
                            # ping frames from JS, so its liveness probe arrives
                            # as an ordinary text frame. Answer it; never write
                            # it to the PTY.
                            if ctl.get("type") == "ping":
                                sub.on_control({"type": "pong"})
                                continue
                        except json.JSONDecodeError:
                            pass
                    term.write(payload)
    finally:
        # DETACH — leave the PTY running in the registry (do NOT terminate it).
        # The persistent reader keeps the PTY alive for a later reconnect.
        term.detach(sub)
        sub.alive = False


def _frame_bytes(data: bytes) -> int:
    """Return the byte length of the first complete WebSocket frame in `data`,
    or 0 if incomplete / unparseable."""
    if len(data) < 2:
        return 0
    length = data[1] & 0x7F
    offset = 2
    if length == 126:
        if len(data) < 4:
            return 0
        length = struct.unpack("!H", data[2:4])[0]
        offset = 4
    elif length == 127:
        if len(data) < 10:
            return 0
        length = struct.unpack("!Q", data[2:10])[0]
        offset = 10
    masked = (data[1] & 0x80) != 0
    if masked:
        offset += 4
    total = offset + length
    if len(data) < total:
        return 0
    return total


def make_handler(repo_root: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # quiet

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            # WebSocket upgrade — /ws/agent only (ADR-0003; /ws/shell is
            # reserved for the future board shell terminal)
            if path == "/ws/agent":
                _handle_ws_upgrade(self, repo_root)
                return
            if path.startswith("/ws/"):
                self._json(404, {"error": "not found"})
                return

            # Static files — check dist/ first, fall back to static/ (HLD §8)
            if path in ("/", "/index.html"):
                # Vite build → index.html; legacy → board.html
                f = _serve_static("index.html") or _serve_static("board.html")
                if f:
                    self._send(200, f.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._json(404, {"error": "not found"})
            elif path == "/agent":
                # One agent shell owns the session; view is a client-side
                # presentation state so switching views does not detach a PTY.
                f = _serve_static("agent.html")
                if f:
                    self._send(200, f.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._json(404, {"error": "not found"})
            elif path in ("/terminal", "/chat"):
                # ADR-0003 backward compat: old page routes 302 to /agent with
                # the original query preserved. GET /chat never spawns anymore
                # (legacy _chat() removed; POST /session/start is the only
                # spawn surface).
                view = "chat" if path == "/chat" else "terminal"
                location = f"/agent?view={view}"
                if parsed.query:
                    location += f"&{parsed.query}"
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
            # Vite build assets (JS, CSS chunks)
            elif path.startswith("/assets/"):
                asset = _serve_static(path.lstrip("/"))
                if asset:
                    ctype = (
                        "text/css; charset=utf-8"
                        if path.endswith(".css")
                        else "application/javascript; charset=utf-8"
                    )
                    self._send(200, asset.read_bytes(), ctype)
                else:
                    self._json(404, {"error": "not found"})
            elif path == "/analytics":
                # Analytics page — static HTML (v2.5)
                f = _serve_static("analytics.html")
                if f:
                    self._send(200, f.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._json(404, {"error": "not found"})
            elif path == "/analytics/files":
                self._analytics_files(repo_root)
            elif path == "/work":
                # Work page — static HTML (v3.0, ADR-0029)
                f = _serve_static("work.html")
                if f:
                    self._send(200, f.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._json(404, {"error": "not found"})
            elif path == "/workspace":
                self._workspace(repo_root)
            elif path == "/health":
                self._json(200, {"ok": True})
            elif path == "/state":
                try:
                    self._json(200, build_state(repo_root))
                except Exception as e:
                    self._json(500, {"error": str(e)})
            elif path == "/events":
                self._events()
            elif (sid := _session_from_path(path, "transcript")) is not None:
                self._transcript(sid, repo_root)
            elif (sid := _session_from_path(path, "timeline")) is not None:
                self._timeline(sid, repo_root)
            elif (sid := _session_from_path(path, "file")) is not None:
                self._file_content(sid, repo_root)
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/session/start":
                self._session_start(repo_root)
            elif path == "/sessions/dismiss":
                self._bulk_archive(repo_root, dismiss=True)
            elif path == "/sessions/undismiss":
                self._bulk_archive(repo_root, dismiss=False)
            elif (sid := _session_from_path(path, "dismiss")) is not None:
                archive.dismiss(sid, repo_root)
                discovery.invalidate_cache(repo_root)
                self._json(200, {"ok": True, "session_id": sid})
            elif (sid := _session_from_path(path, "undismiss")) is not None:
                archive.undismiss(sid, repo_root)
                discovery.invalidate_cache(repo_root)
                self._json(200, {"ok": True, "session_id": sid})
            elif (sid := _session_from_path(path, "kill")) is not None:
                self._kill(sid)
            elif (sid := _session_from_path(path, "message")) is not None:
                self._message(sid)
            else:
                self._json(404, {"error": "not found"})

        def _bulk_archive(self, repo_root: str, dismiss: bool):
            body, error = _read_json_body(self)
            if error:
                self._json(400, {"error": error})
                return

            raw_ids = body.get("session_ids") if body else None
            if not isinstance(raw_ids, list) or not raw_ids:
                self._json(400, {"error": "session_ids must be a non-empty list"})
                return
            if not any(isinstance(sid, str) and sid.strip() for sid in raw_ids):
                self._json(
                    400,
                    {"error": "session_ids must include at least one non-empty string"},
                )
                return

            if dismiss:
                changed = archive.dismiss_many(raw_ids, repo_root)
            else:
                changed = archive.undismiss_many(raw_ids, repo_root)

            discovery.invalidate_cache(repo_root)
            self._json(
                200,
                {
                    "ok": True,
                    "session_ids": changed,
                    "count": len(changed),
                },
            )

        def _kill(self, session_id: str):
            """Terminate a live PTY (SIGTERM) + drop it. If it is not in the
            registry (never live / already dead), graceful skip (B6)."""
            with _reg_lock:
                term = _registry.get(session_id)
            if term is not None:
                term.terminate()  # reader observes EOF → _drop_from_registry
                discovery.invalidate_cache()  # ADR-0010 — session card changed
                self._json(200, {"ok": True, "session_id": session_id, "killed": True})
            else:
                self._json(200, {"ok": True, "session_id": session_id, "killed": False})

        def _events(self):
            q = _NOTIFICATION_HUB.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    try:
                        event = q.get(timeout=15)
                        self.wfile.write(notifications.sse_payload(event))
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                _NOTIFICATION_HUB.unsubscribe(q)

        def _analytics_files(self, repo_root: str):
            """GET /analytics/files?days=1|7|30&harness=claude|codex|agy (v2.5)."""
            qs = parse_qs(urlparse(self.path).query)
            try:
                days = int((qs.get("days") or [None])[0] or "7")
            except (ValueError, TypeError):
                self._json(400, {"error": "days must be 1, 7, or 30"})
                return
            harness_param = (qs.get("harness") or [None])[0]
            if not harness_param:
                self._json(400, {"error": "harness is required (claude|codex|agy)"})
                return
            harness_param = harness_param.strip().lower()
            if harness_param not in analytics.VALID_HARNESSES:
                self._json(400, {"error": f"unknown harness: {harness_param}"})
                return
            try:
                result = analytics.files_analytics(repo_root, days, harness_param)
                self._json(200, result)
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:
                self._json(500, {"error": str(e)})

        def _workspace(self, repo_root: str):
            """GET /workspace — structural overview from the repo tree (ADR-0029).

            Second data source alongside the session stores: reads committed
            markdown, so it lags un-merged work by one PR and says so in the
            payload rather than implying live state.
            """
            qs = parse_qs(urlparse(self.path).query)
            fresh = (qs.get("refresh") or [""])[0] in ("1", "true", "yes")
            try:
                result = workspace.workspace_overview(repo_root, use_cache=not fresh)
                self._json(200, result)
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:
                self._json(500, {"error": str(e)})

        def _timeline(self, session_id: str, repo_root: str):
            """ADR-0017 §SD1 — tool calls for one session, on demand.

            Not polled: the timeline is derived from a transcript the browser is
            already reading, so a live session's new calls arrive on the next
            tab switch rather than on a timer.
            """
            harness_name, jsonl_path = _transcript_source(session_id, repo_root)
            if harness_name is None or jsonl_path is None:
                self._json(404, {"error": f"session '{session_id}' not found"})
                return
            jpath = Path(jsonl_path)
            if not jpath.exists():
                self._json(404, {"error": "transcript file not found"})
                return
            try:
                entries = _build_timeline(_store_for(harness_name), jpath)
            except Exception as e:
                self._json(500, {"error": str(e)})
                return
            self._json(
                200,
                {
                    "session_id": session_id,
                    "harness": harness_name,
                    "entries": entries,
                },
            )

        def _transcript(self, session_id: str, repo_root: str):
            harness_name, jsonl_path = _transcript_source(session_id, repo_root)
            if harness_name is None or jsonl_path is None:
                self._json(404, {"error": f"session '{session_id}' not found"})
                return
            jpath = Path(jsonl_path)
            if not jpath.exists():
                self._json(404, {"error": "transcript file not found"})
                return
            # Parse ?since=<iso-ts> and ?format=rich (ADR-0006).
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            since = (qs.get("since") or [None])[0]
            fmt = (qs.get("format") or [None])[0]
            rich = fmt == "rich"
            if harness_name == "codex":
                reader = (
                    codex_store.read_messages_rich
                    if rich
                    else codex_store.read_messages
                )
            elif harness_name == "agy":
                reader = (
                    agy_store.read_messages_rich if rich else agy_store.read_messages
                )
            else:
                reader = (
                    claude_store.read_messages_rich
                    if rich
                    else claude_store.read_messages
                )
            self._json(
                200,
                {
                    "session_id": session_id,
                    "messages": reader(jpath, since=since),
                },
            )

        def _file_content(self, session_id: str, repo_root: str):
            """GET /session/<id>/file?path=<abs_path> — return file contents
            from the session's worktree (view=files feature)."""
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            raw = (qs.get("path") or [None])[0]
            if not raw:
                self._json(400, {"error": "missing ?path="})
                return
            file_path = Path(raw)
            if not file_path.is_absolute() or not file_path.is_file():
                self._json(404, {"error": "file not found", "path": raw})
                return
            # Security: refuse files outside /workspaces (the only mount in devcontainer).
            # In a broader deploy, constrain this to a whitelist or the worktree root.
            try:
                file_path.resolve(strict=True)
            except (OSError, ValueError):
                self._json(404, {"error": "file not found", "path": raw})
                return
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError) as e:
                self._json(500, {"error": str(e), "path": raw})
                return
            self._json(
                200,
                {
                    "path": raw,
                    "content": text,
                    "size": len(text),
                },
            )

        def _session_start(self, repo_root: str):
            """POST /session/start — spawn a fresh PTY, discover its session_id,
            return {session_id, session_started}. The only spawn surface
            (ADR-0003; legacy GET /chat spawn removed).

            v3.0 (ADR-0030) adds two optional fields:
              model  — pins the tier the session runs on, validated against the
                       lineup the *workspace* declares, never a list held here.
              prompt — typed into the PTY and left unsent. The board prepares
                       the work; a person still presses Enter."""
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._json(400, {"error": "empty body"})
                return
            try:
                body = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, ValueError):
                self._json(400, {"error": "invalid JSON"})
                return

            requested_harness = body.get("harness")
            requested_provider = body.get("provider")
            if not isinstance(requested_harness, str):
                requested_harness = None
            if not isinstance(requested_provider, str):
                requested_provider = None

            requested_model = body.get("model")
            if requested_model is not None and not isinstance(requested_model, str):
                self._json(400, {"error": "model must be a string"})
                return
            requested_model = (requested_model or "").strip() or None
            if requested_model:
                allowed = workspace.allowed_models(repo_root)
                if not allowed:
                    self._json(
                        409,
                        {
                            "error": (
                                "workspace ยังไม่ได้ประกาศแผนที่ tier → model "
                                "— สั่งงานตาม tier ไม่ได้จนกว่าจะอ่านแผนที่นั้นได้"
                            )
                        },
                    )
                    return
                if requested_model not in allowed:
                    self._json(
                        400,
                        {
                            "error": f"unknown model: {requested_model}",
                            "allowed": sorted(allowed),
                        },
                    )
                    return

            prompt = body.get("prompt")
            if prompt is not None and not isinstance(prompt, str):
                self._json(400, {"error": "prompt must be a string"})
                return
            prompt = prompt or ""
            try:
                harness_name, provider = harness.resolve(
                    requested_provider, requested_harness
                )
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return

            try:
                child_env = harness.provider_env(harness_name, provider, _ENV_FILE)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return

            cwd = config.DEFAULT_CWD
            if not cwd or not os.path.isdir(cwd):
                cwd = repo_root

            try:
                term, _reused = _get_or_spawn(
                    None, cwd, harness_name, provider, child_env, model=requested_model
                )
            except ValueError as e:
                # e.g. model pinning asked of a harness whose flag is unverified
                self._json(400, {"error": str(e)})
                return
            discovery.invalidate_cache(repo_root)

            prompt_typed = _type_prompt(term, prompt) if prompt else False

            # Wait for the id-capture thread to discover the session_id (max 30s).
            deadline = time.time() + 30
            while (
                time.time() < deadline and term.is_alive() and term.session_id is None
            ):
                time.sleep(0.25)

            sid = term.session_id
            if sid:
                self._json(
                    200,
                    {
                        "session_id": sid,
                        "session_started": True,
                        "harness": harness_name,
                        "provider": provider,
                        "model": requested_model,
                        "prompt_typed": prompt_typed,
                    },
                )
            else:
                self._json(
                    202,
                    {
                        "session_id": None,
                        "session_started": False,
                        "harness": harness_name,
                        "provider": provider,
                        "model": requested_model,
                        "prompt_typed": prompt_typed,
                        "message": "Session starting; retry to re-check.",
                    },
                )

        def _message(self, session_id: str):
            """POST /session/<id>/message — write text to the session's PTY stdin.
            v2.3: resumes via _get_or_spawn when not in registry (mirror terminal)."""
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._json(400, {"error": "empty body"})
                return
            try:
                body = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, ValueError):
                self._json(400, {"error": "invalid JSON"})
                return

            text = body.get("text", "")
            if not text or not isinstance(text, str):
                self._json(400, {"error": "missing 'text' field"})
                return

            with _reg_lock:
                term = _registry.get(session_id)

            # v2.3: resume on send — if the session is not in the registry,
            # spawn it via _get_or_spawn (mirror terminal WS) so the "Chat"
            # button on an existing idle session works.
            if term is None:
                harness_name, provider, cwd = _resolve_session_runtime(
                    session_id, repo_root
                )
                try:
                    child_env = harness.provider_env(harness_name, provider, _ENV_FILE)
                except ValueError as e:
                    self._json(400, {"error": str(e)})
                    return
                try:
                    term, _reused = _get_or_spawn(
                        session_id, cwd, harness_name, provider, child_env
                    )
                except lock.SessionBusy as e:
                    self._json(409, {"error": str(e)})
                    return

            if not term.is_alive():
                self._json(410, {"error": "session ended"})
                return

            payload = _chat_message_payload(text, term.harness)
            try:
                term.write(payload)
            except OSError as e:
                self._json(500, {"error": f"stdin write failed: {e}"})
                return

            self._json(200, {"ok": True, "session_id": session_id})

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--port", type=int, default=int(os.environ.get("ORCH_PORT", "8787"))
    )
    ap.add_argument("--repo", default=os.environ.get("ORCH_REPO", os.getcwd()))
    args = ap.parse_args()

    repo_root = str(Path(args.repo).resolve())

    # Load pricing.json for token→USD cost (ADR-0022 §SD1 — the primary path,
    # not a fallback). Graceful skip: the server starts with cost disabled if
    # the file is missing or invalid, rather than pricing everything to None.
    pricing_json_path = str(HERE / "pricing.json")
    try:
        pricing_registry = pricing.load_pricing(pricing_json_path)
        claude_store.set_pricing(pricing_registry)
        print(f"switchboard: pricing.json loaded ({len(pricing_registry)} models)")
    except (FileNotFoundError, ValueError) as e:
        print(f"switchboard: pricing.json unavailable — cost disabled ({e})")
        claude_store.set_pricing(None)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(repo_root))
    print(f"switchboard: http://127.0.0.1:{args.port}  (repo={repo_root})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
