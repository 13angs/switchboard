# ADR-0011: Agy Transcript Reader — SQLite Protobuf Extraction

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-07-19 |
| **Deciders** | Don (owner) |
| **Supersedes** | — (new; extends ADR-0005 Phase 3, unblocks PR #512 stubs) |

## Context

PR #512 fixed `view=chat` and `view=files` for codex harness and added the agy
dispatch branch in `server.py:_transcript`. The dispatch now calls
`agy_store.read_messages[_rich]`, but both are **stubs returning `[]`**.

Agy (Antigravity CLI / Gemini) stores conversations fundamentally differently
from Claude and Codex:

| Store | Format | Reader |
|---|---|---|
| Claude | JSONL (`~/.claude/projects/<enc>/<sid>.jsonl`) | `claude_store.py` ✅ |
| Codex | JSONL (`$CODEX_HOME/sessions/**/*.jsonl`) | `codex_store.py` ✅ |
| **Agy** | **SQLite + protobuf** (`~/.gemini/antigravity-cli/conversations/<uuid>.db`) | `agy_store.py` ❌ (stub) |

The SQLite DB has a `steps` table with protobuf-encoded BLOB columns. Without
a `.proto` schema file (agy ships none), we must reverse-engineer or extract
via heuristics.

### What we know (from live DBs)

**Schema:**
```sql
CREATE TABLE steps (
  idx INTEGER,
  step_type INTEGER,     -- discriminates role/tool
  status INTEGER,
  has_subtrajectory numeric,
  metadata BLOB,          -- protobuf: tool name + args (JSON embedded)
  error_details BLOB,     -- protobuf: error messages
  permissions BLOB,       -- protobuf: tool + path
  task_details BLOB,
  render_info BLOB,
  step_payload BLOB,      -- protobuf: full message/tool payload
  step_format INTEGER
);
```

**Step type → role mapping (reverse-engineered from `4c445d9c` session, 152 steps):**

| step_type | Count | Role | Notes |
|---|---|---|---|
| 14 | 2 | `user` | User message; text embedded in `step_payload` as protobuf field |
| 15 | 73 | `assistant` | Assistant response; `metadata` contains tool context (toolSummary JSON), response text in `step_payload` |
| 5 | 9 | `tool_use` | `replace_file_content` — metadata has tool name + JSON args with `filePath` |
| 7 | 4 | `tool_use` | `grep_search` — metadata has tool name + JSON args with `Query`, `Includes` |
| 8 | 44 | `tool_use` | `view_file` (Read) — metadata has JSON with `AbsolutePath`, `StartLine`, `EndLine` |
| 9 | 9 | `tool_use` | `list_dir` — metadata has JSON with `DirectoryPath` |
| 21 | 4 | `tool_use` | `run_command` (Bash) — metadata has JSON with `CommandLine` |
| 17 | 3 | `tool_result` | Tool output / error (e.g., quota errors) — text in `step_payload` |
| 23 | 2 | `system` | Session lifecycle (permission, turn boundary) |
| 98 | 2 | `system` | Session init metadata |

**Key observations:**
- The `.proto` schema is **not shipped** with agy (`find ~/.gemini -name '*.proto'` → empty)
- Protobuf BLOBs contain **embedded JSON** fragments (tool args) and **UTF-8 text** (user messages, command output) that survive `decode('utf-8', errors='replace')` with ~87–99% readability
- `metadata` column carries the tool name + JSON args for tool steps
- `step_payload` column carries the full message payload for user/assistant steps
- `error_details` + `permissions` columns carry supplementary info
- `render_info` is mostly empty in observed sessions

**Tool name mapping (agy → canonical ADR-0006):**

| Agy tool | Canonical | File paths in |
|---|---|---|
| `view_file` | `Read` | `metadata → JSON.AbsolutePath` |
| `list_dir` | `Bash(ls)` | `metadata → JSON.DirectoryPath` |
| `replace_file_content` | `Edit` | `metadata → JSON.filePath` |
| `grep_search` | `Bash(grep)` | `metadata → JSON.Includes[]` |
| `run_command` | `Bash` | `metadata → JSON.CommandLine` |

## Decision

Implement `read_messages` and `read_messages_rich` in `agy_store.py` using
**regex-based text extraction** from protobuf BLOBs — no `.proto` dependency.

### Approach: UTF-8 decode + regex extraction

Protobuf encodes string fields as UTF-8 byte sequences with length-delimited
wire format. Since agy's protobuf payloads are ~87–99% readable ASCII/UTF-8
text (the non-readable bytes are protobuf wire markers), we can:

1. **Decode** each BLOB as UTF-8 (`errors='replace'`)
2. **Extract** structured data via regex patterns keyed on step_type:
   - User text from step_payload (type 14)
   - Assistant text from step_payload (type 15)
   - Tool name + JSON args from metadata (types 5,7,8,9,21)
   - Tool output text from step_payload (type 17)
3. **Build** `{role, ts, content: [{type, ...}]}` blocks per ADR-0006 contract

### Timestamps

The `steps` table has no native `timestamp` column. Options:
- **Use `idx` as a synthetic sequence** (preserved order, no wall-clock time)
- **Extract timestamps from protobuf** — the `metadata` BLOBs contain varint-encoded epoch timestamps. Without the `.proto` schema, parsing varints from unknown field positions is fragile.
- **Decision:** Use `idx` as `ts` — `f"step-{idx:04d}"`. This preserves ordering without fragile timestamp extraction. The frontend message keys already use `ts` for ordering only.

### `read_messages_rich` (structured, `?format=rich`)

For each step row, produce a canonical `RichMessage`:

```
type=14 (user)     → {role: "user", content: [{type: "text", text: ...}]}
type=15 (assistant) → {role: "assistant", content: [{type: "text", text: ...}]}
type=5,7,8,9,21    → {role: "assistant", content: [{type: "tool_use", id, name, input}]}
type=17            → {role: "assistant", content: [{type: "tool_result", tool_use_id, content}]}
type=23,98         → skipped (system/internal)
```

**Tool name normalization** (agy → canonical):
- `view_file` → `Read`
- `replace_file_content` → `Edit`
- `list_dir` → `Bash`
- `grep_search` → `Bash`
- `run_command` → `Bash`

This normalization ensures `view=files` (`file-refs.ts`) can extract file refs from agy messages.

### `read_messages` (plain text)

Concatenate text blocks into a flat `text` field per existing contract.
Tool steps produce `[tool: Read] /path/to/file` style summaries.

### Degraded fidelity (accepted)

| Field | Status |
|---|---|
| `model` | Not extractable — omit |
| `stop_reason` | Not extractable — omit |
| `usage` | Not extractable — omit |
| `thinking` blocks | Not available in agy format — skip |
| `tool_use.id` | Synthetic from `idx` (no UUID in protobuf) |
| Timestamps | Synthetic from `idx` ordering |

The frontend (`Agent.tsx`, `MessageBubble.tsx`) already handles missing
metadata gracefully (null checks on `model`, `usage`, `thinking`).

## Alternatives considered

### A. Full protobuf decode (rejected)

Would require reverse-engineering the `.proto` schema from binary data — a
multi-day effort with fragile maintenance. The agy CLI could change its schema
at any version.

### B. protobuf library with `unknown_fields` (rejected)

Python's `protobuf` library can parse without `.proto` via `UnknownFields`,
but it flattens nested messages and loses field names. The output is no better
than regex extraction for our use case.

### C. Read agy's `history.jsonl` instead (rejected)

`~/.gemini/antigravity-cli/history.jsonl` exists but contains only command-line
invocations (shell history), not conversation transcripts.

### D. Wait for agy to expose a transcript API (rejected)

No timeline. The orchestrator's design principle is reading the spine directly;
waiting for an upstream API breaks that.

## Consequences

**Positive:**
- `view=chat` renders structured transcript for agy sessions (unblocks PR #512)
- `view=files` extracts file refs for agy sessions (via tool name normalization)
- No external dependencies — pure Python stdlib (consistent with orchestrator's zero-dep policy)
- Regex extraction is version-resilient: a schema change may lose some fields but won't crash

**Negative:**
- Degraded fidelity vs Claude/Codex transcripts (no timestamps, no model/usage, no thinking blocks)
- Regex patterns need maintenance if agy changes tool names or JSON field keys
- Step-type-to-role mapping could shift across agy versions (mitigation: fall back to `[]` on unexpected types, log warning)

**Risk:** Agy schema version changes could break extraction. **Mitigation:** unit tests with snapshot DB fixtures; extraction functions isolated in `agy_store.py` per existing pattern (`claude_store.py` isolates JSONL schema volatility).

## Implementation plan

1. **`agy_store.py`**: Add `_extract_user_text(payload)`, `_extract_assistant_text(payload)`, `_extract_tool_meta(metadata)` regex helpers
2. **`agy_store.py`**: Implement `read_messages` — iterate steps, filter + extract
3. **`agy_store.py`**: Implement `read_messages_rich` — same iteration, produce structured content blocks
4. **`tests/`**: Add `test_agy_store.py` with a canned SQLite DB fixture (subset of real steps, sanitized)
5. **Verify**: Run orchestrator server against real agy sessions, confirm `view=chat` + `view=files` render correctly
