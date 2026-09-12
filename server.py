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
    GET  /calendar?date=&before=&after= -> {days:[{date, present, blocks[], unmapped[]}]}
                                         (slices.md S9; bars carry their ritual per ADR-0036)
    GET  /roles/activity?days=&project= -> {roles[], repos[], unresolved, totals} (ADR-0039)
                                         commits shipped per role, paired with rows
                                         still open; 7 roles always, zeros included.
                                         `project` scopes both by path (ADR-0040 §SD5)
    GET  /session/<id>/transcript     -> {session_id, messages:[{role,text,ts}]}  (?since= optional)
    GET  /session/<id>/timeline       -> {session_id, harness, entries:[{tool, category,
                                         args_summary, args, ts, duration_ms,
                                         duration_state, result_summary, result_ts}]}
    POST /session/start               -> spawn a fresh harness PTY
                                         body {harness?, provider?, model?, effort?, prompt?}
                                         model pins the tier (ADR-0030); effort pins
                                         thinking depth (ADR-0032); prompt is typed
                                         into the PTY and submitted too when model
                                         is also given (ADR-0034 §SD1, ADR-0038 §SD2)
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
    daily_calendar,
    discovery,
    harness,
    lock,
    notifications,
    pricing,
    role_activity,
    state,
    terminal,
    transition,
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
    """Encode chat input as the submit key sequence expected by the harness.

    `claude` and `codex` both run raw-mode terminal UIs, and a raw-mode tty
    delivers the physical Enter key exactly as sent — no ICRNL/INLCR
    translation — so both need `\\r` (carriage return), which is what a real
    terminal emulator (and this repo's own `Agent.tsx` WS client) actually
    transmits for Enter. This was `\\n` for `claude` until `S24`'s live
    reproduction against the real binary proved it never submits: `\\n`
    written to the PTY sits in the input box, unread as "Enter", for as long
    as anything keeps retrying it. Every existing unit test stubs the harness
    binary with `cat` (`ORCH_CLAUDE_BIN=cat` in the `srv` fixture), so nothing
    here has ever checked the byte against the real TUI — `agy` (a different
    CLI entirely) keeps its prior `\\n` default rather than guessing.
    """
    submit = "\n" if harness_name == "agy" else "\r"
    return (text + submit).encode("utf-8")


# How long to let a freshly spawned TUI draw its input box before typing into
# it. Below this the keystrokes land before the box exists and are swallowed.
_PROMPT_SETTLE_S = 1.5

# How long to keep re-pressing the submit key on the dispatch path, and how
# often (S24 / ADR-0038 amendment below). Retrying is only safe for the
# submit key itself — retrying the prompt text would duplicate it visibly in
# the input box — so `_type_prompt` never appends it; `_submit_typed_prompt`
# owns the keypress and its own retry budget.
#
# 8.0 was the original guess and was wrong on this workspace's own host: live
# reproduction (S24, ADR-0038 Amendment 3) against the real server under
# real contention (this proot host runs several `claude` processes
# concurrently, including the one doing the reproducing) consistently took
# 35-50s for a freshly spawned session to become responsive enough to read
# its first byte of stdin at all — nothing to do with the submit key being
# wrong; the child simply had not been scheduled yet. The written `\r` sits
# correctly queued in the PTY's input buffer regardless of when the child
# gets CPU time to read it, so a longer window does not change what gets
# sent — only how long this function keeps offering the evidence loop a
# chance to see it before giving up and reporting failure.
_SUBMIT_RETRY_INTERVAL_S = 1.0
_SUBMIT_RETRY_WINDOW_S = 60.0


def _type_prompt(term: terminal.PtyTerminal, prompt: str) -> bool:
    """Type a prepared prompt's text into a fresh PTY. Never appends a submit
    key (ADR-0030 §SD3) — a resume/prompt-less/manual dispatch leaves it in
    the input box unsent; the dispatch path presses Enter itself afterward,
    via `_submit_typed_prompt`, as its own write (S24 — see that function's
    docstring for why the two must not share one `term.write()` call).

    Returns whether the text reached the PTY. A failure here is not fatal to
    the spawn: the session is already live and usable, so it is reported
    (`prompt_typed`) rather than raised.
    """
    if not term.is_alive():
        return False
    time.sleep(_PROMPT_SETTLE_S)
    try:
        term.write(prompt.encode("utf-8"))
    except OSError:
        return False
    return True


