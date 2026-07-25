# ADR-0005: Chat View as Read-Only Transcript Viewer

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-07-18 |
| **Deciders** | Don (owner) |
| **Supersedes** | — (new decision; does not invalidate prior ADRs) |

## Context

ADR-0004 unified the agent shell and made chat an in-tab view alongside
terminal. Both views share one PTY session; switching does not detach.

The chat view currently supports sending messages via `POST /session/<id>/message`
(`server.py:_message()`), which writes the text to the session's PTY stdin.
The intent was to give the chat view first-class interactivity — the user could
type in a chat-style input and have it submitted to the harness.

In practice this has not worked reliably:

1. **Two writers, one PTY.** The terminal WebSocket and the chat POST both write
   to the same PTY stdin. There is no coordination — text injected from chat
   arrives at the PTY independently of the terminal UI state.
2. **Harness input is not plain text.** Claude Code and Codex accept input through
   terminal key sequences, not raw stdin injection. Commit `3116324` adjusted the
   submit key (`\n` → `\r` for Codex), but the deeper problem — that the harness
   may not be in a state to accept input when the chat sends — remains.
3. **Fragile send → poll loop.** After sending, the client polls the transcript
   every 1s waiting for an assistant response. A mistimed send (harness busy in a
   tool call, streaming output, or waiting for a permission decision) breaks this
   loop — the send silently fails, and the typing indicator spins until timeout.

The owner has decided: **chat sending is not worth fixing**. The terminal view is
the correct interaction surface (it owns the PTY WebSocket). The chat view's
value lies elsewhere — as a structured, readable transcript of the conversation.

## Decision

**Make the chat view read-only.** Position it as a **transcript viewer** —
a structured, searchable, scrollable record of the conversation — distinct from
the terminal which remains the sole interaction surface.

### Phase 1 — Frontend-only (immediate)

No API changes. Three frontend changes in `AgentPage` + chat components:

| # | Change | Rationale |
|---|---|---|
| 1 | **Replace `ChatInput` → read-only status bar** | Remove the textarea+Send; show session state + a "Switch to Terminal to interact" link. Eliminates the broken send path and makes the read-only intent explicit. |
| 2 | **Smart scroll — pause auto-scroll when reading history** | When the user scrolls up, stop auto-scrolling to bottom. Show a floating "↓ New messages" badge. Standard chat UX — the user can read history without fighting the scroll. |
| 3 | **Turn separators + contextual empty states** | Group messages into conversation turns (user → assistant cycles) with visual breaks. Empty/loading states show session context instead of a generic "Send a message to start." |

### Phase 2 — Rich transcript (follow-up ADR)

The JSONL transcript contains structured content blocks (thinking, tool_use,
tool_result, text) that the current `read_messages()` flattens into plain text
(`[tool: Read]`, `[tool_result]` — losing information). Phase 2 adds:

| # | Change | Rationale |
|---|---|---|
| 4 | **`GET /session/<id>/transcript?format=rich`** | Return content blocks preserving structure, not flattened text |
| 5 | **Rich block rendering** | Thinking blocks (collapsible `💭`), tool calls (`🔧 name + inputs`), tool results (collapsible `📋`), per-message metadata (model, tokens, stop_reason) |

Phase 2 requires a separate ADR (API contract + backward compat for `?format=`
param). This ADR only gates Phase 1.

### What is NOT changed

- **Terminal view** — unchanged. Remains the sole interaction surface.
- **WebSocket lifecycle** — terminal pane stays mounted on chat switch (ADR-0004
  contract). No PTY behavior changes.
- **Transcript polling** — still polls every 5s (steady) / 1s (during active
  turn detected via `input_ready` SSE).
- **`POST /session/<id>/message` endpoint** — remains in `server.py` (it is the
  correct API surface for programmatic message sending; it just isn't wired to
  the chat UI anymore).
- **`sendMessage()` in `api.ts`** — kept but unused by chat components. Removed
  when no callers remain (Phase 2 cleanup).

## Options considered

### Option A: Fix the send path (add PTY coordination)

Add a lock or queue to serialize PTY writes from chat POST and terminal WS.
Encode harness-specific key sequences instead of raw `\n`/`\r`.

