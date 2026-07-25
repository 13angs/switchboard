# ADR-0003: Agent Page with `view` Parameter; Reserve `terminal` for Shell Terminal

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-07-18 |
| **Deciders** | Don (owner) |
| **Supersedes** | `/terminal` + `/chat` page-route naming in ADR-0002 § Interface contract and the HLD route table (`react-architecture.md` §2) |

> Follow-up: ADR-0004 keeps this public route contract, but supersedes the
> serving detail that dispatched `view=chat` to a separate `chat.html` entry.
> `/agent` now always serves one agent shell.

## Context

The orchestrator serves three pages: `/` (board), `/terminal?session_id=<id>`
(raw PTY view), and `/chat?session_id=<id>` (structured chat view). The latter
two are **two presentations of the same entity — an agent session** — yet they
are named as if they were independent pages.

Two forces motivate a rename now:

1. **Semantic drift.** The `/terminal` page is not a terminal; it is an agent
   session rendered *through* a terminal. The page identity is the session,
   the terminal is the presentation.
2. **Namespace collision ahead.** A planned board feature adds an embedded
   VS Code-style **shell terminal** (a real workspace shell, not a harness
   PTY). If `/terminal` keeps meaning "agent PTY view", the codebase ends up
   with two unrelated things both named "terminal".

A related wart surfaces during tracing: `GET /chat?provider=<name>` is a
legacy **GET with side effects** — it spawns a PTY and returns JSON. The HLD
(`react-architecture.md` §5) already flagged this and moved spawning to
`POST /session/start`; with a Vite build present, `server.py` always serves
`chat.html` for `/chat` and the spawn branch (`_chat()`) is reachable only in
the no-dist legacy fallback. No caller in `src/` uses it — `Board.tsx` calls
`POST /session/start`; `Terminal.tsx` opens `/chat?session_id=…` purely as
page query params.

## Decision

Consolidate the two session pages under one **agent page** keyed by a `view`
query parameter, and reserve the `terminal` name for the future shell-terminal
feature.

| Route | Result |
|---|---|
| `/agent?session_id=<id>&view=terminal` | terminal view (raw PTY) |
| `/agent?session_id=<id>&view=chat` | chat view (structured) |
| `/agent?session_id=<id>` (no `view`) | defaults to `view=terminal` |
| `/terminal?<qs>` | `302 → /agent?view=terminal&<qs>` (query preserved) |
| `/chat?<qs>` | `302 → /agent?view=chat&<qs>` (query preserved) |
| `/ws/agent?session_id=<id>` | WebSocket PTY (renamed from `/ws/terminal`) |

Additional decisions:

- **Serving stays thin (MPA preserved).** The server dispatches `/agent` on
  the `view` param to the existing Vite entries: `agent.html` (renamed from
  `terminal.html`) or `chat.html`. Bundles stay split; no entry merge.
- **Cut the legacy `_chat()` spawn path.** `POST /session/start` is the only
  spawn surface. `GET /chat` never spawns again, in either build mode.
- **Reserved names.** `terminal` (page/route) and `/ws/shell` (transport) are
  reserved for the future board shell-terminal feature. That feature is a new
  security surface (arbitrary workspace shell vs the per-turn permission-
  granted harness PTY, README § Permission model S8) and **requires its own
  ADR before build** — this ADR only reserves the namespace.
- **Component names do not churn.** `components/terminal/*`, `TerminalBody`,
  `Terminal.tsx` etc. name the *view*, which really is a terminal — only the
  page/route layer renames (`terminal.html` → `agent.html`, server dispatch,
  links).

## Options considered

### Option A: Rename `/terminal` → `/agent?view=terminal` only; leave `/chat` as-is

| Pro | Con |
|---|---|
| Smallest diff | Asymmetric end-state: chat stays a sibling page while terminal becomes a view of `/agent` |
| No `/chat` compat work | The "one entity, two views" model is only half-expressed |
| | Touches the same files a second time when chat is folded later |

Rejected. The two pages render the same entity; renaming only one leaves the
model half-migrated and re-touches `Board.tsx`/`server.py` in a follow-up.

