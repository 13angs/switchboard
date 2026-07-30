# Switchboard

One browser board for every coding-agent session running on your machine —
across CLI harnesses, in one place.

If you run more than one agent at a time, you lose track of them. Each harness
keeps its own session store, each terminal tab is a separate window, and
"which of these is waiting on me?" has no answer short of checking all of them.
Switchboard reads those session stores directly, unions them into a single
kanban board, and gives you an attached terminal or a readable transcript for
any session you pick.

<!-- TODO(screenshot): board view, then the agent shell. Capture against a
     throwaway repo — a screenshot of your real board leaks your branch and
     session names. -->

## What it does

- **Discovers** sessions from every supported harness store and overlays git /
  GitHub metadata (branch, PR state) for the repo you point it at.
- **Attaches** to a live or resumed session over a WebSocket-backed PTY, so you
  can drive an agent from the browser.
- **Reads** transcripts as structured messages, with a raw-JSON toggle for when
  the normalizer meets a schema it doesn't know.
- **Scores** session health from graduated signals, so a stalled or
  rate-limited session is visible without opening it.
- **Extracts cost** per session from a per-harness pricing registry.

It deliberately does **not** merge pull requests, drive tasks, or fan work out
to agents. It observes and launches; the decisions stay yours.

## Your data stays local

The server is `python3` with the standard library only, binds to `127.0.0.1`,
and reads session stores from your own filesystem. Transcripts are never
uploaded anywhere — there is no backend, no telemetry, and no account. The one
outbound call is to the GitHub API for PR metadata, and only for the repo you
pass with `--repo`.

Provider tokens, if you configure extra providers, are read from a gitignored
`.env` and injected into the harness process. They are not logged or served.

## Quick start

```bash
git clone https://github.com/13angs/switchboard.git
cd switchboard
npm ci && npm run build
python3 server.py --repo /path/to/your/repo --port 8787
```

Open `http://127.0.0.1:8787`.

`server.py` needs Python **3.10 or newer** and nothing else — no pip install, no
virtualenv. CI runs the test suite on 3.10 and 3.13, so those are the two the
floor is actually measured against. Env overrides for the flags: `ORCH_PORT`,
`ORCH_REPO`, `ORCH_ENV_FILE`.

### Running without a local build

`GET /` serves `dist/`, so the frontend has to be built once. The Python server
runs anywhere; the **build** is the part with a toolchain requirement — and on
Termux/PRoot (Android) it cannot run at all. The `node` on `PATH` is Termux's
Android build, so npm installs `@rollup/rollup-android-arm64`, and Android's
linker only permits `dlopen` from its namespace `permitted_paths`
(`/system/lib64`, `/data`, `/apex`, …). A checkout outside those paths fails to
load rollup's native binding:

```
dlopen failed: library ".../rollup.android-arm64.node" ... is not accessible
for the namespace "(default)"
```

`vite build` dies, `dist/` is never produced, and `GET /` returns 404.

On those machines, skip the build and fetch the bundle CI already produced:

```bash
scripts/fetch-dist.sh
python3 server.py --repo /path/to/your/repo --port 8787
```

`scripts/fetch-dist.sh` downloads the bundle attached to the rolling
[`dist` release](../../releases/tag/dist), which
[`build-dist`](.github/workflows/build-dist.yml) replaces on every `main` build.
The machine needs `curl` and no Node toolchain — no `gh`, no GitHub account. If
you would rather not run a script, the asset is a plain URL:

```bash
mkdir -p dist
curl -fsSL https://github.com/13angs/switchboard/releases/download/dist/dist.tar.gz \
  | tar -xz -C dist
```

To pin or bisect an older build, pass a `build-dist` run id
(`scripts/fetch-dist.sh 123456789`). That path reads a workflow artifact, which
is behind an authenticated API even on a public repo, so it does need `gh`.

