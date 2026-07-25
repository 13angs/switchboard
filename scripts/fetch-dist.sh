#!/usr/bin/env bash
# Fetch the prebuilt UI bundle from the latest successful `build-dist` run on
# main, so this machine never has to run the Vite toolchain.
#
# Motivation: on Termux/PRoot the `node` on PATH is Termux's Android build, so
# npm installs `@rollup/rollup-android-arm64`. Android's linker only allows
# dlopen from its namespace `permitted_paths` (/system/lib64, /data, /apex, …),
# and this repo lives outside them, so loading rollup's native binding fails
# and `vite build` dies. The backend is pure Python stdlib and runs fine — only
# the frontend build is blocked. See README § Running without a local build.
#
# Usage:
#   scripts/fetch-dist.sh            # latest successful main build
#   scripts/fetch-dist.sh <run-id>   # a specific workflow run

set -euo pipefail

REPO="${SWITCHBOARD_REPO:-13angs/switchboard}"
WORKFLOW="build-dist.yml"
ARTIFACT="dist"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/dist"

command -v gh >/dev/null 2>&1 || {
  echo "fetch-dist: 'gh' is required (https://cli.github.com)" >&2
  exit 1
}
gh auth status >/dev/null 2>&1 || {
  echo "fetch-dist: 'gh' is not authenticated — run 'gh auth login'" >&2
  exit 1
}

run_id="${1:-}"
if [ -z "$run_id" ]; then
  run_id="$(gh run list --repo "$REPO" --workflow "$WORKFLOW" \
    --branch main --status success --limit 1 \
    --json databaseId --jq '.[0].databaseId')"
fi

if [ -z "$run_id" ] || [ "$run_id" = "null" ]; then
  echo "fetch-dist: no successful '$WORKFLOW' run found on main for $REPO" >&2
  echo "fetch-dist: trigger one with 'gh workflow run $WORKFLOW --repo $REPO'" >&2
  exit 1
fi

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT

echo "fetch-dist: downloading artifact '$ARTIFACT' from run $run_id ($REPO)"
gh run download "$run_id" --repo "$REPO" --name "$ARTIFACT" --dir "$staging"

[ -f "$staging/index.html" ] || {
  echo "fetch-dist: artifact is missing index.html — refusing to install" >&2
  exit 1
}

rm -rf "$DEST"
mv "$staging" "$DEST"
trap - EXIT

echo "fetch-dist: installed $(find "$DEST" -type f | wc -l) files into $DEST"
echo "fetch-dist: run 'python3 server.py --repo <workspace> --port 8787'"