### Option B: Fold both as `/agent?view=terminal|chat`, thin server dispatch (chosen)

| Pro | Con |
|---|---|
| Model matches reality: one page entity (agent session), two views | Needs redirects for existing links/bookmarks/alerted tabs |
| MPA entries + split bundles preserved; diff stays small | In-tab view switch still requires navigation |
| Frees `terminal` namespace before the shell feature lands | |
| Legacy spawn overload removed at the same boundary | |

Chosen. `GET /chat?provider=` being dead in the build path removes the only
blocker to folding chat now.

### Option C: Single `agent.html` entry; client renders the view from the param

| Pro | Con |
|---|---|
| True SPA switch: chat ↔ terminal toggles in-tab without reload | Merges two entries: xterm.js + chat UI in one bundle |
| The "Chat" button stops opening a new tab | Largest refactor of the three; entry/main/router restructure |

Rejected **for this round** — it is the natural follow-up if in-tab view
switching becomes wanted, and Option B does not preclude it (the route
contract is identical; only serving changes).

## Interface contract

### Page links (client)

```text
Board.tsx  sessionUrl() -> /agent?view=terminal|chat&session_id=<id>&harness=<h>&provider=<p>&label=<l>
Terminal.tsx handleChat() -> /agent?view=chat&session_id=<id>&harness=<h>&provider=<p>
```

`harness`/`provider`/`label` query params keep ADR-0002 semantics unchanged.

### Backward compatibility

- `/terminal` and `/chat` return `302` with the full original query string
  appended after the injected `view` param — open tabs from session-tab
  alerts (#492) and bookmarks keep working.
- `/ws/terminal` is renamed, **not** redirected: WebSocket clients ship in
  the same repo (no external consumers), and HTTP redirects on WS upgrade
  are unreliable across clients. The old path returns 404 after the rename.
- `view` values outside `terminal|chat` fall back to `terminal` (same as
  omitted).

## Implementation order

1. Rename `terminal.html` → `agent.html`; update `vite.config.ts` input map.
2. `server.py`: add `/agent` dispatch on `view`; `302` handlers for
   `/terminal` + `/chat`; rename `/ws/terminal` → `/ws/agent`; delete the
   `_chat()` spawn branch (and its no-dist fallback).
3. Update `Board.tsx` `sessionUrl()` and `Terminal.tsx` `handleChat()`;
   update the React Router path if the page reads its own location.
4. Tests: dispatch by `view` (incl. default + unknown value), redirect
   preserves query, old WS path gone, `GET /chat` never spawns.
5. **Bump the HLD in the same PR** (`react-architecture.md` §2 route table,
   §3 page sections, §5 spawn-endpoint note) — per-merge bump rule,
   `sop-software-design.md` § HLD is a living document.

## Consequences

### Positive

- Page model matches the entity model: `/agent` = one session, `view` = how
  you look at it.
- `terminal` is free for the shell-terminal feature before any collision
  exists.
- The last GET-with-side-effects route is gone; spawning has exactly one
  surface (`POST /session/start`).
- Component layer untouched — rename cost is confined to route/entry/link
  code.

### Negative / cost

- Redirect shims for `/terminal` + `/chat` live indefinitely (cheap, but
  they are contract surface).
- Renamed WS path is a hard break for anything outside this repo (none
  known).
- Docs referencing `/terminal`/`/chat` (README § Endpoints, board SOP) need
  a sweep in the implementation PR.

### Follow-up risks

- The board shell-terminal feature must not ship on namespace reservation
  alone — it needs its own ADR covering the security model (who may exec,
  which cwd, how it composes with S8 per-turn grants).
- If in-tab view switching is requested, revisit Option C; the route
  contract here is forward-compatible with it.

## References

- ADR-0002: [`0002-harness-adapter-registry.md`](0002-harness-adapter-registry.md) — `harness`/`provider` query semantics preserved
- HLD: [`react-architecture.md`](../design/react-architecture.md) §2 routes, §5 spawn endpoint migration
- README § Endpoints, § Permission model (S8) — shell-terminal security contrast
- Owner decision thread: 2026-07-18 session (rename + fold chat + cut legacy spawn confirmed)
