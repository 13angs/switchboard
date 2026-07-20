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

## Layout

| Path | Role |
| --- | --- |
| `server.py` | stdlib HTTP/WebSocket server |
| `control_plane/` | Python package for harness stores, discovery, PTY, and config |
| `src/` | React + TypeScript UI |
| `tests/` | Python regression tests |

Provider settings use the existing `ORCH_*` env names for compatibility. Copy
`.env.example` to `.env` for local secrets; `.env` is gitignored.
