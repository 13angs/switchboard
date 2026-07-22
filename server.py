#!/usr/bin/env python3
"""Agent View — session-centric kanban + in-browser harness terminal.

Serves the kanban board + JSON API + WebSocket terminal. No daemon loop:
the browser polls /state. Zero external dependencies (Python 3 stdlib only).

Run:
    python3 projects/agent-view/repos/agent-view/server.py [--port 8787] [--repo /workspaces/my-projects]

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
    GET  /session/<id>/transcript     -> {session_id, messages:[{role,text,ts}]}  (?since= optional)
    POST /session/start               -> spawn a fresh harness PTY
    POST /session/<id>/message        -> write text to PTY stdin  body {text}
    POST /session/<id>/dismiss        -> {ok, session_id}
    POST /session/<id>/undismiss      -> {ok, session_id}
    POST /sessions/dismiss            -> {ok, session_ids[], count}
    POST /sessions/undismiss          -> {ok, session_ids[], count}
    POST /session/<id>/kill           -> {ok, session_id, killed}
    GET  /ws/agent?session_id=<id>    -> WebSocket upgrade → PTY (registry-backed;
                                         /ws/shell reserved for the future board
                                         shell terminal, ADR-0003)

Session lifecycle (v2.1): PTYs live in an in-memory registry keyed by session_id,
decoupled from WebSocket connections. Closing a WebSocket *detaches* (PTY keeps
running); an explicit kill terminates it; a reconnect re-attaches to the live PTY
or, if it died while detached, respawns via `claude --resume`.
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
    state,
    terminal,
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
# pid/fd can't be serialized anyway). Keyed by session_id; fresh sessions are
# keyed by a temporary "pending:<pid>" until their id is discovered, then re-keyed.
_registry: dict[str, terminal.PtyTerminal] = {}
_reg_lock = threading.Lock()

# Model-provider config from projects/agent-view/repos/agent-view/.env — read ONCE at start (C3).
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


def _start_id_capture(term: terminal.PtyTerminal, cwd: str, temp_key: str) -> None:
    """For a fresh session (no id at spawn), poll the project dir for the new
    jsonl, then re-key the registry from `temp_key` to the real session_id and
    push it to the browser. Resolves HLD open-question #1."""
    store = _store_for(term.harness)
    pre_existing = store.existing_session_ids_for_cwd(cwd)
    deadline = time.time() + 30

    def run():
        while time.time() < deadline and term.is_alive() and term.session_id is None:
            sid = store.newest_session_id_for_cwd(cwd, exclude=pre_existing)
            if sid:
                term.session_id = sid
                with _reg_lock:
                    if _registry.get(temp_key) is term:
                        _registry.pop(temp_key, None)
                    _registry[sid] = term
                # Persist Claude provider lock next to the new session's jsonl.
                if term.harness == "claude":
                    claude_store.write_provider(sid, cwd, term.provider)
                term.notify_session_id(sid)
                return
            time.sleep(0.5)

    threading.Thread(target=run, daemon=True).start()


def _get_or_spawn(
    session_id: Optional[str],
    cwd: str,
    harness_name: str = "claude",
    provider: str = "claude",
    child_env: Optional[dict] = None,
) -> tuple[terminal.PtyTerminal, bool]:
    """Return (terminal, reused). Reuse a live registered terminal for
    session_id; else spawn (injecting the provider env) + register + start
    reader (+ id capture for fresh).

    v2.3: Before spawning a *resume* (session_id given, not in registry),
    probes for an external live holder of the same session in `cwd` — a native
    harness session or another server instance already writing the same jsonl.
    Raises `lock.SessionBusy(pid)` if found (prevents two-writer corruption)."""
    with _reg_lock:
        term = _registry.get(session_id) if session_id else None
        if term is not None and not term.is_alive():
            # Stale: child died while detached (B7) — drop and respawn.
            _registry.pop(session_id, None)
            term = None
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
            env=child_env,
            output_observer=_observe_terminal_output,
            input_observer=_LIFECYCLE_DETECTOR.observe_input,
            close_observer=_LIFECYCLE_DETECTOR.stop,
        )
        key = session_id or f"pending:{term.pid}"
        _registry[key] = term

    term.start_reader(_drop_from_registry)
    if not session_id:
        _start_id_capture(term, cwd, key)
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


def _handle_ws_upgrade(request: BaseHTTPRequestHandler, repo_root: str) -> None:
    """Handle WebSocket upgrade for /ws/agent → attach to a registry PTY."""
    parsed = urlparse(request.path)
    qs = parse_qs(parsed.query)
    session_id = (qs.get("session_id") or [None])[0]

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
            session_id, cwd, harness_name, provider, child_env
        )
    except lock.SessionBusy as e:
        sub.on_control({"type": "error", "message": str(e)})
        sub.on_exit(None)
        return
    discovery.invalidate_cache(repo_root)
    term.attach(sub)

    # Tell the browser the session_id (known for resume; None for a brand-new
    # session until capture pushes it via notify_session_id).
    sub.on_control({"type": "session_id", "id": term.session_id})

    # Main loop: read WebSocket frames → write to PTY.
    buf = b""
    try:
        while sub.alive and term.is_alive():
            ready, _, _ = select.select([sock], [], [], 0.5)
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
            (ADR-0003; legacy GET /chat spawn removed)."""
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

            term, _reused = _get_or_spawn(None, cwd, harness_name, provider, child_env)
            discovery.invalidate_cache(repo_root)

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
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(repo_root))
    print(f"agent-view: http://127.0.0.1:{args.port}  (repo={repo_root})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
