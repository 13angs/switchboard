---
title: "ADR-0015: Raw JSON Transcript Toggle (P0#2)"
type: adr
created: 2026-07-24
status: accepted
project: switchboard
implements: "plans/p0-p1-gaps-from-comparable-systems-research.md (forge, 2026-07-23) — Branch B"
related: "docs/design/hld-workspace-native-orchestrator-v2.md § v2.6 delta"
teams: [software-design]
---

# ADR-0015: Raw JSON Transcript Toggle

## Context

Forge plan Branch B (3 decisions settled: B1–B3) requires a toggle in the chat
view header that switches between rendered message bubbles and raw JSON transcript
in-place. No new server endpoint — the existing `GET /session/<id>/transcript?format=rich`
already returns full message structures with `content`, `model`, `stop_reason`, `usage`.

**Constraints:**
- Zero new server-side code
- Toggle is in-place (no modal, no separate page)
- Pretty-printed JSON in `<pre>` — no syntax highlighting (B3)
- Copy-to-clipboard button for debug sharing

## Decision SD1 — Toggle placement: `ReadOnlyBar` (chat header)

### Options

| Option | Pro | Con |
|---|---|---|
| A. Button in ReadOnlyBar | Minimal change; consistent with existing Export button; chat header is the natural "view mode" control | None |
| B. Separate toggle in MessageList | Closer to the content being toggled | Duplicates control pattern; ReadOnlyBar already owns view-mode chrome |

### Decision: A

Add a `[{}] JSON` toggle button to `ReadOnlyBar`'s right section (alongside Export
and cost). When active, `Agent.tsx` renders a `<pre>` block instead of `<MessageList>`.
The toggle is a two-state button (pressed = JSON view, unpressed = rendered bubbles).

### Rejected: B

## Decision SD2 — Data source: reuse existing rich transcript

### Context

The chat page already fetches `GET /session/<id>/transcript?format=rich` via
`fetchRichTranscript()`. The response includes `{session_id, messages: RichMessage[]}`
where each `RichMessage` has `role, ts, content, model?, stop_reason?, usage?`.

### Decision

No new fetch. The JSON view renders the SAME `messages` array already in React state.
Toggling does not trigger a network request — it's a pure render-mode switch.

```typescript
// Agent.tsx — conceptual diff:
const [showRawJson, setShowRawJson] = useState(false);

// In the render tree:
{showRawJson ? (
  <RawJsonView messages={messages} />
) : (
  <MessageList messages={messages} ... />
)}
```

## Decision SD3 — `RawJsonView` component: `<pre>` + copy button

### Component contract

```typescript
interface RawJsonViewProps {
  messages: RichMessage[];
  onCopy: () => void;
}
```

### Rendering

```tsx
<div className="raw-json-view">
  <button className="copy-json-btn" onClick={onCopy}>
    📋 Copy
  </button>
  <pre className="raw-json-pre">
    {JSON.stringify(messages, null, 2)}
  </pre>
</div>
```

- `JSON.stringify(data, null, 2)` — pretty-print with 2-space indent (per B3)
- No syntax highlighting — forge decision B3 explicitly rejects it
- Copy button writes to `navigator.clipboard.writeText()` with a brief
  "Copied!" feedback (CSS class toggle, ~1.5s timeout)
- `<pre>` uses `tab-size: 2`, `white-space: pre-wrap`, monospace font
- Scroll container matches existing chat scroll behavior

## Decision SD4 — Toggle state: local, not persisted

### Context

JSON view is a transient debug aid, not a persistent preference. It should reset
to "rendered" on page load / session switch.

### Decision

`showRawJson` is local React state in `Agent.tsx`. It resets to `false` when
`session_id` changes (via the existing `useEffect` that reacts to session changes).

No `localStorage`, no URL param, no server-side preference.

## Consequences

- **Positive:** Zero server changes; ~30 lines of new component code; uses
  existing data pipeline; copy-to-clipboard makes debug sharing trivial
- **Negative:** Large transcripts (200+ messages) produce enormous JSON blobs
  that may lag the browser's JSON.stringify → acceptable for a debug tool;
  Don can toggle back if it's slow
- **Risk:** `RichMessage` object may contain circular references if the API
  contract changes → mitigated by `JSON.stringify`'s built-in circular-reference
  error (it throws; catch and show error banner)

## Handoff

→ `dev` implement against this ADR + HLD v2.6 delta § Branch B
