# ADR-0007: Chat Transcript Session Insights + Actions (Phase 3)

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-07-18 |
| **Deciders** | Don (owner) |
| **Supersedes** | — (new; extends ADR-0005 + ADR-0006) |

## Context

Phase 1 (ADR-0005, #501) made the chat view a read-only transcript viewer.
Phase 2 (ADR-0006, #502) made its content rich — structured blocks, model +
token metadata. The viewer now *reads* well but offers:

- **No session-level insight** — running cost (`total_cost_usd`, already
  polled via `/state` and present on `SessionCard`, `types.ts:11`) is only
  visible in the right drawer.
- **No actions on content** — a transcript you can read but not copy or
  export is a dead end; getting a conversation out of the app means manual
  selection over styled blocks.

The owner's Phase 3 plan (2026-07-18 session; absorbed into this ADR) listed
six candidate items. Four are accepted below; two are rejected/deferred (see
Alternatives).

Code tracing during design review found one plan↔code gap: the plan's item
"always-visible scroll badge" describes behavior that **already exists** —
the badge renders whenever `userScrolledUp` (`MessageList.tsx:115`); the
`prevCount > 0` condition the plan targets sits in a no-op effect
(`MessageList.tsx:41-47`, empty body). The item reduces to dead-code removal
plus honest labeling.

## Decision

Four additive, frontend-only changes. No backend, no API, no route changes.

### 1. Cost display in `ReadOnlyBar`

Show the session's running cost right-aligned in the `ReadOnlyBar`:

```
📖 Read-only transcript · Switch to Terminal to interact          $2.47
```

- Source: `displaySession.total_cost_usd` — already in `AgentPage` state from
  the `/state` poll. New `costUsd` prop on `ReadOnlyBar`.
- `total_cost_usd` is `number | null` — **hidden when `null`** (no `$0.00`
  fabrication for harnesses/sessions that report no cost).

### 2. Copy message button — full markdown

Hover-reveal copy button per `MessageBubble`. Copies the **entire message
serialized to markdown — all blocks** (owner decision 2026-07-18: full
markdown over text-only):

| Block | Markdown |
|---|---|
| `text` | verbatim |
| `thinking` | `> 💭 …` blockquote |
| `tool_use` | `` 🔧 `name(input…)` `` inline code line |
| `tool_result` | `<details><summary>📋 N lines</summary>…</details>` |

- Serializer is **shared with export (#3)** — one source of truth in
  `lib/export-markdown.ts` (`messageToMarkdown`).
- `navigator.clipboard` requires a secure context. The server binds
  `127.0.0.1` (`server.py:791`) so the normal origin is secure; behind an
  http tunnel/LAN origin the API is `undefined` → **button is hidden when
  `navigator.clipboard` is unavailable** (no broken affordance, no
  deprecated `execCommand` fallback).
- Feedback: icon/tooltip "Copied!", auto-hide ~1.5 s.

### 3. Export transcript as `.md`

Export button in `ReadOnlyBar` → client-side download
`transcript-<session-id>.md` via `Blob` + `<a download>`. No server
round-trip — messages are already in client state (`fetchRichTranscript`).

Format: header (title · harness/provider · model · total cost), then messages
grouped under `## Turn N` headings using the **same turn-grouping logic as
`MessageList`** — extracted to `lib/turns.ts` (currently inlined at
`MessageList.tsx:50-66`) so viewer and export can never disagree on turn
numbering.

### 4. Scroll badge — dead code removal + honest label

- Delete the no-op effect (`MessageList.tsx:41-47`).
- Label reflects state: **"↓ New messages"** when messages arrived while
  scrolled up, else **"↓ Jump to latest"**. Render condition stays
  `userScrolledUp` (unchanged — already correct).

### New modules

| Module | Exports | Used by |
|---|---|---|
| `src/lib/export-markdown.ts` | `messageToMarkdown(msg)`, `messagesToMarkdown(msgs, meta)` | Copy (#2), Export (#3) |
| `src/lib/turns.ts` | turn-grouping (extracted from `MessageList`) | `MessageList`, Export (#3) |

## Alternatives considered

### Copy text-blocks-only

Copy only `text` blocks (what the assistant "said"), skip thinking/tool
blocks. Rejected by owner (2026-07-18) — full markdown keeps copy consistent
with export and captures what the assistant *did*; one serializer serves
both.

### Cost-per-turn from token counts

Derive per-turn cost from `usage` × a model pricing table. Rejected — a
pricing table is a maintenance liability out of scope for this tool;
per-message token counts already ship (Phase 2); the per-session total (#1)
covers the actual question ("what is this session costing me").

### Session info header above the message list

Compact card (title, provider, model, turns, cost, duration) above
`MessageList`. **Deferred** — the right drawer already carries this;
reconsider only if the drawer proves too far away after #1–#4 ship. Deferred
stages emit no handoff (per `sop-pipeline-handoff.md`).

### Server-side export endpoint

`GET /session/<id>/export.md`. Rejected — the client already holds the rich
transcript; a server endpoint duplicates the serializer server-side and adds
API surface for zero gain. Client-side `Blob` is ~10 lines.

## Implementation

Order (by verified effort): **1 → 4 → 2 → 3**.

| # | Item | Effort | Files |
|---|---|---|---|
| 1 | Cost in ReadOnlyBar | S | `ReadOnlyBar.tsx` (+`costUsd` prop), `Agent.tsx`, `Chat.css` (`.ro-cost`) |
| 4 | Badge cleanup + label | XS | `MessageList.tsx` |
| 2 | Copy button | S | `MessageBubble.tsx`, `lib/export-markdown.ts` (new), `Chat.css` (`.bubble-copy-btn`) |
| 3 | Export .md | M | `lib/export-markdown.ts`, `lib/turns.ts` (new), `ReadOnlyBar.tsx`, `Agent.tsx` |

Gate: `npm run build` (includes `tsc --noEmit`) + existing pytest suite green
+ manual verify in the running app. No frontend test runner exists in this
repo; none is added this phase (revisit if `lib/` pure functions accumulate).

The dev PR also executes **ADR-0005's deferred cleanup**: remove the client
`sendMessage()` (`api.ts:37`, caller-less since Phase 1; the
`POST /session/<id>/message` endpoint stays — valid programmatic surface).

One task branch / PR, same as Phases 1–2 (#501, #502). The dev PR bumps the
HLD (drops the ⏳ planned markers) per the per-merge discipline in
`sop-software-design.md § HLD is a living document`.

## Consequences

- **Positive:** cost visible where the user already looks; transcript becomes
  portable (copy one message, export the whole session) — the viewer is
  useful *outside* the app; scroll affordance stops implying new messages
  that don't exist.
- **Negative:** the markdown serializer must track future block types — same
  defensive posture as ADR-0006's `_content_blocks()`: unknown types render
  as a fenced JSON stub rather than being dropped. `ReadOnlyBar` accretes
  children (status · link · cost · export) — still one row; split it if it
  grows again.
- **Risk:** clipboard API absent on non-secure origins → guarded by hiding
  the button; export uses `Blob`/`download` which has no secure-context
  requirement.

## Pipeline

- **ux-ui stage skipped** (conditional per `sop-pipeline-handoff.md`): the
  visual spec was authored by the owner in the Phase 3 plan (exact
  placement + rendering per item) — owner routed design → dev directly
  (2026-07-18).
- **Handoff:** `handoff:dev` label on this PR. Baton = this ADR + HLD
  §3.4 / §4.5.

## References

- ADR-0005: [`0005-chat-read-only-transcript-viewer.md`](0005-chat-read-only-transcript-viewer.md) — read-only viewer (Phase 1)
- ADR-0006: [`0006-rich-transcript-api.md`](0006-rich-transcript-api.md) — rich blocks (Phase 2); defensive block handling this ADR mirrors
- HLD: [`../design/react-architecture.md`](../design/react-architecture.md) §3.4 Chat Pane, §4.5 Transcript serialization
- Owner Phase 3 plan: 2026-07-18 session (absorbed into this ADR; no separate plan file)
