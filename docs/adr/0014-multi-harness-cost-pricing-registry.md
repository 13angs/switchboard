---
title: "ADR-0014: Multi-Harness Cost Extraction + Pricing Registry (P0#1)"
type: adr
created: 2026-07-24
status: accepted
project: switchboard
implements: "plans/p0-p1-gaps-from-comparable-systems-research.md (forge, 2026-07-23) — Branch A"
related: "docs/design/hld-workspace-native-orchestrator-v2.md § v2.6 delta"
teams: [software-design]
---

# ADR-0014: Multi-Harness Cost Extraction + Pricing Registry

## Context

Forge plan Branch A (4 decisions settled: A1–A4) identifies that `codex_store.py:187`
and `agy_store.py:195` hardcode `total_cost_usd=None` while `claude_store.py:351`
correctly reads it from the jsonl envelope. The UI already displays cost (`Card.tsx:94`,
`RightDrawer.tsx:111`) — the gap is server-side data, not client rendering.

**Constraints:**
- Python 3 stdlib only (zero-deps)
- `total_cost_usd` in jsonl envelope = authoritative; pricing.json = fallback
- pricing.json committed and version-controlled (not gitignored)
- Read-once at server start (no hot-reload needed)

### Codebase reality check

| Store | Has `total_cost_usd` in data? | Current extraction | Fix needed |
|---|---|---|---|
| `claude_store.py` | ✅ Yes — jsonl envelope | ✅ Reads at line 351 | Add pricing fallback for sessions missing envelope cost |
| `codex_store.py` | ✅ Yes — jsonl envelope (codex CLI mirrors Claude Code format) | ❌ Hardcodes `None` at line 187 | Read `ev.get("total_cost_usd")` — same pattern as claude_store |
| `agy_store.py` | ❌ No — Antigravity SQLite + metadata JSON; no `usage` fields either (per module docstring: "no model, stop_reason, or usage") | ❌ Hardcodes `None` at line 195 | Document as known limitation; cost unavailable for agy sessions |

**Finding:** The plan's assumption that agy can extract cost is partially incorrect. agy's
storage format (Antigravity SQLite) carries no token/cost data. Pricing fallback can't
work without `usage` tokens either. agy cost remains `None` — this is a data-format
limitation, not an extraction bug.

## Decision SD1 — New module: `control_plane/pricing.py`

### Options

| Option | Pro | Con |
|---|---|---|
| A. New module | Same pattern as `analytics.py`; testable in isolation; server.py unchanged | +1 file (~60 lines) |
| B. Inline in `config.py` | No new file | Mixes static config concern with runtime pricing logic; `config.py` already handles `.env` + spine bindings |

### Decision: A

`control_plane/pricing.py` — two public entries:

```python
def load_pricing(path: str) -> dict[str, dict[str, float]]:
    """Read pricing.json → {model_id: {input: float, output: float}}.
    Called once at server start. Raises ValueError on invalid schema."""

def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
    pricing: dict[str, dict[str, float]],
) -> float | None:
    """Token→USD from pricing table. Returns None if model not in registry.
    Formula: (input_tokens * input_price + output_tokens * output_price) / 1_000_000"""
```

### Rejected: B

## Decision SD2 — `pricing.json` schema + location

### Location

`repos/switchboard/pricing.json` — committed, version-controlled, at app root (sibling to `server.py`).

### Schema

```json
{
  "models": {
    "claude-sonnet-5":    { "input": 3.00,  "output": 15.00 },
    "claude-opus-4-8":    { "input": 15.00, "output": 75.00 },
    "claude-haiku-4-5":   { "input": 0.80,  "output": 4.00  },
    "claude-fable-5":     { "input": 3.00,  "output": 15.00 },
    "gpt-5":              { "input": 2.50,  "output": 10.00 },
    "gemini-2.5-pro":     { "input": 1.25,  "output": 5.00  },
    "gemini-2.5-flash":   { "input": 0.30,  "output": 1.50  }
  },
  "default_fallback": null
}
```

Prices = USD per 1M tokens. `default_fallback: null` means no fallback when model
unknown (preserves `None` rather than guessing). A future aggregate dashboard (P2)
may set a conservative default to avoid undercounting.

### Rationale

- **Per-model pricing** — different models have different rates; a single fallback
  would be inaccurate across Claude/DeepSeek/Gemini tiers
- **`default_fallback: null`** — explicit choice: unknown model → `None` cost (safe)
  rather than a wrong guess (misleading). Per decision A4, aggregate cost is deferred to P2
