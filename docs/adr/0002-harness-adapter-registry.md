# ADR-0002: Harness Adapter Registry for Claude and Codex

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-07-15 |
| **Deciders** | Don (owner) |
| **Supersedes** | Parts of HLD server/session assumptions that treated Claude as the only harness |

## Context

The orchestrator now has a React board, terminal page, and chat page that show
both `harness` and `provider` chips. The implementation, however, still treats
Claude as the only executable harness:

- `terminal.spawn_claude()` builds only `claude` / `claude --resume` commands.
- `config.provider_env()` treats `provider` as a Claude-harness model-provider
  override (`claude` or `deepseek`).
- discovery reads only `~/.claude/projects/.../*.jsonl`.

Don approved a design step before implementation because Codex support is not a
dropdown-only change. Codex has a different command contract and a different
session store:

- Interactive Codex runs through `codex`; resume is `codex resume <SESSION_ID>`.
- Codex local state lives under `CODEX_HOME`, defaulting to `~/.codex`.
- Codex session transcripts live under `$CODEX_HOME/sessions`.
- In the current local CLI (`codex-cli 0.143.0`), a transcript contains
  `session_meta`, `turn_context`, `event_msg`, and `response_item` records.

The OpenAI Codex manual documents `codex`, `codex resume`, `codex exec`, the
`CODEX_HOME` state root, and `$CODEX_HOME/sessions` transcript location. Local
CLI help was also checked for the exact command flags available in this
environment.

## Decision

Introduce a **Harness Adapter Registry**.

`harness` and `provider` are separate concepts:

- `harness` is the executable/runtime family: `claude`, `codex`.
- `provider` is a harness-local provider/model profile: `claude`, `deepseek`,
  `openai`, or later harness-specific names.

The server will route all session lifecycle operations through a registry of
harness adapters instead of hard-coding Claude in the terminal/session path.

```text
HarnessAdapter
- name
- available_providers(env_file, process_env)
- provider_env(provider, env_file, process_env)
- build_fresh_command(cwd, provider)
- build_resume_command(session_id, cwd, provider)
- store adapter
- parse transcript
- discover sessions under repo_root
```

Initial adapters:

| Harness | Providers | Fresh command | Resume command | Session store |
|---|---|---|---|---|
| `claude` | `claude`, `deepseek` | `claude` | `claude --resume <session_id>` | `ORCH_SESSION_ROOT` / `~/.claude/projects` |
| `codex` | `openai` | `codex --no-alt-screen -C <cwd>` | `codex --no-alt-screen -C <cwd> resume <session_id>` | `CODEX_HOME/sessions` |

DeepSeek remains a provider of the Claude harness. Adding Codex must not move
or hide DeepSeek behind another harness; when DeepSeek credentials are
configured, `/state.launchers` must expose `{"harness": "claude",
"providers": ["claude", "deepseek"]}`.

Provider configuration still comes from the orchestrator env layer. The normal
file is gitignored `projects/switchboard/repos/switchboard/.env`. Per-task worktrees under
`.claude/worktrees/...` do not contain that file, so the server resolves provider
config in this order:

1. `ORCH_ENV_FILE`, when explicitly set.
2. The current checkout's `projects/switchboard/repos/switchboard/.env`.
3. The main checkout's `projects/switchboard/repos/switchboard/.env`, when running from a workspace
   task worktree and the worktree-local file is absent.

`codex exec` is not the first implementation target. It is a non-interactive
automation surface, while the orchestrator terminal is a PTY-backed interactive
surface. A later automation lane can add `codex exec --json` as a separate
mode, but it must not be mixed into the interactive terminal contract.

## Options considered

### Option A: Add `codex` as another `provider` under the Claude path

| Pro | Con |
|---|---|
| Smallest UI change | Incorrect abstraction: `provider` would mean both model override and executable harness |
| Reuses existing `provider_env()` shape | Forces Codex into Claude's `~/.claude/projects` discovery model |
| Quick to start | Resume command, session id capture, transcript parser, and auth state differ |

Rejected. This would make the API easier for one PR and harder for every
follow-up. It also makes the `harness` chip mostly decorative instead of a real
contract field.

### Option B: Branch inside existing Claude modules

| Pro | Con |
|---|---|
| Keeps file count low | `claude_store.py`, `terminal.py`, and `server.py` become mixed-harness modules |
| Minimal TypeScript API churn | Harder to isolate future schema drift in either Claude or Codex |
| Preserves current `/state.providers` short-term | Tests become branch-heavy and naming misleading |

Rejected. The current design already isolates Claude's unstable jsonl schema in
`claude_store.py`; Codex deserves the same isolation rather than adding
cross-product branches there.

### Option C: Harness Adapter Registry (chosen)

