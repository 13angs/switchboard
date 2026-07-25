---
title: "ADR-0018: Rate-Limit Self-Healing — dual-path detection + auto-pause (P1#5)"
type: adr
created: 2026-07-24
status: accepted
project: switchboard
implements: "plans/p0-p1-gaps-from-comparable-systems-research.md (forge, 2026-07-23) — Branch E"
related: "docs/design/hld-workspace-native-orchestrator-v2.md § v2.6 delta"
teams: [software-design]
---

# ADR-0018: Rate-Limit Self-Healing

## Context

Forge plan Branch E (4 decisions settled: E1–E4) requires automatic rate-limit
detection and self-healing: detect when a session hits a provider rate limit,
block further input, display a card overlay with countdown, and auto-resume
after cooldown. Currently an operator must manually notice and intervene.

**Constraints:**
- Python 3 stdlib only
- Dual-path detection: PTY scrollback (primary) + jsonl error pattern (secondary)
- Block input only — no SIGSTOP (E2: PTY continues running; internal Claude
  processes may timeout if SIGSTOP'd)
- Auto-resume after 60s default cooldown + manual "▶ Resume now" override
- Parse `retry-after` header if present in error message

## Decision SD1 — Detection: extend `_observe_terminal_output` hook

### Context

`notifications.py` already watches PTY stdout for lifecycle events via the
`_observe_terminal_output(term, data)` hook in `server.py:109-147`. Rate-limit
detection follows the same pattern — scan PTY output for rate-limit signatures.

### Detection patterns

```python
# control_plane/rate_limiter.py

RATE_LIMIT_PATTERNS: list[re.Pattern] = [
    re.compile(rb"Rate limit exceeded", re.IGNORECASE),
    re.compile(rb"rate.limit", re.IGNORECASE),
    re.compile(rb"Too many requests", re.IGNORECASE),
    re.compile(rb"429", re.IGNORECASE),  # HTTP 429
    re.compile(rb"retry.after", re.IGNORECASE),
    re.compile(rb"quota exceeded", re.IGNORECASE),
    re.compile(rb"API rate limit", re.IGNORECASE),
    re.compile(rb"Try again in.{0,20}\d+.{0,5}(second|minute|hour)", re.IGNORECASE),
]

def detect_rate_limit(data: bytes) -> bool:
    """True when PTY output contains a rate-limit signature."""
    return any(p.search(data) for p in RATE_LIMIT_PATTERNS)
```

### Integration point

In `server.py`'s `_observe_terminal_output()` (line 109), add after the existing
notification check:

```python
def _observe_terminal_output(term: terminal.PtyTerminal, data: bytes) -> None:
    # ...existing notification logic...

    # Rate-limit detection (ADR-0018)
    if rate_limiter.detect_rate_limit(data):
        rate_limiter.pause_session(term.session_id)
```

### Why not regex the structured error event from jsonl?

Jsonl writes are asynchronous — a rate-limit error may appear in the jsonl seconds
after the PTY already shows the message. PTY scrollback is real-time; jsonl is
eventually-consistent. Both paths cover different latency windows.

### Rejected: parse structured response objects

The PTY output is raw ANSI text, not structured JSON. Parsing structured errors
from PTY output would require intercepting the harness's API response stream,
which is infeasible without modifying the harness itself.

## Decision SD2 — Pause mechanism: `RateLimitGuard` in server.py

### State model

```python
# server.py — module-level (alongside _registry, _reg_lock)

_rate_limit_until: dict[str, float] = {}  # session_id → epoch seconds
_rate_limit_lock = threading.Lock()
```

### Public interface (via `rate_limiter.py` module)

```python
# control_plane/rate_limiter.py

COOLDOWN_SECONDS = 60  # default (E3)

def pause_session(session_id: str, retry_after: int | None = None) -> float:
    """Mark session as rate-limited until now + cooldown.
    Returns the resume_at epoch time."""

def is_rate_limited(session_id: str) -> bool:
    """True when session is currently rate-limited."""

def remaining_seconds(session_id: str) -> int:
    """Seconds until auto-resume (0 when not rate-limited)."""

def resume_session(session_id: str) -> bool:
    """Manual override — clear rate-limit state immediately."""

def rate_limited_sessions() -> dict[str, float]:
    """Return {session_id: resume_at} for all currently rate-limited sessions."""
```

### Input blocking

**Chat (`_message` in server.py):**

```python
# Before stdin write — check rate-limit state:
if rate_limiter.is_rate_limited(session_id):
    remaining = rate_limiter.remaining_seconds(session_id)
    self._json(429, {
        "error": f"Rate-limited. Resuming in {remaining}s.",
        "retry_after": remaining,
        "session_id": session_id,
    })
    return
```

**Terminal (WS `_handle_ws_upgrade` write path):**

```python
# Before os.write to PTY fd:
if rate_limiter.is_rate_limited(session_id):
    # Don't write keystrokes — silently drop (per E2)
    return
```

Note: terminal keystrokes are silently dropped (not sent an error frame —
that would spam the terminal UI). The card overlay + countdown is the visual
signal; the terminal itself shows whatever the PTY outputs naturally.

### Auto-resume

A background thread checks every 5 seconds (same cadence as board poll):

```python
def _rate_limit_janitor():
    """Background thread: auto-resume expired sessions."""
    while True:
        time.sleep(5)
        now = time.time()
        with _rate_limit_lock:
            expired = [
                sid for sid, until in _rate_limit_until.items()
                if now >= until
            ]
            for sid in expired:
                del _rate_limit_until[sid]
```

The janitor is started in `server.py main()` alongside the HTTP server.

### Rejected: per-session timer threads

One thread per rate-limited session → thread count explodes if many sessions
hit rate limits simultaneously. A single janitor thread is bounded.

## Decision SD3 — jsonl fallback detection (secondary path)

### Context

E1 specifies dual-path: PTY scrollback (primary) + jsonl error pattern (secondary).
The jsonl path catches rate limits that occurred while the orchestrator wasn't
watching PTY output (e.g. a session that was detached when it hit the limit).

### Implementation

During `/state` poll (in `discovery.py` or `state.py`), check if a session's
most recent assistant message has a rate-limit signature in its `stop_reason`:

```python
# In discovery.py discover(), after building card:
if card.health and card.health.error == "unhealthy":
    # Check if the error is rate-limit-related via jsonl
    # This is a lightweight check — only for sessions already flagged unhealthy
    ratelimit = _check_jsonl_rate_limit(s.jsonl_path, s.harness)
    if ratelimit:
        rate_limiter.pause_session(card.session_id)
```

`_check_jsonl_rate_limit()` reads the last 3 assistant messages from jsonl
and checks `stop_reason` or error text for rate-limit keywords.

### Priority

PTY detection wins — it's real-time. The jsonl path is a safety net for
sessions that were rate-limited while detached.

## Decision SD4 — `/state` response: add `rate_limited_until` per session

### SessionCard addition

```python
# discovery.py SessionCard — add field:
rate_limited_until: Optional[float] = None  # epoch seconds
```

Populated from `rate_limiter.rate_limited_sessions()` during `discover()`.

### Client type

```typescript
// types.ts SessionCard — add:
rate_limited_until?: number | null;
```

## Decision SD5 — UI: card overlay + countdown

### Card overlay (`Card.tsx`)

When `card.rate_limited_until` is set and in the future:

```tsx
{card.rate_limited_until && card.rate_limited_until * 1000 > Date.now() && (
  <div className="rate-limit-overlay">
    <span className="rl-icon">⏳</span>
    <span className="rl-label">Rate-Limited</span>
    <Countdown until={card.rate_limited_until} />
    <button
      className="rl-resume-btn"
      onClick={(e) => {
        e.stopPropagation();
        resumeSession(card.session_id);
      }}
    >
      ▶ Resume now
    </button>
  </div>
)}
```

### Countdown component

```tsx
function Countdown({ until }: { until: number }) {
  const [remaining, setRemaining] = useState(Math.max(0, until - Date.now() / 1000));
  useEffect(() => {
    const t = setInterval(() => {
      setRemaining(Math.max(0, until - Date.now() / 1000));
    }, 1000);
    return () => clearInterval(t);
  }, [until]);
  if (remaining <= 0) return null;
  return <span className="rl-countdown">{Math.ceil(remaining)}s</span>;
}
```

### Manual resume button

```typescript
// api.ts — add:
async function resumeSession(sessionId: string): Promise<OkResponse> {
  const r = await fetch(`/session/${sessionId}/resume`, { method: 'POST' });
  return r.json();
}
```

New endpoint: `POST /session/<id>/resume` → calls `rate_limiter.resume_session(id)`.

### Rejected: webhook/notification

Deferred to P3 per plan scope boundary.

## Decision SD6 — Rate-limit state: in-memory only

### Context

Rate-limit state is transient (60s cooldown). Persisting it across server
restarts would be misleading — a restart likely means the operator is
actively intervening.

### Decision

`_rate_limit_until` dict is in-memory. Server restart → all rate limits cleared.
This is correct behavior — the first action after restart is an operator
decision, not an automated cooldown continuation.

## Consequences

- **Positive:** Rate-limited sessions self-heal within 60s; operator sees card
  overlay with countdown; manual resume always available; dual-path detection
  covers both live and detached sessions
- **Negative:** PTY regex matching is inherently fragile — rate-limit message
  formats vary by provider and may change; patterns need maintenance
- **Risk:** False positive detection (legitimate text matching rate-limit
  patterns) → session unnecessarily paused → operator can manually resume
- **Risk:** False negative detection (new rate-limit message format not in
  patterns) → no auto-pause → operator still sees error on card via health score

## Handoff

→ `dev` implement against this ADR + HLD v2.6 delta § Branch E