| Pro | Con |
|---|---|
| Chat becomes genuinely interactive | Fundamental mismatch: harness input is terminal-level, not message-level |
| Single interaction surface | The lock itself adds complexity and a new failure mode |
| | Terminal already provides perfect interaction — chat send is redundant |

Rejected. The harness is a terminal application; injecting text into its PTY
from a side channel is fighting the model, not working with it.

### Option B: Disable the input only (minimal change)

Set `disabled={true}` on `ChatInput`, change the placeholder to "Read-only."

| Pro | Con |
|---|---|
| Smallest diff (1 line) | Confusing — a disabled input with no explanation looks broken, not intentional |
| | Leaves the "Send a message to start" empty state |
| | No improvement to the reading experience |

Rejected. A disabled input is a worse UX than removing it entirely — it signals
"this should work but it's broken" rather than "this is a read-only view."

### Option C: Read-only transcript viewer, Phase 1 enhancements (chosen)

| Pro | Con |
|---|---|
| Clear design intent — read-only is a feature, not a limitation | Requires 3 component changes (~80 lines net) |
| Smart scroll + turn separators make reading history practical | |
| One-click switch to terminal when interaction is needed | |
| No API changes — entirely frontend | |

Chosen. The chat view becomes a better transcript reader than the raw terminal,
and the "Switch to Terminal" link makes the interaction path obvious.

## Interface contract

### Component tree change (§3.4 Chat Pane)

```
ChatBody
├── StateBanner         (unchanged)
├── MessageList
│   ├── TurnSeparator[] (NEW — "Turn N" between user→assistant cycles)
│   ├── MessageBubble[] (unchanged)
│   └── ScrollBadge     (NEW — "↓ New messages" when scrolled up)
├── EndedOverlay        (moved into ReadOnlyBar when ended)
└── ReadOnlyBar         (REPLACES ChatInput)
    ├── StatusText      (state-dependent: typing / ended / ready)
    └── SwitchLink      (→ "Switch to Terminal")
```

### URL contract

No change. `/agent?view=chat` still serves the chat pane. The `view` param
semantics are unchanged.

### Data flow change

Removed from `AgentPage`:

```
handleSend → sendMessage() → POST /session/:id/message → PTY stdin
```

The `sendMessage` function stays in `api.ts` (the endpoint is correct for
programmatic use). The chat pane no longer imports or calls it.

Added to `MessageList`:

```
onScroll → detect distance from bottom → userScrolledUp state
userScrolledUp + new messages arrive → show ScrollBadge
```

## Consequences

### Positive

- **One interaction surface.** Terminal writes to PTY; chat reads from
  transcript. No race, no coordination, no silent failures.
- **Better reading experience.** Smart scroll + turn separators make reviewing
  long conversations practical — something the raw terminal does poorly.
- **Clear intent.** Removing the input signals "this is a transcript viewer"
  more clearly than disabling it.
- **Zero API changes.** Phase 1 is entirely frontend — low risk, fast to ship.

### Negative / cost

- **Chat can't send.** User must switch to terminal to interact. One extra
  click — acceptable trade-off (terminal is always one tab/click away via the
  Topbar segmented control).
- **`sendMessage` / `POST /session/<id>/message` become dead code in the chat
  path.** The endpoint stays (it has valid programmatic use), but the client
  function loses its only caller. Removed in Phase 2 cleanup.
- **Phase 2 (rich transcript) deferred.** The "flattened text" problem
  (`[tool: Read]` instead of structured tool display) is not solved yet.
  Phase 1 improves the reading *container*; Phase 2 improves the reading
  *content*.

### Follow-up

- **ADR for Phase 2** — rich transcript API contract (`?format=rich`) before
  backend changes.
- **Remove `sendMessage` from `api.ts`** when no callers remain (Phase 2
  cleanup — the endpoint itself stays).

## References

- ADR-0003: [`0003-agent-page-view-param.md`](0003-agent-page-view-param.md) — `/agent?view=chat` route
- ADR-0004: [`0004-single-agent-shell-in-tab-view-switch.md`](0004-single-agent-shell-in-tab-view-switch.md) — in-tab view switching, single runtime
- HLD: [`../design/react-architecture.md`](../design/react-architecture.md) §3.4 Chat Pane, §4.3 data flow
- Owner decision: 2026-07-18 session — chat sending unreliable → read-only transcript viewer
