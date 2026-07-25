# ADR-0001: React + TypeScript + Vite for orchestrator UI

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-07-14 |
| **Deciders** | Don (owner) |
| **Replaces** | — (first UI architecture ADR) |

## Context

The orchestrator UI currently uses vanilla JS (1,314 lines across 5 files: `board.html`, `terminal.html` + `terminal.js`, `chat.html` + `chat.js`). Design prototypes for all 3 pages are signed off (Preact+htm prototypes in `docs/design/orchestrator-*`).

The orchestrator will be open-sourced. Vanilla JS at this scale is manageable but creates friction as features grow: no component reuse (both terminal and chat pages implement identical drawers, topbar, and status bar), design tokens duplicated across files, and no type safety for the API contract.

## Decision

**Migrate the implementation (`static/`) to React 19 + TypeScript + Vite.**

- **React 19** — component model, hooks, ecosystem (React Router, xterm.js React wrappers)
- **TypeScript** — type-safe API surface; reduces contributor ramp-up (types are documentation)
- **Vite** — build tool; fast dev server with HMR, zero-config TypeScript support
- **Python server** — unchanged (stdlib-only); serves `dist/` instead of `static/` in production

## Options considered

### Option A: React 19 + TypeScript + Vite (chosen)

| Pro | Con |
|---|---|
| Largest contributor pool (open-source default) | ~40KB gzip bundle (React + ReactDOM) |
| TypeScript first-class → self-documenting API surface | Requires build step (Vite) |
| Ecosystem: React Router, TanStack Query, xterm.js React wrappers | `node_modules` in the repo |
| Shared components eliminate 3× duplication | — |
| Vite HMR → faster dev iteration | — |

### Option B: Preact + TypeScript + Vite

| Pro | Con |
|---|---|
| ~4KB gzip (10× smaller than React) | Smaller ecosystem — fewer React wrappers for xterm.js, marked |
| API surface near-identical to React | Contributors less familiar with Preact nuances |
| Same build pipeline | Preact compat layer needed for some React libs |

Rejected: bundle size difference (~36KB) is negligible for a dev tool (not a customer-facing web app served at scale). React's ecosystem advantage and contributor familiarity outweigh the size saving for an open-source project.

### Option C: Stay vanilla JS, add module organization

| Pro | Con |
|---|---|
| No build step, no `node_modules` | No component reuse — continue 3× code duplication |
| Zero migration cost | No type safety — API contract changes break silently |
| Current implementation is working | Every new feature adds more ad-hoc state management |

Rejected: doesn't address the core motivation — flexibility at scale and open-source contributor experience. The build step is explicitly accepted.

## Consequences

### Positive

- Component sharing: terminal + chat share drawers, topbar, status bar (currently duplicated)
- Design tokens live in one `tokens.css` (currently copy-pasted across 3 files)
- TypeScript types for API responses → editors autocomplete, refactoring is safe
- Hot Module Replacement during development
- Standard Vite toolchain → any React developer can contribute immediately

### Negative / cost

- Build step required before running (`npm run build` → `dist/`)
- `node_modules/` adds ~200MB on disk (gitignored)
- Migration is ~1,300 lines to rewrite (phased, not big-bang — see HLD §Migration)
- Python server needs 2 small changes: (1) serve from `dist/` via env var, (2) add `POST /session/start` endpoint to decouple spawn from `GET /chat?provider=xxx` (the GET-with-side-effect pattern breaks when the SPA serves `chat.html` for all `/chat` requests)

### Neutral / watch

- xterm.js + marked + highlight.js move from CDN to npm — better version pinning, but adds to bundle
- If the orchestrator is embedded in a resource-constrained environment, React bundle size could matter — not the case today

## References

- HLD: [`react-architecture.md`](../design/react-architecture.md) — component tree, data flow, route design, migration phases
- Design prototypes (sign-off target): `docs/design/orchestrator-board/` · `orchestrator-terminal/` · `orchestrator-chat/`
- Current implementation: `static/board.html` · `static/terminal.html` + `terminal.js` · `static/chat.html` + `chat.js`
- Server: `server.py` (Python stdlib, zero deps)
