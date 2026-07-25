# ADR-0008: Add agy (Antigravity CLI) as a Harness

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-07-19 |
| **Deciders** | Don (owner) |
| **Supersedes** | — (additive; extends ADR-0002 harness registry) |

## Context

Antigravity CLI (`agy`) is Google's new AI coding assistant that replaces Gemini
CLI. It was installed on the devcontainer at `/home/vscode/.local/bin/agy`
(v1.1.4). The user wants it available as a harness in the orchestrator board,
alongside `claude` and `codex`.

Key differences from existing harnesses:

- **Session store:** `~/.gemini/antigravity-cli/conversations/<uuid>.db` (SQLite)
  + `cache/conversation_metadata.json` — not jsonl files.
- **Resume CLI:** `--conversation <uuid>` (not `--resume`).
- **Models:** Built-in — Gemini 3.5 Flash (M/H/L), Gemini 3.1 Pro (L/H),
  Claude Sonnet 4.6, Claude Opus 4.6, GPT-OSS 120B — all through a single
  Google backend.
- **Auth:** OAuth token at `~/.gemini/antigravity-cli/antigravity-oauth-token`
  (handled by agy itself; orchestrator never touches it).
- **Zero config:** No provider env vars needed — agy manages its own backend
  communication.

## Decision

Add `agy` as a third harness in the adapter registry, following the ADR-0002
pattern exactly.

### Harness contract

| Harness | Providers | Fresh command | Resume command | Session store |
|---|---|---|---|---|
| `agy` | `google` | `agy` | `agy --conversation <uuid>` | `~/.gemini/antigravity-cli/` |

### Provider design

`agy` exposes a single provider, `google`. Unlike `claude` (where `deepseek`
and `ollama` are separate API backends), agy models all go through Google's
backend. Model selection is a separate concern — the board spawns `agy` and agy
uses its own configured default model. A future ADR can add model selection
(e.g. `--model "Claude Opus 4.6 (Thinking)"`) to the spawn flow.

### Discovery

`agy_store.py` reads:
1. `cache/last_conversations.json` — workspace-path → conversation UUID mapping
2. `cache/conversation_metadata.json` — per-session summary (title, timestamps,
   agent name, project ID)
3. `history.jsonl` — last command timestamp

The SQLite conversation databases are NOT parsed — metadata cache provides
enough signal for card columns (activity state, title, timestamps). Full
transcript parsing from SQLite is a deferred feature.

### Files changed

| File | Change |
|---|---|
| `control_plane/config.py` | Add `AGY_BIN`, `AGY_SESSION_ROOT` spine bindings |
| `control_plane/harness.py` | Register `agy` launcher, resolve, validate, `build_command()` |
| `control_plane/agy_store.py` | **New** — parse agy metadata cache → session summaries |
| `control_plane/discovery.py` | Union `agy_store.all_sessions_for_repo()` into card list |
| `server.py` | Thread `agy` through `_store_for`, `_chat_message_payload`, `_resolve_session_runtime`, `_start_id_capture` |
| `tests/test_agy_harness.py` | **New** — harness command building + store parsing |

### What does NOT change

- **Board UI** — `launchers` array already supports arbitrary harnesses;
  `{"harness": "agy", "providers": ["google"]}` renders correctly without
  frontend changes.
- **Terminal/PTY** — `terminal.py` is harness-agnostic; it delegates to
  `harness.build_command()`.
- **Permission model (S8)** — agy sessions get `--mode accept-edits`
  equivalent. agy's permission flag is `--dangerously-skip-permissions`
  (opt-in); the board does NOT pass it by default (same stance as S8 for
  claude).
- **Lock (S7)** — `lock.py` already takes `harness_name` and probes
  `/proc/<pid>/comm`; an `agy` process with `--conversation <uuid>` is
  detected correctly.

## Options considered

### Option A: Expose agy models as providers

Map each `agy models` entry → a provider under the agy harness (matching how
claude exposes `deepseek`/`ollama` as separate providers).

| Pro | Con |
|---|---|
| Model selection works from the existing provider dropdown | Models ≠ providers — all use the same Google backend |
| No new UI concept needed | `agy models` output format is human-readable, not machine-stable |
| | Choosing a provider that's really a model is misleading |

Rejected. The Claude harness has multiple providers because each is a different
API endpoint with separate auth. agy models all share one backend and one auth
token — they are not separate providers.

### Option B: Single `google` provider (chosen)

See Decision above.

### Option C: Hybrid — `google` default + optional dynamic model list

Parse `agy models` output at startup and expose models as provider entries
alongside `google`.

| Pro | Con |
|---|---|
| Most complete user experience | `agy models` stdout format may drift across versions |
| | Adds startup latency (subprocess call) |
| | Marginal benefit — most users stick with one model |

Rejected. This can be added later as a non-breaking enhancement when model
selection matters more.

## Consequences

### Positive

- agy sessions appear on the board alongside claude/codex sessions.
- agy spawn/resume works through the board's +New Session dialog and chat
  drawer.
- The ADR-0002 "add one adapter and one store" promise is validated by a real
  third harness.
- Zero additional configuration — agy auth is pre-provisioned on the
  devcontainer.

### Negative / cost

- `agy_store.py` adds a third session-store module to maintain.
- agy's metadata cache format may drift (same risk as Claude jsonl / Codex
  session_meta schema drift — isolated in the store module).
- Transcript reading from SQLite DBs is deferred — chat drawer transcript
  viewing for agy sessions starts as best-effort (metadata only).

### Follow-up risks

- agy is v1.1.4; CLI flags (`--conversation`, `--mode`) may change. Monitor
  `agy changelog`.
- SQLite conversation DB schema is undocumented — reverse-engineered, could
  change without notice.
- Rate limits reported by agy users (Pro/Ultra quota) may affect long-running
  board sessions.

## References

- ADR-0002: [`0002-harness-adapter-registry.md`](0002-harness-adapter-registry.md)
- HLD: `hld-workspace-native-orchestrator-v2.md` (living internal design doc, not published)
- agy CLI probe: `agy --help`, `agy models`, `agy agent`, session store at
  `~/.gemini/antigravity-cli/`
- Deep research on Antigravity: `tools/notebooklm/output/research_antigravity-cli_2026-07-19/report.md`
