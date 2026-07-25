# ADR-0010: Discovery Cache + Poll Consolidation + Notification Fix

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-07-19 |
| **Deciders** | Don (owner), software-design team |
| **Supersedes** | — (new; does not invalidate prior ADRs) |

## Context

Four regressions in `projects/switchboard` share a common root:

1. **Performance degradation** — board becomes slower over time as sessions accumulate
2. **Session resume fails** — resume path depends on `discovery.discover()` which fails under load
3. **Page refresh/load fails** — server saturated by poll load, GIL blocks HTML serving
4. **Notifications don't work** — `HarnessLifecycleDetector` waits for `input_seen` before processing output; SSE loop starved under load

All four trace to: `discovery.discover()` rescans every JSONL file (`read_session()` reads + parses full content) on every 5s poll with zero caching. Two polls per Agent tab (board state + transcript), plus Board tab poll. With N sessions and M average file size, this is O(N×M) per poll, every 5s.

### Current poll architecture (per open tab)

```
AgentPage                Board tab
├── /state poll (5s)     └── /state poll (5s)
├── /transcript poll (5s)
└── /transcript fast-poll (1s when typing)
```

Each `/state` → `discovery.discover()` → `all_sessions_for_repo()` × 3 stores. Each `/transcript` → `_transcript_source()` → `find_card_by_session()` → `discovery.discover()` **again**.

### Notification state machine bug

`HarnessLifecycleDetector.observe_output()` (`notifications.py:193-213`) gates on `input_seen`:

```python
if not state or state.get("stopped") or not state.get("input_seen"):
    return  # drops ALL output before first user input
```

State is only initialized in `observe_input()`. On resume or fresh spawn, the harness streams output before any user input arrives → detector drops it all → no `input_ready` events ever fire until the user types something.

## Decision

### 1. Server-side discovery cache (TTL + mtime)

Add a module-level cache to `discovery.py` keyed by `repo_root`:

```
discovery._cache: dict[str, tuple[float, list[SessionCard]]]
```

| Parameter | Value | Rationale |
|---|---|---|
| TTL | 3s | Shorter than the 5s poll interval; ensures at most one recompute per poll cycle |
| Invalidation | PTY spawn → clear; PTY kill → clear | Only live-session state changes affect card columns |
| Store | Module-level dict (in-memory, per server process) | Consistent with registry design (ephemeral, no persistence needed) |

The mtime optimization: check jsonl file mtimes before re-reading. If none changed, reuse the cached `SessionSummary`. This avoids re-parsing long-running sessions whose files haven't changed.

### 2. Direct store lookup for `find_card_by_session`

Replace `find_card_by_session()` (`server.py:124-131`) which calls `discovery.discover()` → scan all, with a direct exact-match lookup:

```python
def find_card_by_session(session_id, repo_root):
    # Try each store directly — O(1) per store lookup, not O(N) scan.
    for store in (claude_store, codex_store, agy_store):
        path = store.find_session_path(session_id)
        if path:
            summary = store.read_session(path)
            return _summary_to_card(summary, repo_root)
    return None
```

`find_session_path()` already exists in each store and does a direct filename match. This eliminates the redundant full scan on every transcript poll.

### 3. Frontend poll consolidation

Merge the two independent poll timers in `AgentPage` into one:

```
AgentPage
├── 5s poll → fetchBoardState() + fetchRichTranscript() in parallel
│             (state feeds session list; transcript delta since last_ts)
└── 1s fast-poll → fetchRichTranscript() only (when typing)
```

- `fetchBoardState()` call is **shared** — both Agent page and board state update from the same response
- Transcript poll uses `since=last_ts` (already supported) — only fetches new messages
- Remove the separate `setInterval(poll, 5000)` for board state (line 283 of Agent.tsx)

### 4. Fix HarnessLifecycleDetector state machine

Change `observe_output()` to **initialize state on first output** when no state exists, rather than requiring `input_seen`:

