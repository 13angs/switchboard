---
title: "ADR-0016: Session Health Score — 3-signal graduated heuristic (P1#3)"
type: adr
created: 2026-07-24
status: accepted
project: switchboard
implements: "plans/p0-p1-gaps-from-comparable-systems-research.md (forge, 2026-07-23) — Branch C"
related: "docs/design/hld-workspace-native-orchestrator-v2.md § v2.6 delta"
teams: [software-design]
---

# ADR-0016: Session Health Score

## Context

Forge plan Branch C (4 decisions settled: C1–C4) requires 3 health signals
(stale / loop / error) → 3-tier graduated score (🟢🟡🔴) displayed as a dot
next to the state-badge on kanban cards with detail in RightDrawer Stats.

Health is **heuristic only** — observe, never auto-act (C4). No auto-archive/kill.

**Thresholds (from forge):**
- **Stale:** 🟡 idle > 6h, 🔴 idle > 24h
- **Loop:** 🟡 ≥5 consecutive same-tool calls, 🔴 ≥10
- **Error:** 🟡 ≥3 errors in last 10 turns, 🔴 error rate ≥ 50%

**Constraints:**
- Python 3 stdlib only
- Read from jsonl transcript (same data source as cost tracking)
- Score computed server-side; clients receive it via `/state`
- Zero new dependencies

## Decision SD1 — New module: `control_plane/health.py`

### Options

| Option | Pro | Con |
|---|---|---|
| A. New module | Isolated, testable, follows `analytics.py` pattern; discovery.py stays lean | +1 file (~80 lines) |
| B. Inline in `discovery.py` | No new file | Discovery grows; health logic mixed with card building |
| C. Inside each store's `read_session()` | Single scan pass | Couples health concern into 3 store modules; health thresholds change → 3 files to edit |

### Decision: A

`control_plane/health.py` — single public entry:

```python
@dataclass
class HealthScore:
    status: str        # 'healthy' | 'warning' | 'unhealthy'
    stale: str         # per-signal
    loop: str
    error: str
    stale_hrs: float   # hours since last_ts (for UI detail)
    loop_count: int    # max consecutive same-tool (for UI detail)
    error_count: int   # errors in last 10 turns (for UI detail)
    error_total: int   # total turns checked

def session_health(
    jsonl_path: str,
    last_ts: str | None,
    harness: str,
    now: datetime | None = None,
) -> HealthScore | None:
    """Return health score or None when jsonl is unreadable/empty."""
```

### Computation algorithm

