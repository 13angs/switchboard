"""Agent View control plane — session-centric kanban + in-browser terminal.

See projects/agent-view/docs/design/hld-workspace-native-orchestrator-v2.md.
  v2 — observer + launcher (discovery, state, archive, terminal, ws_handler).
  v1 Phases 1-4 superseded (task-centric, claude -p, post-merge removed).
"""

__all__ = [
    "config",
    "claude_store",
    "codex_store",
    "harness",
    "gh",
    "discovery",
    "state",
    "archive",
    "terminal",
    "ws_handler",
]
