---
title: "ADR-0013: Analytics Files Endpoint — server-side aggregate + /analytics page"
type: adr
created: 2026-07-22
status: accepted
project: switchboard
implements: "plans/analytics-files-page.md (forge, 2026-07-22)"
related: "docs/design/hld-workspace-native-orchestrator-v2.md § v2.5 delta"
teams: [software-design]
---

# ADR-0013: Analytics Files Endpoint

## Context

Forge plan `analytics-files-page.md` (7 settled decisions) requires a server-side
endpoint that aggregates file operations from tool_use blocks across all sessions
within a time range, filtered by harness. Four design decisions are unresolved in
the plan — module organization, path normalization, frontend entry point, and
tool_use extraction strategy.

**Constraints:**
- Python 3 stdlib only (zero-deps, per HLD v2 AD1)
- Compute on-the-fly (no persistence/cache in MVP; defer D4)
- Reuse existing stores: `claude_store`, `codex_store`, `agy_store`
- Match existing frontend architecture (Vite multi-entry, no React Router)

## Decision SD1 — New module: `control_plane/analytics.py`

### Options

| Option | Pro | Con |
|---|---|---|
| A. New module | Testable in isolation; `server.py` stays navigable; reusable; follows `gh.py`/`archive.py` pattern | +1 file (~120 lines) |
| B. Inline in `server.py` | No new file | `server.py` grows from 928 → ~1100 lines; logic mixed with HTTP handler |

### Decision: A

`control_plane/analytics.py` — single public entry `files_analytics(repo_root, days, harness, now) -> dict`.
Internal helpers: session filtering by time range, tool_use extraction, path
normalization, aggregation. Follows the existing module pattern exactly.

### Rejected: B

## Decision SD2 — Path normalization: resolve against session cwd

### Options

| Option | Pro | Con |
|---|---|---|
| A. Resolve against session cwd | Accurate for all path forms (absolute, relative, `../`); `SessionSummary.cwd` available | Requires cwd per session |
| B. Heuristic: strip known prefixes | Simpler code | Breaks on relative paths, `../`, out-of-tree files; fragile across harnesses |

### Decision: A

```python
def _normalize_path(file_path: str, session_cwd: str, workspace_root: str) -> str | None:
    if os.path.isabs(file_path):
        resolved = file_path
    else:
        resolved = os.path.join(session_cwd, file_path)
    rel = os.path.relpath(resolved, workspace_root)
    if rel.startswith(".."):
        return None  # outside workspace — exclude
    return rel
```

- Absolute path → relativize directly against `WORKSPACE_ROOT`
- Relative path → join with session `cwd` → relativize
- Paths resolving outside workspace → `None` (filtered out)

### Rejected: B

## Decision SD3 — Frontend: new Vite multi-entry

### Options

| Option | Pro | Con |
|---|---|---|
| A. `analytics.html` + `src/analytics.tsx` | Follows `agent.html`/`terminal.tsx` pattern; separate bundle; zero new dependencies | +2 files |
| B. React Router in existing SPA | Single bundle; client-side routing | Adds dependency; rewrites `main.tsx` entry; changes architecture foundation |

### Decision: A

- `analytics.html` — entry HTML (mirrors `agent.html`)
- `src/analytics.tsx` — mount `<AnalyticsPage />` (mirrors `terminal.tsx`)
- `src/pages/Analytics.tsx` — page component with filter bar + summary tiles + top files table
- `vite.config.ts` — add `analytics` to `rollupOptions.input`
- Reuse: `usePoll` hook for polling, shared `Topbar` if applicable

### Rejected: B

## Decision SD4 — Tool_use extraction: direct jsonl content-block scan

### Options

| Option | Pro | Con |
|---|---|---|
| A. Direct content-block scan via store `read_messages()` | Exact match to client-side `file-refs.ts` FILE_TOOLS; no intermediate format | Duplicates extraction logic (client JS + server Python) |
| B. Add extraction to store layer | Single source of truth | Changes store interface for all callers; couples analytics concern into store |

### Decision: A

Server-side tool_use extraction in `analytics.py`:

```python
FILE_TOOLS: dict[str, str] = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "NotebookEdit": "edit",
    "exec_command": "edit",
    "Bash": "edit",
}

def _extract_file_ops(messages: list[dict]) -> list[FileOp]:
    ops = []
    for msg in messages:
        for block in msg.get("content", []):
            if block.get("type") != "tool_use":
                continue
            op = FILE_TOOLS.get(block.get("name", ""))
            if not op:
                continue
            fp = _file_path_from_input(block.get("input", {}))
            if not fp:
                continue
            ops.append(FileOp(path=fp, op=op, ts=msg.get("ts")))
    return ops
```

Duplication is acceptable — the FILE_TOOLS map is a stable discriminator
(~6 entries), and the two implementations target different runtimes (browser
vs server). A change to the tool set would touch both anyway.

### Rejected: B

## Consequences

- **Positive:** New analytics concern isolated in one module + one page; follows all
  existing architectural patterns; zero new dependencies; path normalization
  handles all harness path forms
- **Negative:** FILE_TOOLS map duplicated between `file-refs.ts` (client) and
  `analytics.py` (server) — low-risk (6 stable entries)
- **Risk:** Large session count (200+ in 30d range) may make compute-on-the-fly
  slow → deferred decision D4 (caching) re-opens if /analytics response > 2s

## Handoff

→ `ux-ui` designs the `/analytics` page look (filter bar + tiles + table) before
dev writes frontend code
→ `dev` implements against this ADR + HLD v2.5 delta
