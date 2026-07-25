---
title: "ADR-0017: Tool-Call Timeline — RightDrawer tab + /timeline endpoint (P1#4)"
type: adr
created: 2026-07-24
status: accepted
project: switchboard
implements: "plans/p0-p1-gaps-from-comparable-systems-research.md (forge, 2026-07-23) — Branch D"
related: "docs/design/hld-workspace-native-orchestrator-v2.md § v2.6 delta"
teams: [software-design]
---

# ADR-0017: Tool-Call Timeline

## Context

Forge plan Branch D (4 decisions settled: D1–D4) requires a "Timeline" tab in
the RightDrawer showing tool invocations with arguments, timing, and filter chips.
The data source is `tool_use` blocks from transcript jsonl — the same pipeline
used by `analytics.py` and `read_messages_rich()`.

`MessageBubble.tsx:128-133` already renders `tool_use`/`tool_result` blocks inline
as labels. This feature extracts them into a dedicated, filterable timeline panel
without changing the inline display.

**Constraints:**
- Python 3 stdlib only
- Reuse `FILE_TOOLS` discriminator map from `analytics.py` (extract to shared location)
- RightDrawer already has Terminal/Files view toggle — Timeline becomes a 3rd tab
- New server endpoint needed (can't reuse `/transcript` — different shape)

## Decision SD1 — New endpoint: `GET /session/<id>/timeline`

### Contract

```
GET /session/<session_id>/timeline
```

**Response:**
```json
{
  "session_id": "abc123",
  "entries": [
    {
      "tool": "Read",
      "category": "read",
      "args_summary": "CLAUDE.md",
      "args": {"file_path": "/workspaces/...", "...": "..."},
      "ts": "2026-07-24T10:00:00+07:00",
      "duration_ms": 234,
      "result_summary": "1	# claudeMd...",
      "result_ts": "2026-07-24T10:00:00.234+07:00"
    }
  ]
}
```

| Field | Source | Note |
|---|---|---|
| `tool` | `tool_use.name` | e.g. Read, Write, Edit, Bash |
| `category` | FILE_TOOLS[tool] | 'read' / 'write' / 'edit' — for filter chips |
| `args_summary` | Derived from `tool_use.input` | ≤1 line; first meaningful value (file_path, command, etc.) |
| `args` | `tool_use.input` | Full args dict for expand detail |
| `ts` | Message timestamp | When tool_use was sent |
| `duration_ms` | Computed: `tool_result.ts - tool_use.ts` | null when tool_result missing (tool still running or session interrupted) |
| `result_summary` | `tool_result.content` | Truncated to 200 chars; null when no result |
| `result_ts` | `tool_result` timestamp | When result arrived |

### Implementation

Server-side in `server.py` — new `_timeline()` method, following the `_transcript()` pattern:

```python
def _timeline(self, session_id: str, repo_root: str):
    harness_name, jsonl_path = _transcript_source(session_id, repo_root)
    if harness_name is None or jsonl_path is None:
        self._json(404, {"error": f"session '{session_id}' not found"})
        return
    store = _store_for(harness_name)
    entries = _build_timeline(store, jsonl_path)
    self._json(200, {"session_id": session_id, "entries": entries})
```

`_build_timeline()` lives in `server.py` as a module-level function (not a new
module — it's ~50 lines of parsing logic, below the threshold for a separate file):

```python
def _build_timeline(store_module, jsonl_path: str) -> list[dict]:
    """Extract tool_use→tool_result pairs from transcript jsonl."""
    messages = store_module.read_messages_rich(Path(jsonl_path))
    entries = []
    pending: dict[str, dict] = {}  # tool_use_id → partial entry

    for msg in messages:
        for block in msg.get("content", []):
            if block.get("type") == "tool_use":
                entry = {
                    "tool": block.get("name", "?"),
                    "category": _tool_category(block.get("name", "")),
                    "args_summary": _args_summary(block.get("input", {})),
                    "args": block.get("input", {}),
                    "ts": msg.get("ts"),
                    "duration_ms": None,
                    "result_summary": None,
                    "result_ts": None,
                }
                pending[block.get("id", "")] = entry
                entries.append(entry)
            elif block.get("type") == "tool_result":
                tid = block.get("tool_use_id", "")
                if tid in pending:
                    e = pending[tid]
                    e["result_ts"] = msg.get("ts")
                    if e["ts"] and e["result_ts"]:
                        # Compute duration
                        t1 = _parse_ts(e["ts"])
                        t2 = _parse_ts(e["result_ts"])
                        if t1 and t2:
                            e["duration_ms"] = int((t2 - t1).total_seconds() * 1000)
                    result_content = block.get("content", "")
                    if isinstance(result_content, str) and result_content:
                        e["result_summary"] = result_content[:200]
                    elif isinstance(result_content, list):
                        # content might be a list of text blocks
                        text = " ".join(
                            b.get("text", "") for b in result_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                        e["result_summary"] = text[:200] if text else None

    # Sort by timestamp ascending (chronological order)
    entries.sort(key=lambda e: e.get("ts") or "")
    return entries
```

### Decision: inline, not new module

`_build_timeline()` is ~50 lines of pure data transformation. A new module
for this would add ceremony (module docstring, exports, test file) with no
isolation benefit. The function has no side effects and no configuration
dependencies — it takes a store module + path, returns a list.

### Rejected: separate `timeline.py` module

Over-engineered for 50 lines of pure transformation. Revisit if timeline
grows logic beyond extraction+pairing.

## Decision SD2 — Shared FILE_TOOLS: extract to `analytics.py` export

### Context

FILE_TOOLS is defined in `analytics.py:23-30`. The timeline endpoint needs the
same map. Duplicating it in `server.py` would create a 3rd copy (client
`file-refs.ts` + server `analytics.py` + server `server.py`).

### Decision

`server.py` imports `_tool_category()` from `analytics.py`:

```python
from .analytics import FILE_TOOLS  # existing; make it public

def _tool_category(tool_name: str) -> str:
    return FILE_TOOLS.get(tool_name, "other")
```

`analytics.py`'s `FILE_TOOLS` is already module-level; the import is free.

## Decision SD3 — Frontend: Timeline tab in RightDrawer

### Component structure

```
RightDrawer.tsx (amended)
├── view-toggles: [Terminal] [Files] [Timeline]  ← 3rd button
└── drawer-body
    └── TimelinePanel.tsx  ← NEW component, shown when view='timeline'
```

**`TimelinePanel` props:**
```typescript
interface TimelinePanelProps {
  sessionId: string;
}
```

**Internal state:**
```typescript
interface TimelineEntry {
  tool: string;
  category: string;  // 'read' | 'write' | 'edit' | 'other'
  args_summary: string;
  args: Record<string, unknown>;
  ts: string;
  duration_ms: number | null;
  result_summary: string | null;
  result_ts: string | null;
}
```

### Data fetching

`TimelinePanel` fetches on mount + session change:
```typescript
const [entries, setEntries] = useState<TimelineEntry[]>([]);
useEffect(() => {
  fetch(`/session/${sessionId}/timeline`)
    .then(r => r.json())
    .then(d => setEntries(d.entries || []));
}, [sessionId]);
```

No polling — timeline is static for a given session transcript (new turns
don't appear in a read-only view; user refreshes by switching tab).

### Filter chips

```tsx
const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'read', label: 'Read' },
  { key: 'write', label: 'Write' },
  { key: 'edit', label: 'Edit' },
  { key: 'bash', label: 'Bash' },
];

// 'Bash' maps to FILE_TOOLS['Bash'] = 'edit' — but the UI should let users
// filter Bash separately. Override: tool name 'Bash'/'exec_command' → 'bash'.
```

**Filter override rule:** FILE_TOOLS maps `Bash` → `edit` (because Bash can
write files), but timeline filter should separate Bash from Edit for usability.
`_tool_category()` returns `"bash"` for Bash/exec_command tool names,
`FILE_TOOLS[name]` otherwise.

### Row rendering

Each row shows:
```
[icon] tool_name  args_summary    duration    [expand ▼]
```

- **Icon:** emoji per tool — 📖 Read, ✏️ Write, 🔧 Edit, 💻 Bash
- **Duration:** formatted ms (e.g. "234ms", "1.2s")
- **Expand:** click toggles inline detail showing full args JSON + result_summary
- **Filtered rows hidden** via CSS `display: none` (not removed from DOM — keeps
  expand state across filter changes)

### Sort

Entries arrive sorted by `ts` ascending (server-side). Client preserves order.

### Rejected: poll-based timeline

Timeline data is derived from the transcript — it doesn't change after session
end. A live-session timeline would need polling, but that's a P2 enhancement.

## Decision SD4 — RightDrawer integration: view toggle extension, not redesign

### Context

`RightDrawer.tsx` currently has a `view` prop with values `'terminal'` | `'files'`
and a `hideViewToggle` flag. The Timeline is a third view, but should only
appear when a session is selected.

### Decision

- Add `'timeline'` to the view type union
- Add the "Timeline" button to `view-toggles`
- `TimelinePanel` is rendered inside `drawer-body` when `view === 'timeline'`
- No structural changes to RightDrawer — pure extension

## Consequences

- **Positive:** Dedicated, filterable tool-call view; zero impact on existing
  chat/terminal rendering; FILE_TOOLS shared via import; RightDrawer pattern
  extended cleanly
- **Negative:** New endpoint adds ~50 lines to server.py + ~100 lines of new
  frontend code; `_build_timeline()` re-reads full jsonl (acceptable — one
  session, on-demand, not polled)
- **Risk:** Large transcripts (500+ tool calls) produce large JSON response →
  acceptable (on-demand fetch, not polled; timeline is a debug/audit tool)

## Handoff

→ `dev` implement against this ADR + HLD v2.6 delta § Branch D