```python
def observe_output(self, term, data):
    if not data:
        return
    key = id(term)
    with self._lock:
        state = self._states.get(key)
        if not state or state.get("stopped"):
            # Initialize on first output — don't require prior input.
            # Covers: fresh spawn (welcome text), resume (replay/status),
            # and re-attach after detach (harness may be mid-stream).
            self._states[key] = {
                "turn_no": 0,
                "input_seen": False,
                "output_seen": True,
                "approval_seen": False,
                "emitted_ready": False,
                "stopped": False,
                "timer": None,
                "last_output": time.time(),
            }
            state = self._states[key]
        # ... rest unchanged
```

## Options considered

### Option A: Full in-memory cache, no re-read (rejected)

Cache `SessionSummary` objects indefinitely; invalidate only on PTY events.

| Pro | Con |
|---|---|
| Fastest possible /state | Stale after external `claude` runs (session updates outside orchestrator) |
| Simple | Session cost/turn/title drift undetected until server restart |

Rejected. External `claude` sessions (not spawned through the orchestrator) would never update their cards.

### Option B: mtime-based incremental (chosen supplement)

Store `(session_id, jsonl_mtime, card)` tuple. On each poll, stat the jsonl; only re-read if mtime changed.

| Pro | Con |
|---|---|
| Accurate — reflects external updates | Slightly more complex than pure TTL |
| Fast for idle sessions (no re-parse) | Two-phase: stat pass + selective re-read |

Chosen as supplement to the TTL cache. Idle sessions whose jsonl hasn't changed skip the full parse.

### Option C: Push-based — WebSocket broadcast on state change (rejected for now)

Replace polling entirely; server broadcasts state deltas via WebSocket.

| Pro | Con |
|---|---|
| Zero poll overhead | Requires every state change to be instrumented |
| Real-time | External changes (not through orchestrator) still need polling |
| | Major architectural change — not a bugfix |

Rejected. HLD §4.2 already has SSE for lifecycle events. State broadcasting is a v2 redesign, not a bugfix.

## Consequences

### Positive

- **Bug 1 (performance):** /state drops from O(N×M) every 5s to O(N) mtime stat + O(K×M) selective re-reads (K = changed sessions, typically ≤2). 3s TTL ensures at most 1 recompute per poll cycle.
- **Bug 2 (resume):** `find_card_by_session` goes from discovery scan → direct store lookup. Resume no longer competes with discovery for CPU.
- **Bug 3 (page load):** Server freed from per-request full-scan → HTML serving no longer starved.
- **Bug 4 (notifications):** Detector initializes on first output → `input_ready` fires for resumed sessions and fresh spawns without requiring a user keystroke first.

### Negative / cost

- **Discovery cache adds state** — module-level dict is shared across threads; needs a lock. ~15 lines.
- **mtime stat per session** — adds one `stat()` call per jsonl per poll. Negligible vs current full-read.
- **Detector state machine change** — a session with only system output (no assistant turn) may now emit `input_ready`. Acceptable — the session *is* ready for input.

### Files touched (estimated)

| File | Change |
|---|---|
| `control_plane/discovery.py` | Add cache dict + `_discover_cached(repo_root, ttl=3.0)` |
| `server.py` | Replace `find_card_by_session` → direct store lookup; call cached discovery in `build_state` |
| `control_plane/notifications.py` | Fix `observe_output` → init state on first output |
| `src/pages/Agent.tsx` | Merge board-state poll into transcript poll timer; remove standalone board poll |
| `tests/` | Add: cache invalidation test, notification detector init-on-output test, poll consolidation regression |

## References

- HLD: [`../design/react-architecture.md`](../design/react-architecture.md) §4 Data Flow, §4.3 Hooks
- ADR-0005: chat read-only transcript viewer
- ADR-0006: rich transcript API
- `control_plane/discovery.py` — current discovery flow
- `control_plane/notifications.py` — `HarnessLifecycleDetector`
- `control_plane/claude_store.py` — `all_sessions_for_repo`, `find_session_path`, `read_session`
- `src/pages/Agent.tsx` — current poll architecture
