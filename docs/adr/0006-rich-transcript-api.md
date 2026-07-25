# ADR-0006: Rich Transcript API (`?format=rich`)

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-07-18 |
| **Deciders** | Don (owner) |
| **Supersedes** | — (new; extends ADR-0005 Phase 2) |

## Context

ADR-0005 made the chat view read-only and deferred rich transcript rendering
to a follow-up. The current `/session/<id>/transcript` endpoint returns
flattened messages:

```json
{"role": "assistant", "text": "I'll check. [tool: Read] [tool_result]", "ts": "..."}
```

The JSONL transcript contains structured content blocks — thinking, tool_use,
tool_result, text — and per-message metadata (model, stop_reason, usage). The
current `read_messages()` flattens this into a single `text` field, losing:

- **Thinking blocks** — dropped entirely
- **Tool calls** — collapsed to `[tool: name]`
- **Tool results** — collapsed to `[tool_result]`
- **Model + token metadata** — dropped

## Decision

Add an optional `?format=rich` parameter to `GET /session/<id>/transcript`.
When present, return content blocks preserving the JSONL structure.

### Response shape

```json
{
  "session_id": "abc123",
  "messages": [
    {
      "role": "user",
      "ts": "2026-07-18T09:30:00Z",
      "content": [{"type": "text", "text": "fix the bug"}]
    },
    {
      "role": "assistant",
      "ts": "2026-07-18T09:30:15Z",
      "model": "deepseek-v4-pro",
      "stop_reason": "tool_use",
      "usage": {"input_tokens": 5000, "output_tokens": 200},
      "content": [
        {"type": "thinking", "thinking": "Let me look at the file first..."},
        {"type": "text", "text": "I'll check the file."},
        {"type": "tool_use", "id": "call_01", "name": "Read", "input": {"file_path": "src/app.ts"}},
        {"type": "tool_result", "tool_use_id": "call_01", "content": "import React from 'react'\n..."}
      ]
    }
  ]
}
```

### Content block types

| `type` | Fields | Notes |
|---|---|---|
| `text` | `text: string` | Plain text or markdown |
| `thinking` | `thinking: string` | Reasoning block; signature omitted (internal) |
| `tool_use` | `id: string`, `name: string`, `input: object` | Tool name + params |
| `tool_result` | `tool_use_id: string`, `content: string \| list` | Tool output; `content` may be string or list of sub-blocks |

### Backward compatibility

- `GET /session/<id>/transcript` (no `?format`) → existing flattened format (unchanged)
- `GET /session/<id>/transcript?format=rich` → new structured format
- `?since=` and `?limit=` work identically in both modes
- Unknown `?format=` values → fall back to default (flattened)

### Frontend rendering (Phase 2)

| Block | Rendering |
|---|---|
| `thinking` | `💭 Thinking…` — collapsed by default, expandable on click |
| `text` | Markdown (existing `renderMarkdown`) |
| `tool_use` | `🔧 name(key=value, …)` — compact inline badge |
| `tool_result` | `📋 N chars / N lines` — collapsed by default, expandable |

Each assistant message also shows: model name, token usage (input/output).

## Alternatives considered

### Option A: Always return rich format, let client flatten

Change the default response shape; old clients break.

Rejected. The board's transcript drawer and any programmatic consumer may rely on
the flattened `text` field. Backward compat via a query param is cheap (one
`if` branch) and avoids a breaking change.

### Option B: Return both `text` and `content` in every response

```json
{"role": "assistant", "text": "flattened...", "content": [...]}
```

Duplicates data. The `text` field is derivable from `content` — sending both
wastes bandwidth and adds a maintenance burden (they must stay in sync).

Rejected. The `text` field exists for backward compat; `content` is the richer
replacement. Pick one per request.

## Implementation

1. `claude_store.py`: add `read_messages_rich()` — same signature as
   `read_messages()`, returns content blocks + metadata
2. `codex_store.py`: add `read_messages_rich()` — same contract
3. `server.py:_transcript()`: dispatch on `?format=rich` → call `*_rich()` variant
4. Client: `fetchTranscript(sessionId, since?, format?)` — add optional third param
5. `MessageBubble`: block-based rendering for rich messages
6. HLD: update §3.4 Chat Pane (MessageBubble sub-tree) + §4.1 API Layer

## Consequences

- **Positive:** Chat view finally shows what the assistant actually did — tool
  calls with their inputs, results (collapsed), and thinking. Token usage gives
  visibility into cost per turn.
- **Negative:** Response size increases (tool_result content can be large —
  collapsed by default in the UI). Two code paths in each store adapter
  (`read_messages` + `read_messages_rich`), but they share the jsonl parse loop
  and differ only in the output shape — ~30 lines net new per adapter.
- **Risk:** JSONL schema changes on a `claude` upgrade may add new block types.
  The `_content_blocks()` helper is defensive — unknown block types are passed
  through with their raw fields rather than dropped.

## References

- ADR-0005: [`0005-chat-read-only-transcript-viewer.md`](0005-chat-read-only-transcript-viewer.md) — Phase 1 read-only chat; Phase 2 deferred here
- HLD: [`../design/react-architecture.md`](../design/react-architecture.md) §3.4, §4.1, §4.3
