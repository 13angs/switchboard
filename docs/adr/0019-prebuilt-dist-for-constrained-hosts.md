---
title: "ADR-0019: Prebuilt dist for constrained hosts — CI builds the UI, hosts consume the artifact"
type: adr
created: 2026-07-25
status: accepted
project: switchboard
related: "docs/design/hld-workspace-native-orchestrator-v2.md § v2.7 delta"
supersedes_note: "resolves HLD § Open questions #2 (xterm.js CDN load) — obsolete, xterm is bundled"
implementation: "13angs/switchboard#6"
teams: [software-design, dev]
---

# ADR-0019: Prebuilt dist for constrained hosts

## Context

Switchboard would not run on the PRoot/Android tablet (`proot-tablet`, aarch64).
Diagnosed on-device 2026-07-25.

Two independent failures, only one of them host-specific.

### Failure 1 — Android linker blocks rollup's native binding (host-specific)

The `node` on `PATH` inside PRoot is **Termux's Android build**
(`/data/data/com.termux/files/usr/bin/node`, `process.platform === 'android'`),
not a glibc node in the PRoot rootfs — the Ubuntu guest has no node installed at
all. npm therefore installs `@rollup/rollup-android-arm64`, which is *correct*
for that platform.

But Android's bionic linker enforces a namespace whitelist. `dlopen` is only
permitted from `permitted_paths` — `/system/lib64`, `/data`, `/apex`, `/mnt/expand`,
and a handful of framework dirs. The checkout lives under `/workspaces`, outside
all of them:

```
dlopen failed: library ".../@rollup/rollup-android-arm64/rollup.android-arm64.node"
needed or dlopened by "/data/data/com.termux/files/usr/bin/node"
is not accessible for the namespace "(default)"
```

Rollup cannot load its binding → `vite build` dies → `dist/` is never produced →
`server.py` returns **404 on `/`**.

This is a path-namespace rule, not a PRoot syscall-translation limit. Verified by
copying the same `.node` under `/data/data/com.termux/files/home/` and calling
`require()` on it: **dlopen succeeds**.

### What was *not* the cause

The obvious suspects were tested and cleared:

| Hypothesis | Result |
| --- | --- |
| PTY unavailable under PRoot | **False** — `os.openpty()` returns `/dev/pts/4`; `pty.fork()` + exec works |
| Python backend cannot run | **False** — `/health` 200, `/state` 200 in 1.1s across 45 discovered sessions |
| xterm.js CDN load fails offline | **Stale** — no CDN reference remains; xterm is bundled into the agent chunk |
| esbuild native binary blocked | **False** — esbuild 0.25.12 runs; it is a spawned executable, not a `dlopen` |

The zero-deps stdlib backend the ADRs protect is genuinely portable. Only the
**frontend build** was ever blocked.

### Failure 2 — `npm run build` is broken on any clean install (not host-specific)

```
src/hooks/useXterm.ts(3,10): error TS2305: Module '"@xterm/addon-fit"' has no exported member 'FitAddon'.
src/hooks/useXterm.ts(4,10): error TS2305: Module '"@xterm/addon-web-links"' has no exported member 'WebLinksAddon'.
```

`@xterm/addon-fit@0.10.0` and `@xterm/addon-web-links@0.11.0` wrap their
declarations in `declare module '<pkg>' { ... }` inside a `.d.ts` that already has
a top-level `import`. That makes the block a *module augmentation* rather than an
ambient declaration, so under `moduleResolution: "bundler"` TypeScript resolves
the package to a file with no top-level exports.

`package-lock.json` has pinned `typescript` 5.9.3 since `bb1beeb initial agent-view
split`, so **every** clean `npm ci` reproduces this — devcontainer included. It is
pre-existing repo debt that the tablet investigation surfaced, not a tablet issue.

## Decision — CI builds the UI; constrained hosts fetch the artifact

`.github/workflows/build-dist.yml` builds on push to `main`, on PRs, and on manual
dispatch, uploading `dist/` as a 30-day artifact. `scripts/fetch-dist.sh` installs
that artifact from the latest successful `main` run via `gh`.

A host then needs **Python 3 and `gh`** — no Node toolchain:

