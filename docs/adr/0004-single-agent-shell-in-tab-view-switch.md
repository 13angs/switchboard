# ADR-0004: Single Agent Shell with In-Tab View Switching

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-07-18 |
| **Deciders** | Don (owner) |
| **Supersedes** | ADR-0003 serving detail: `/agent?view=chat` dispatching to `chat.html` |

## Context

ADR-0003 moved the public route contract to `/agent?view=terminal|chat`, but
kept two Vite entries behind that contract: `agent.html` for terminal and
`chat.html` for chat. That made the URL model correct, but switching views still
meant loading a separate page/runtime.

Don clarified the desired UX: chat should be a view inside the agent page, like
terminal and files. Switching views must not kill or detach the session; the
views should share one session.

## Decision

Use one browser shell for an agent session:

- `/agent?session_id=<id>&view=terminal|chat` always serves `agent.html`.
- `agent.html` mounts one React `AgentPage` that owns `session_id`, session
  metadata, drawers, transcript polling, and the terminal WebSocket lifecycle.
- `view` is client-side presentation state. The topbar switches between
  `Terminal` and `Chat` in the same tab and updates the query string.
- Once the terminal pane has mounted, it stays mounted while the chat pane is
  active. This keeps the WebSocket subscriber attached during terminal → chat
  switches; only explicit close/detach or kill affects the PTY.
- `/chat` remains a compatibility redirect to `/agent?view=chat&<qs>`, but
  there is no `chat.html` build entry.

The existing server PTY model remains unchanged: browser close detaches,
explicit kill terminates, and reconnect attaches/resumes.

## Consequences

Positive:

- The route, product model, and runtime model now match: one agent session, one
  page shell, multiple views.
- Terminal → chat switching no longer closes the WebSocket or detaches from the
  PTY.
- `chat.html` and `src/chat.tsx` disappear from the build surface.

Costs:

- `AgentPage` owns more state than the old page-specific wrappers.
- The terminal bundle is present for the agent shell even when first opened in
  chat view. The trade-off is acceptable because xterm is only initialized after
  the terminal view is opened.

## References

- ADR-0003: [`0003-agent-page-view-param.md`](0003-agent-page-view-param.md)
- HLD: [`../design/react-architecture.md`](../design/react-architecture.md) §2, §3, §5