def _submit_typed_prompt(term: terminal.PtyTerminal) -> bool:
    """Press Enter for a prompt `_type_prompt` already typed (ADR-0034 §SD1,
    implemented at ADR-0038 §SD2 — this function is `S24`'s amendment to
    that implementation, not a new decision).

    `_type_prompt` used to hand the prompt text and the submit key to
    `term.write()` **together**, reusing `_chat_message_payload`. `S24` found
    that a single write carrying an embedded trailing `\\r`/`\\n` is exactly
    what the harness's TUI reads as a bracketed paste, not a keypress: the
    whole chunk lands in the input box as `[Pasted text …]` and the trailing
    byte becomes a literal newline *inside* that pasted text instead of
    submitting it. No first turn ever reaches the harness, so it never writes
    the jsonl that gives the session a `session_id` (ADR-0028) — the session
    is spawned but permanently invisible to the board.

    The fix is to never bundle the two again: the submit key goes in its own
    `term.write()`, using the same `_chat_message_payload` encoding (empty
    text, so only the submit byte(s) survive) — reused, not reimplemented.

    One write is still not enough to trust blindly — `_PROMPT_SETTLE_S` is an
    observed guess, not a signal that the TUI is actually ready
    (`risks.md S-03`), and a slow `SessionStart` hook can outlast it. Retrying
    the *text* would duplicate it on screen, but retrying only the submit key
    is safe: an extra `\\r`/`\\n` into an already-submitted, now-empty input
    box is a no-op. So this re-presses Enter every `_SUBMIT_RETRY_INTERVAL_S`
    until the harness's own jsonl proves the turn actually landed — read via
    `term.session_id`, which `_start_id_capture`'s background poll (started
    at spawn, already running by the time this executes) sets the moment it
    finds that jsonl — capped at `_SUBMIT_RETRY_WINDOW_S`. Evidence of a first
    turn decides when to stop, not elapsed time.

    Returns whether that evidence showed up before the window closed. This is
    the value `prompt_submitted` reports — bytes reaching the PTY is no
    longer sufficient to claim a submit happened.
    """
    submit_key = _chat_message_payload("", term.harness)
    deadline = time.time() + _SUBMIT_RETRY_WINDOW_S
    while term.is_alive() and term.session_id is None and time.time() < deadline:
        try:
            term.write(submit_key)
        except OSError:
            return False
        time.sleep(_SUBMIT_RETRY_INTERVAL_S)
    return term.session_id is not None


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
    effort: Optional[str] = None,
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
    would silently change the tier of work already in flight. `effort`
    (ADR-0032) rides the same fresh-spawn-only rule."""
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
            effort=None if session_id else effort,
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
            elif path == "/calendar":
                self._calendar(repo_root)
            elif path == "/roles/activity":
                self._role_activity(repo_root)
            elif path == "/work/transitions":
                self._work_transitions(repo_root)
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
            elif path == "/work/transition":
                self._work_transition(repo_root)
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

        def _calendar(self, repo_root: str):
            """GET /calendar — daily-schedule bars for the Work board (S9).

            `?date=YYYY-MM-DD` centers the window (default: today, Asia/Bangkok
            — the owner's day, not the container's UTC); `?before=`/`?after=`
            widen it (default 2/2, so 5 bars). Not HEAD-cached like /workspace:
            "today" moves with the clock, not with a commit.
            """
            qs = parse_qs(urlparse(self.path).query)
            date_param = (qs.get("date") or [""])[0]
            if date_param:
                try:
                    center = datetime.strptime(date_param, "%Y-%m-%d").date()
                except ValueError:
                    self._json(400, {"error": "date must be YYYY-MM-DD"})
                    return
            else:
                center = daily_calendar.today_bangkok()

            def _int_param(name: str, default: int) -> Optional[int]:
                raw = (qs.get(name) or [""])[0]
                if not raw:
                    return default
                try:
                    return int(raw)
                except ValueError:
                    return None

            before = _int_param("before", 2)
            after = _int_param("after", 2)
            if before is None or after is None or before < 0 or after < 0:
                self._json(400, {"error": "before/after must be non-negative integers"})
                return

            # ADR-0036 — the bars carry their ritual, so the board knows which
            # ones can be pressed. Resolved here rather than in the browser:
            # the "two keys on one bar = no button" rule and the id it composes
            # are one decision, and one decision belongs in one tested place.
            registry = workspace.ritual_registry(repo_root)
            days = daily_calendar.window_schedule(
                Path(repo_root),
                center,
                before=before,
                after=after,
                rituals=registry["rituals"],
            )
            self._json(
                200,
                {
                    "center": center.isoformat(),
                    "days": days,
                    "rituals": {
                        "present": registry["present"],
                        "reason": registry.get("reason", ""),
                        "source": registry["source"],
                        "declared": len(registry["rituals"]),
                    },
                },
            )

        def _role_activity(self, repo_root: str):
            """GET /roles/activity — commits per role vs rows still open (ADR-0039).

            The board's third data source, and the only one that reads history
            rather than the tree. Not HEAD-cached like /workspace: the answer is
            a function of the *window*, not of one commit, and the whole scan
            measured 0.47s over ~1,300 commits (ADR-0039 §SD7).
            """
            qs = parse_qs(urlparse(self.path).query)
            raw = (qs.get("days") or [""])[0]
            if raw:
                try:
                    days = int(raw)
                except ValueError:
                    self._json(400, {"error": "days must be an integer"})
                    return
            else:
                days = role_activity.DEFAULT_DAYS
            # ADR-0040 §SD5 — scoping is by path, and an unknown name is a 400
            # rather than an empty panel that reads like "shipped nothing".
            project = (qs.get("project") or [""])[0].strip() or None
            try:
                self._json(200, role_activity.role_activity(repo_root, days, project))
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

        def _work_transitions(self, repo_root: str):
            """GET /work/transitions — every move out of one row's current
            stage, evaluated for one acting role (ADR-0044 §SD5 · slices.md
            S43a · S43b).

            A read-only preview of the same `transition.evaluate()` the POST
            handler below calls to actually move the card — so a button on
            the board is a picture of this answer, never a second copy of the
            rule. `?role=` is the operator's *acting* role from the header
            picker (S43a), not the row's own `role` cell: those answer
            different questions (row-status.md § ตารางการส่งต่อ 🔑).
            """
            qs = parse_qs(urlparse(self.path).query)
            project = (qs.get("project") or [""])[0].strip()
            slice_id = (qs.get("slice_id") or [""])[0].strip()
            actor = (qs.get("role") or [""])[0].strip()
            if not all((project, slice_id, actor)):
                self._json(
                    400, {"error": "ต้องมี project · slice_id · role ครบทุกช่อง"}
                )
                return

            root = Path(repo_root)
            overview = workspace.workspace_overview(repo_root, use_cache=True)
            found = next(
                (
                    s
                    for p in overview["projects"]
                    if p["name"] == project
                    for s in p["slices"]
                    if s["id"] == slice_id
                ),
                None,
            )
            if found is None:
                self._json(404, {"error": f"ไม่พบแถว {slice_id} ใน {project}"})
                return

            table = transition.transitions(root)
            if not table["present"]:
                self._json(
                    200, {"present": False, "reason": table["reason"], "moves": []}
                )
                return
            self._json(
                200,
                {
                    "present": True,
                    "reason": "",
                    "moves": transition.candidates(root, row=found, actor_role=actor),
                },
            )

        def _work_transition(self, repo_root: str):
            """POST /work/transition — move one card one station (ADR-0044 §SD5).

            The gate runs HERE, not in the browser. A disabled button on the
            board is a picture of this handler's answer; a request that skips
            the UI meets the same answer, which is the whole point of there
            being one gate.
            """
            body, error = _read_json_body(self)
            if error:
                self._json(400, {"error": error})
                return
            body = body or {}
            project = (body.get("project") or "").strip()
            slice_id = (body.get("slice_id") or "").strip()
            to_stage = (body.get("to_stage") or "").strip()
            actor = (body.get("role") or "").strip()
            if not all((project, slice_id, to_stage, actor)):
                self._json(
                    400,
                    {"error": "ต้องมี project · slice_id · to_stage · role ครบทุกช่อง"},
                )
                return

            root = Path(repo_root)
            # The row comes from the same reader the board renders from, never
            # from the request: a caller must not be able to describe a row as
            # unblocked, or as having its criteria closed, and have that stand.
            overview = workspace.workspace_overview(repo_root, use_cache=False)
            found = next(
                (
                    s
                    for p in overview["projects"]
                    if p["name"] == project
                    for s in p["slices"]
                    if s["id"] == slice_id
                ),
                None,
            )
            if found is None:
                self._json(404, {"error": f"ไม่พบแถว {slice_id} ใน {project}"})
                return

            form = body.get("form") if isinstance(body.get("form"), dict) else {}
            office = (body.get("office") or "-").strip() or "-"
            client = (body.get("client") or "internal").strip() or "internal"
            result = transition.apply(
                root,
                project=project,
                row=found,
                to_stage=to_stage,
                actor_role=actor,
                office=office,
                client=client,
                form=form,
            )
            if not result["ok"]:
                # 409, not 400: the request was well formed, the rules said no.
                self._json(409, result)
                return

            workspace.invalidate_cache(repo_root)
            result["notified"] = transition.notify(
                transition.payload(
                    project=project,
                    row=found,
                    to_stage=to_stage,
                    actor_role=actor,
                    form=form,
                )
            )
            self._json(200, result)

        def _session_start(self, repo_root: str):
            """POST /session/start — spawn a fresh PTY, discover its session_id,
            return {session_id, session_started}. The only spawn surface
            (ADR-0003; legacy GET /chat spawn removed).

            v3.0 (ADR-0030) adds two optional fields:
              model  — pins the tier the session runs on, validated against the
                       lineup the *workspace* declares, never a list held here.
              prompt — typed into the PTY. When `model` is also given (the
                       board's dispatch dialog is the only caller that sends
                       both), the server submits it too (ADR-0034 §SD1,
                       ADR-0038 §SD2, amended by S24 — see
                       `_submit_typed_prompt`) — the click on "สั่งงาน" already
                       is the decision. Without `model`, the prompt is left
                       unsent as before (ADR-0030 §SD3).

            ADR-0032 adds a third:
              effort — pins the thinking depth. Validated against the fixed set
                       the `claude` CLI itself accepts (not workspace-declared —
                       that lineup belongs to the CLI, not to roles.md)."""
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

            requested_effort = body.get("effort")
            if requested_effort is not None and not isinstance(requested_effort, str):
                self._json(400, {"error": "effort must be a string"})
                return
            requested_effort = (requested_effort or "").strip().lower() or None
            if requested_effort:
                if requested_effort not in workspace.VALID_EFFORTS:
                    self._json(
                        400,
                        {
                            "error": f"unknown effort: {requested_effort}",
                            "allowed": sorted(workspace.VALID_EFFORTS),
                        },
                    )
                    return
                if (
                    requested_model
                    and workspace.model_tier(repo_root, requested_model) == "light"
                ):
                    # ADR-0032 §SD6: the light-tier model (Haiku) rejects
                    # `--effort` outright — it still uses `budget_tokens`.
                    self._json(
                        400,
                        {
                            "error": (
                                "effort ไม่รองรับกับ tier light — โมเดลนี้ใช้ "
                                "budget_tokens ไม่ใช่ effort"
                            )
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
                    None,
                    cwd,
                    harness_name,
                    provider,
                    child_env,
                    model=requested_model,
                    effort=requested_effort,
                )
            except ValueError as e:
                # e.g. model pinning asked of a harness whose flag is unverified
                self._json(400, {"error": str(e)})
                return
            discovery.invalidate_cache(repo_root)

            # ADR-0034 §SD1 / ADR-0038 §SD2: model+prompt together is the one
            # signature the board's dispatch dialog sends (DispatchDialog.tsx)
            # — resume, prompt-less spawn, and chat's own _chat_message_payload
            # path are untouched.
            submit_prompt = bool(requested_model) and bool(prompt)
            prompt_typed = _type_prompt(term, prompt) if prompt else False
            # S24: bytes reaching the PTY (`prompt_typed`) is not evidence the
            # harness accepted a first turn — `_submit_typed_prompt` presses
            # Enter as its own write and only reports True once the harness's
            # jsonl proves it landed.
            prompt_submitted = (
                _submit_typed_prompt(term) if prompt_typed and submit_prompt else False
            )

            # Wait for the id-capture thread to discover the session_id (max 30s).
            # Skipped on the dispatch-submit path: _submit_typed_prompt above
            # already waited on this exact evidence for up to
            # _SUBMIT_RETRY_WINDOW_S — running this a second time would only
            # add latency to a result it cannot change (nothing further
            # presses Enter here to produce new evidence for this loop to
            # find).
            if not (prompt_typed and submit_prompt):
                deadline = time.time() + 30
                while (
                    time.time() < deadline
                    and term.is_alive()
                    and term.session_id is None
                ):
                    time.sleep(0.25)

            sid = term.session_id
            if sid:
                self._json(
                    200,
                    {
                        "session_id": sid,
                        "attach_key": term.attach_key,
                        "session_started": True,
                        "harness": harness_name,
                        "provider": provider,
                        "model": requested_model,
                        "effort": requested_effort,
                        "prompt_typed": prompt_typed,
                        "prompt_submitted": prompt_submitted,
                    },
                )
            else:
                # ADR-0028 §SD1: the PTY already has an attach_key (assigned at
                # spawn, before session_id exists) — this is the id-less window
                # the ADR describes. Without it in this response the caller has
                # no identity to navigate with, and a second surface (the
                # terminal WS) spawns a duplicate PTY on first connect instead
                # of attaching to this one (risks.md S-11).
                self._json(
                    202,
                    {
                        "session_id": None,
                        "attach_key": term.attach_key,
                        "session_started": False,
                        "harness": harness_name,
                        "provider": provider,
                        "model": requested_model,
                        "effort": requested_effort,
                        "prompt_typed": prompt_typed,
                        "prompt_submitted": prompt_submitted,
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