```bash
scripts/fetch-dist.sh
python3 server.py --repo /workspaces/my-projects --port 8787
```

### Options considered

| Option | Mechanism | Rejected because |
| --- | --- | --- |
| **A. Relocate `node_modules`** | Move the tree under `/data/data/com.termux/files/home/` and symlink it back | Works (verified), but it is per-machine tribal knowledge that cannot ship with the repo, and it still demands a full Vite toolchain on a tablet |
| **B. glibc node inside the PRoot rootfs** | `apt install nodejs` → 20.19.4 → `platform === 'linux'` → normal dlopen | Two node installs on one device with `claude` CLI on Termux's — PATH ambiguity for no gain; 20.19.4 also sits right at Vite 6's floor |
| **C. Build on CI, consume the artifact** ✅ | Workflow artifact + `gh` fetch | — |

C wins because it makes the *backend's* zero-deps portability the only thing a
consuming host must satisfy. That property is already a hard-won constraint
(ADR `adr-orchestrator-v2-session-centric-pty-2026-07` AD1 rejected `aiohttp`
specifically to keep it). Requiring a working Vite toolchain everywhere would have
quietly relocated the portability burden to the frontend.

Option A is kept as a documented escape hatch in the repo README for when a build
genuinely must happen on-device. One trap is recorded there: the relocated
directory **must still be named `node_modules`**, or Node's package resolution
walks past it (`Cannot find package 'picomatch'`).

### Companion fixes

- `src/xterm-addons.d.ts` — ambient declarations for the two addons. A workaround;
  delete it when upstream ships top-level exports.
- `.gitignore` — `node_modules/` → `node_modules`, so a symlinked tree matches.

## Consequences

- **Positive:** the board runs on any host with Python 3 + `gh`; `npm run build` is
  green again on clean installs; every PR now gets a build check, which is what
  would have caught Failure 2.
- **Negative:** the artifact is a second distribution channel to keep alive — a red
  `build-dist` run means constrained hosts silently keep serving a stale `dist/`.
- **Negative:** consuming hosts now depend on GitHub reachability + `gh` auth at
  fetch time. Air-gapped use needs a manually copied `dist/`.
- **Risk:** 30-day artifact retention. A host that goes quiet for longer finds no
  artifact; `scripts/fetch-dist.sh <run-id>` pins an older run and re-running the
  workflow refreshes it.
- **Risk:** the typings shim can drift from the real addon API. It declares only
  the surface `useXterm.ts` uses, so an unshimmed member fails loudly at compile
  time rather than silently at runtime.

## Verification

Run on the PRoot/Android host this fixes, 2026-07-25:

- `npm run build` → tsc clean, vite built in 7.29s, 12 assets
- `python3 -m pytest tests/ -q` → **125 passed** in 8.89s
- `/`, `/agent`, `/analytics`, `/health` → all **200**; agent chunk 373904 bytes
- `/state` → 200 in 1.1s across 45 sessions

The CI path was then verified on the same host, against run `30143923329`
(`build-dist`, PR #6, `completed/success`):

- `scripts/fetch-dist.sh 30143923329` → installed 12 files into `dist/`
- served that artifact with no local build: `/`, `/agent`, `/analytics`,
  `/health` → all **200**

So both paths are measured end-to-end. What remains unexercised is the *default*
invocation — `scripts/fetch-dist.sh` with no run id resolves the latest successful
run **on `main`**, and the workflow does not exist on `main` until PR #6 merges.

## Handoff

→ `dev` — after 13angs/switchboard#6 merges, run bare `scripts/fetch-dist.sh` on
`proot-tablet` once to confirm the main-branch resolution path (the pinned-run-id
path is already verified above).

**Closed 2026-07-25.** #6 merged; the `build-dist` push run on `main`
(`30144278252`) succeeded. From a checkout with `dist/` and `node_modules` both
removed, bare `scripts/fetch-dist.sh` resolved that run, installed 12 files, and
`/`, `/agent`, `/analytics`, `/health` served **200** (`/state` 200 in 0.42s).
`git status` clean, confirming the `.gitignore` change. No open task.

*(Appended, not rewritten — the decision above is accepted and immutable; this
records the outcome of the handoff it created.)*
