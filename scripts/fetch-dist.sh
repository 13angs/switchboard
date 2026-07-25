#!/usr/bin/env bash
# Fetch the prebuilt UI bundle so this machine never has to run the Vite
# toolchain.
#
# Motivation: on Termux/PRoot the `node` on PATH is Termux's Android build, so
# npm installs `@rollup/rollup-android-arm64`. Android's linker only allows
# dlopen from its namespace `permitted_paths` (/system/lib64, /data, /apex, …),
# and this repo lives outside them, so loading rollup's native binding fails
# and `vite build` dies. The backend is pure Python stdlib and runs fine — only
# the frontend build is blocked. See README § Running without a local build.
#
# Two sources, because they answer different questions (ADR-0021):
#
#   default   — the asset on the rolling `dist` release. Never expires, and a
#               public repo serves it without auth, so this path needs `curl`
#               and nothing else. This is the supported install.
#   <run-id>  — the workflow artifact from one specific `build-dist` run, for
#               pinning or bisecting an older build. Artifacts sit behind an
#               authenticated API even on a public repo, so this path does
#               still require `gh`.
#
# Usage:
#   scripts/fetch-dist.sh            # current build of main, no auth needed
#   scripts/fetch-dist.sh <run-id>   # a specific workflow run (needs gh)

set -euo pipefail

REPO="${SWITCHBOARD_REPO:-13angs/switchboard}"
WORKFLOW="build-dist.yml"
ARTIFACT="dist"
RELEASE_TAG="dist"
ASSET="dist.tar.gz"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/dist"

run_id="${1:-}"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
mkdir "$staging/dist"

if [ -z "$run_id" ]; then
  command -v curl >/dev/null 2>&1 || {
    echo "fetch-dist: 'curl' is required" >&2
    exit 1
  }

  url="https://github.com/$REPO/releases/download/$RELEASE_TAG/$ASSET"
  echo "fetch-dist: downloading $url"

  curl -fsSL "$url" -o "$staging/$ASSET" || {
    echo "fetch-dist: could not download the release asset." >&2
    echo "fetch-dist: if the '$RELEASE_TAG' release does not exist yet, or $REPO" >&2
    echo "fetch-dist: is private, pin a run instead: scripts/fetch-dist.sh <run-id>" >&2
    exit 1
  }

  tar -xzf "$staging/$ASSET" -C "$staging/dist"
else
  command -v gh >/dev/null 2>&1 || {
    echo "fetch-dist: pinning a run needs 'gh' (https://cli.github.com)" >&2
    echo "fetch-dist: for the current build of main, run with no arguments" >&2
    exit 1
  }
  gh auth status >/dev/null 2>&1 || {
    echo "fetch-dist: 'gh' is not authenticated — run 'gh auth login'" >&2
    echo "fetch-dist: for the current build of main, run with no arguments" >&2
    exit 1
  }

  echo "fetch-dist: downloading artifact '$ARTIFACT' from run $run_id ($REPO)"
  gh run download "$run_id" --repo "$REPO" --name "$ARTIFACT" --dir "$staging/dist"
fi

[ -f "$staging/dist/index.html" ] || {
  echo "fetch-dist: bundle is missing index.html — refusing to install" >&2
  exit 1
}

rm -rf "$DEST"
mv "$staging/dist" "$DEST"

echo "fetch-dist: installed $(find "$DEST" -type f | wc -l) files into $DEST"
echo "fetch-dist: run 'python3 server.py --repo <workspace> --port 8787'"