If you do need to build on-device, move `node_modules` under a path the Android
linker permits and symlink it back in. Termux's home (`/data/…`) qualifies;
PRoot's own `$HOME` (`/root`) does not. The directory must still be named
`node_modules` or Node's package resolution breaks (`Cannot find package
'picomatch'`):

```bash
TERMUX_HOME=/data/data/com.termux/files/home
npm ci
mkdir -p "$TERMUX_HOME/.switchboard-build"
mv node_modules "$TERMUX_HOME/.switchboard-build/node_modules"
ln -s "$TERMUX_HOME/.switchboard-build/node_modules" node_modules
npm run build
```

### What the store cost

```bash
python3 scripts/price_store.py
```

Prints per-session cost, the store total, and any model the rate card cannot
price. Same code path the board renders from — so it is also the way to check a
cost figure quoted in a design doc without opening the UI.

Every rate in `pricing.json` must carry a `source` URL and a `checked_on` date;
the loader rejects the file otherwise, and the board shows the oldest of those
dates in the cost tooltip (ADR-0026). Nothing detects a rate that is simply
*wrong* — that has no offline oracle, so verification is a human act: open the
six sources, correct anything that moved, bump `checked_on`, commit.

## Supported harnesses

**+ New session** in the header asks for a harness, then a provider.

| Harness  | Providers                                       | Session store                                         |
| -------- | ----------------------------------------------- | ----------------------------------------------------- |
| `claude` | `claude`; `deepseek` / `ollama` when configured | `ORCH_SESSION_ROOT` → `~/.claude/projects`             |
| `codex`  | `openai`                                        | `$CODEX_HOME/sessions`                                 |
| `agy`    | `google`                                        | `ORCH_AGY_SESSION_ROOT` → `~/.gemini/antigravity-cli`  |

Harness-specific parsing is isolated in `control_plane/*_store.py`. Agent
transcript schemas are unstable across releases, so stores are written to
degrade to null fields rather than crash discovery — expect to add a store
module, not to patch a shared parser, when a new harness appears.

## Endpoints

| Route                                                   | Returns                                            |
| ------------------------------------------------------- | -------------------------------------------------- |
| `GET /`                                                 | session kanban                                     |
| `GET /agent?session_id=<id>&view=terminal\|chat\|files` | agent-session shell                                |
| `GET /ws/agent?session_id=<id>`                         | WebSocket to the selected harness PTY               |
| `GET /state`                                            | `{generated_at, repo, activities[], providers[], launchers[], sessions[]}` |
| `GET /events`                                           | SSE lifecycle events (`approval_required`, `input_ready`) |
| `GET /session/<id>/transcript`                          | normalized transcript messages                     |
| `POST /session/<id>/message`                            | write a turn to the live/resumed PTY                |
| `POST /session/start`                                   | spawn a fresh harness PTY                           |
| `GET /health`                                           | `{ok: true}`                                        |

## Extra providers

`claude` works with no configuration. `deepseek` and `ollama` appear in the
provider picker only when their `ORCH_*` keys are present in `.env` or the
server's environment; both run through the `claude` CLI with `ANTHROPIC_*`
overrides. Copy [`.env.example`](.env.example) to `.env` and fill in what you
need — `.env` is gitignored.

## Layout

| Path             | Role                                                      |
| ---------------- | --------------------------------------------------------- |
| `server.py`      | stdlib HTTP + WebSocket server, routes                    |
| `control_plane/` | harness stores, discovery, PTY lifecycle, config, pricing |
| `src/`           | React + TypeScript UI (Vite)                              |
| `tests/`         | Python regression tests                                   |
| `docs/`          | architecture decision records + frontend architecture     |

## Design records

[`docs/`](docs/README.md) carries the twenty ADRs behind the choices above —
why the harness adapter registry exists, why chat started read-only, why CI
builds the UI instead of every host, why the project is called Switchboard.
Each one states the options weighed and what the chosen one costs, and they are
immutable: a decision that stops holding gets superseded by a later ADR rather
than quietly edited.

Read them if you want to know whether the design will survive your use case.
They are also the honest record of what this project got wrong on the first
attempt — the v1 architecture was task-centric and is gone.

## Status and support

Personal tool, shared because it might be useful. It is built for how I work
and changes when my workflow does — treat `main` as the only supported
version. Issues are closed and I am not taking feature requests; forks and
patches for your own setup are very welcome, and that is the intended way to
use this.

The one release, tagged `dist`, is not a version. It is a fixed tag whose
attached bundle is overwritten by every `main` build, so that hosts which
cannot build the UI have somewhere permanent to fetch it from. There are no
version tags and there will not be any.

## License

[MIT](LICENSE)
