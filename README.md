# Agent View

Workspace-native session board for monitoring and launching local agent
sessions across a workspace.

## Run

```bash
npm ci
npm run build
python3 server.py --repo /workspaces/my-projects --port 8787
```

Open `http://127.0.0.1:8787`.

## Running without a local build

The server is Python 3 stdlib only and runs anywhere. The **frontend build** is
the part with a toolchain requirement, and on Termux/PRoot (Android) it cannot
run at all: the `node` on `PATH` is Termux's Android build, so npm installs
`@rollup/rollup-android-arm64`, and Android's linker only permits `dlopen` from
its namespace `permitted_paths` (`/system/lib64`, `/data`, `/apex`, …). A
checkout outside those paths therefore fails to load rollup's native binding:

```
dlopen failed: library ".../rollup.android-arm64.node" ... is not accessible
for the namespace "(default)"
```

`vite build` dies, `dist/` is never produced, and `GET /` returns 404.

On those machines, skip the build and fetch the bundle CI already produced:

```bash
scripts/fetch-dist.sh
python3 server.py --repo /workspaces/my-projects --port 8787
```

`scripts/fetch-dist.sh` pulls the `dist` artifact from the latest successful
[`build-dist`](.github/workflows/build-dist.yml) run on `main` via `gh`, so the
machine needs `gh` authenticated but no Node toolchain. Pass a run id to pin a
specific build (`scripts/fetch-dist.sh 123456789`).

If you do need to build on-device, move `node_modules` under a path the Android
linker permits and symlink it back in. Termux's home (`/data/…`) qualifies;
PRoot's own `$HOME` (`/root`) does not. The directory must still be named
`node_modules` or Node's package resolution breaks (`Cannot find package
'picomatch'`):

```bash
TERMUX_HOME=/data/data/com.termux/files/home
npm ci
mkdir -p "$TERMUX_HOME/.agent-view-build"
mv node_modules "$TERMUX_HOME/.agent-view-build/node_modules"
ln -s "$TERMUX_HOME/.agent-view-build/node_modules" node_modules
npm run build
```

## Layout

| Path | Role |
| --- | --- |
| `server.py` | stdlib HTTP/WebSocket server |
| `control_plane/` | Python package for harness stores, discovery, PTY, and config |
| `src/` | React + TypeScript UI |
| `tests/` | Python regression tests |

Provider settings use the existing `ORCH_*` env names for compatibility. Copy
`.env.example` to `.env` for local secrets; `.env` is gitignored.