1. **Stale** (cheapest — computed first, short-circuits message scan if unhealthy):
   ```python
   if last_ts is None:
       stale = "healthy"  # newborn session
   else:
       hrs = (now - parse_ts(last_ts)).total_seconds() / 3600
       if hrs > 24:   stale = "unhealthy"
       elif hrs > 6:  stale = "warning"
       else:          stale = "healthy"
   ```
   If stale == "unhealthy" AND turn_count > 0 → skip message scan (loop/error
   can't make it worse; worst-signal-wins and already at 🔴).

2. **Loop** + **Error** (message scan — only when stale != "unhealthy" or turn_count == 0):
   - Read last 20 messages via the appropriate store's `read_messages()`
     (non-rich, for performance — we only need role + text + stop_reason)
   - **Loop:** scan assistant messages for consecutive identical tool names in
     text prefix (e.g. lines starting with `Read`, `Bash`, `Write`).
     Count longest run of consecutive same-tool.
   - **Error:** count messages with role=assistant AND stop_reason in
     `{"error", "refusal"}` (or permission_denials non-empty) in last 10 turns.
     If ≥ 5 of ≤ 10 → 🔴. Else if ≥ 3 → 🟡.

   ```python
   # Pseudocode for loop detection:
   max_consecutive = 0
   current_tool = None
   current_run = 0
   for msg in recent_assistant_messages:
       tool = _extract_tool_name(msg)  # first word of text, or tool_use block name
       if tool == current_tool:
           current_run += 1
       else:
           current_tool = tool
           current_run = 1
       max_consecutive = max(max_consecutive, current_run)
   ```

3. **Overall status** = worst of (stale, loop, error) — "unhealthy" beats "warning" beats "healthy".

### Why message scan goes through store reader

`session_health()` calls the store's `read_messages(jsonl_path, limit=20)`. This reuses
the existing message parser (including harness-specific quirks) rather than
re-implementing jsonl parsing. The function accepts `harness: str` to dispatch
to the correct store reader.

The re-read cost is acceptable: health is computed once per `/state` poll
(every 5s), and 20-message reads are sub-millisecond per file. For 30 sessions,
that's ~30ms total — within the poll budget.

### Rejected: B, C

## Decision SD2 — Integration: add `health` field to SessionCard + `/state` response

### Data model additions

**`discovery.py` `SessionCard`** — add field:
```python
health: Optional[dict] = None
# Shape when present:
# {status, stale, loop, error, stale_hrs, loop_count, error_count, error_total}
```

**`discovery.py` `discover()`** — after building each card:
```python
card.health = health.session_health(
    jsonl_path=str(s.jsonl_path) if s.jsonl_path else "",
    last_ts=s.last_ts,
    harness=s.harness or "claude",
    now=now,
)
```

Error handling: if `session_health()` returns `None` (unreadable jsonl),
`card.health` stays `None` → client renders no dot.

**`src/lib/types.ts`** — add interface:
```typescript
export interface HealthScore {
  status: 'healthy' | 'warning' | 'unhealthy';
  stale: 'healthy' | 'warning' | 'unhealthy';
  loop: 'healthy' | 'warning' | 'unhealthy';
  error: 'healthy' | 'warning' | 'unhealthy';
  stale_hrs: number;
  loop_count: number;
  error_count: number;
  error_total: number;
}
```

**`SessionCard`** type — add `health?: HealthScore | null`.

### Rejected: separate endpoint

A `GET /session/<id>/health` endpoint would mean N+1 requests from the board
(N cards × 1 health poll each) → unnecessary server load. Baking health into
`/state` is one response, one poll cycle.

## Decision SD3 — UI: health dot on card + detail in RightDrawer

### Card badge (`Card.tsx`)

Health dot renders inline — between the title and the state-badge:

```tsx
{card.health && card.health.status !== 'healthy' && (
  <span
    className={`health-dot h-${card.health.status}`}
    title={`Health: ${card.health.status}\nStale: ${card.health.stale} (${card.health.stale_hrs.toFixed(1)}h)\nLoop: ${card.health.loop} (max ${card.health.loop_count})\nError: ${card.health.error} (${card.health.error_count}/${card.health.error_total})`}
  />
)}
```

- 🟢 healthy → no dot (clean default; avoid visual noise)
- 🟡 warning → yellow dot
- 🔴 unhealthy → red dot

### RightDrawer detail (`RightDrawer.tsx`)

Add a "Health" subsection inside the Stats detail-group when `session.health` is present:

```tsx
{health && (
  <div className="health-detail">
    <div className="dg-row">
      <span className="k">Health</span>
      <span className={`v health-${health.status}`}>{health.status}</span>
    </div>
    <div className="dg-row">
      <span className="k">Stale</span>
      <span className="v">{health.stale_hrs.toFixed(1)}h</span>
    </div>
    <div className="dg-row">
      <span className="k">Loop</span>
      <span className="v">max {health.loop_count} consecutive</span>
    </div>
    <div className="dg-row">
      <span className="k">Errors</span>
      <span className="v">{health.error_count}/{health.error_total} turns</span>
    </div>
  </div>
)}
```

## Decision SD4 — Loop detection: harness-agnostic text heuristic

### Context

Loop detection needs to identify "same tool called consecutively." The robust
approach would parse `tool_use` blocks from rich messages, but health computation
uses the non-rich `read_messages()` for performance.

### Decision

Extract the tool name from the first line of each assistant message text:

```python
def _tool_from_text(text: str) -> str | None:
    """Heuristic: first word-like token that matches known tool names."""
    first_word = text.strip().split()[0] if text.strip() else None
    if first_word in KNOWN_TOOLS:
        return first_word
    return None

KNOWN_TOOLS = frozenset({
    "Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch",
    "WebSearch", "Task", "Agent", "NotebookEdit",
})
```

This is a heuristic — it catches the common case (tool name appears as first
word in the text render) but misses edge cases (multi-line tool output before
tool name). **Acceptable** — health is a heuristic (C4), not a control signal.
False negatives (missed loops) → health under-reports; false positives
(wrongly flagged loops) → health over-reports. Both are safe because no
auto-action is taken (C4).

### Rejected: parse rich content blocks

Too expensive for a poll-time computation on all sessions; would require
`read_messages_rich()` which parses full content blocks.

## Consequences

- **Positive:** Health scores visible at a glance on the board; detail in
  RightDrawer; zero new endpoints; reuses existing data pipeline
- **Negative:** Message re-read per session per poll (O(sessions × 20 messages)
  → ~30ms for 30 sessions) — acceptable; can add mtime-based cache later
- **Risk:** Loop detection text heuristic may produce false positives on
  legitimate repeated tool calls → acceptable (heuristic, not control)

## Handoff

→ `dev` implement against this ADR + HLD v2.6 delta § Branch C