| Pro | Con |
|---|---|
| Correctly separates executable harness from provider/model profile | Requires API and TypeScript contract migration |
| Keeps Claude and Codex transcript parsing isolated | More modules than a branch-in-place patch |
| Makes future harnesses additive | Needs backward compatibility for existing `provider` query links |
| Lets `/state` expose launch options explicitly | Initial implementation must thread `harness` through UI and server |

Chosen. It matches the UI vocabulary already present on cards and right
drawers, and it keeps product-specific session stores out of each other.

## Interface contract

### `GET /state`

Add `launchers` and preserve `providers` during the migration for backward
compatibility.

```json
{
  "generated_at": "2026-07-15T00:00:00+00:00",
  "repo": "/workspaces/my-projects",
  "activities": ["Working", "Awaiting", "Blocked", "Idle"],
  "providers": ["claude", "deepseek"],
  "launchers": [
    {"harness": "claude", "providers": ["claude", "deepseek"]},
    {"harness": "codex", "providers": ["openai"]}
  ],
  "sessions": []
}
```

### `SessionCard`

Every card must carry real backend-derived values:

```json
{
  "session_id": "019f6489-3463-73c1-9808-2312d18b7564",
  "harness": "codex",
  "provider": "openai"
}
```

For legacy Claude sessions with no sidecar, `harness = "claude"` and
`provider = "claude"`.

### `POST /session/start`

Move from provider-only to harness-aware input:

```json
{
  "harness": "codex",
  "provider": "openai",
  "label": "orchestrator codex test"
}
```

Backward compatibility: if `harness` is omitted, treat it as `claude` and keep
existing `provider` behavior.

### `/terminal` and `/chat` query params

New links include both fields:

```text
/terminal?harness=codex&provider=openai
/chat?session_id=<id>&harness=codex&provider=openai
```

Backward compatibility: if `harness` is omitted, infer `claude`.

## Data model

Add a harness sidecar next to existing provider metadata where needed:

```text
<session-id>.harness.json  -> {"harness": "codex", "provider": "openai"}
```

Claude's existing `.provider.json` remains valid. During migration:

1. Read `.harness.json` first.
2. Fall back to Claude `.provider.json`.
3. Fall back to transcript detection.
4. Fall back to `claude` / `claude`.

Codex transcripts do not need a sidecar for discovery because `session_meta`
identifies Codex sessions and their cwd, but the sidecar is still useful for
sessions started by the orchestrator before the transcript is fully captured.

## Implementation order

1. Add `harness.py` / adapter registry and rename terminal primitives from
   Claude-specific wording toward harness-neutral process wording.
2. Add `codex_store.py` for `$CODEX_HOME/sessions/**/*.jsonl`, parsing:
   - id/cwd/provider from `session_meta.payload`
   - model/sandbox/approval metadata from `turn_context.payload`
   - transcript turns from `event_msg.payload.type=user_message|agent_message`
3. Extend discovery to union Claude and Codex session summaries, then emit
   `harness` and `provider` on every `SessionCard`.
4. Thread `harness` through `POST /session/start`, `/ws/terminal`, chat send,
   kill, transcript, and UI links.
5. Update React types and `NewSessionDialog` to choose harness first, then
   provider.
6. Add tests:
   - adapter command building for Claude and Codex
   - Codex transcript parsing fixture
   - `/state.launchers` contract
   - backward compatibility for provider-only Claude links
   - worktree `.env` fallback keeps the Claude launcher exposing DeepSeek when
     DeepSeek is configured in the main checkout

## Consequences

### Positive

- The UI's `harness` chip becomes a real backend contract.
- Codex support can land without weakening Claude/DeepSeek behavior.
- Worktree-based development sees the same configured Claude providers as the
  main checkout unless `ORCH_ENV_FILE` intentionally points elsewhere.
- Future harnesses can add one adapter and one store instead of branching every
  server path.
- Transcript schema drift stays isolated per harness.

### Negative / cost

- API contract changes from provider-only to harness-aware.
- Server modules need a small naming migration (`spawn_claude` becomes
  harness-neutral).
- Chat transcript rendering for Codex starts as best-effort because the Codex
  schema differs from Claude and may evolve.

### Follow-up risks

- `codex app-server` and the Codex SDK may become better control surfaces later,
  but they should be evaluated in a separate ADR. This decision is only for the
  current PTY-backed orchestrator.
- `codex exec --json` is useful for automation, but it is not equivalent to an
  interactive terminal session.
- Codex auth files such as `~/.codex/auth.json` are secrets. The orchestrator
  must never read or expose credential files; it only shells out to the CLI and
  reads session transcripts.

## References

- HLD: [`react-architecture.md`](../design/react-architecture.md)
- ADR-0001: [`0001-react-typescript-vite.md`](0001-react-typescript-vite.md)
- OpenAI Codex manual: CLI command reference, authentication/state locations,
  and non-interactive mode
- Local CLI verification: `codex-cli 0.143.0`, `codex --help`, `codex resume --help`,
  `codex exec --help`