- **No per-provider grouping** — models are the pricing unit; provider is a UI label.
  The same model across providers (e.g. `gpt-5` on Azure vs OpenAI) would need
  separate model_id entries if prices differ

### Rejected alternatives

- **Single default price** — inaccurate across tiers; a $0.80/M Haiku rate applied
  to Opus sessions gives wildly wrong costs
- **YAML** — adds a parser dependency (stdlib has `json`); JSON is adequate for a flat table

## Decision SD3 — Cost extraction: amend each store, not a post-hoc pipeline

### Options

| Option | Pro | Con |
|---|---|---|
| A. Amend each `read_session()` | Cost is a session property — belongs in SessionSummary; same pattern claude_store already uses | Touches 2–3 store files |
| B. Post-hoc enrichment in `discovery.py` | Stores untouched | Breaks store encapsulation — discovery shouldn't parse jsonl internals; adds I/O (re-reads jsonl per session) |

### Decision: A

1. **`codex_store.py` `read_session()`** — add `ev.get("total_cost_usd")` read
   inside the existing jsonl scan loop (identical to `claude_store.py:350-353`):

   ```python
   # Inside the per-event loop (after existing role/text extraction):
   if total_cost is None and ev.get("total_cost_usd") is not None:
       try:
           total_cost = float(ev["total_cost_usd"])
       except (TypeError, ValueError):
           pass
   ```

2. **`claude_store.py` `read_session()`** — add pricing fallback AFTER the
   existing `total_cost_usd` extraction but BEFORE returning SessionSummary:

   ```python
   # Existing: capture total_cost_usd from envelope
   # New: if still None and usage tokens available, fall back to pricing
   if total_cost is None and _pricing is not None:
       total_cost = _token_cost_from_messages(messages, _pricing)
   ```

   `_token_cost_from_messages()` sums `usage.input_tokens`/`output_tokens` across
   all assistant messages, then calls `pricing.calculate_cost()`. Model is read
   from the first assistant message's `model` field.

3. **`agy_store.py`** — no change. Document the limitation in a code comment:
   `# total_cost_usd=None — agy storage format carries no token/cost data`

4. **`server.py`** — load `pricing.json` once at startup, inject into store modules
   via a module-level `_pricing` singleton (same pattern as `_ENV_FILE`):

   ```python
   # server.py main():
   try:
       pricing_table = pricing.load_pricing(
           os.path.join(os.path.dirname(__file__), "pricing.json")
       )
   except (FileNotFoundError, ValueError) as e:
       print(f"switchboard: pricing.json unavailable — cost fallback disabled ({e})")
       pricing_table = None
   claude_store.set_pricing(pricing_table)
   ```

### Rejected: B

## Decision SD4 — agy cost: accept `None` (data-format limitation)

### Context

agy's Antigravity storage format (SQLite + metadata JSON) carries conversation
metadata (title, step count, preview text) but no token counts or cost data.
The `read_messages_rich()` docstring confirms: "no model, stop_reason, or usage."

### Options

| Option | Pro | Con |
|---|---|---|
| A. Accept None | Honest; no false data | agy cards show no cost |
| B. Estimate from turn_count × heuristic | Shows a number on agy cards | Inaccurate; mixes heuristic data with real cost data without clear distinction |
| C. Parse agy CLI stdout for token info | Accurate | agy runs as a PTY — cost info isn't structured; pattern matching stdout is fragile |

### Decision: A

agy sessions return `total_cost_usd=None`. The UI already handles `null` cost
gracefully (`costStr()` returns `"—"`). If Antigravity adds token/cost fields to
its metadata format in the future, revisit.

### Rejected: B, C

## Consequences

- **Positive:** codex sessions show cost on card + RightDrawer; claude sessions
  that lack envelope cost get a pricing-based fallback; all cost data flows
  through the existing `SessionSummary.total_cost_usd → SessionCard.total_cost_usd`
  pipeline — zero UI changes needed
- **Negative:** agy sessions never show cost (acceptable — format limitation)
- **Risk:** pricing.json becomes stale as model prices change → mitigated by
  version-controlling it (PR updates are lightweight) and by `total_cost_usd`
  envelope being authoritative (pricing.json is fallback only)
- **Risk:** pricing.json schema change breaks server start → mitigated by
  `try/except` at load time (server starts without fallback, logs warning)

## Handoff

→ `dev` implement against this ADR + HLD v2.6 delta § Branch A
