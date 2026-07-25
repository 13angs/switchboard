# ADR-0009: agy Discovery Source Contract

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-07-19 |
| **Deciders** | Don (owner) |
| **Supersedes** | ADR-0008 discovery assumption that `last_conversations.json` is sufficient as the session index |

## Context

ADR-0008 added `agy` as a third harness and assumed the metadata cache was
stable enough for board discovery. The live cache shape disproved that
assumption:

- `conversations/<uuid>.db` can exist even when metadata is missing.
- `conversation_metadata.json` can contain a conversation that is not present in
  `last_conversations.json`.
- `last_conversations.json` records the latest conversation per workspace; it is
  not a complete conversation index.

The board symptom was that real `agy` sessions either disappeared from
discovery or appeared as empty, untitled `Idle` cards at the bottom of the
board.

## Decision

`agy_store` treats the three local sources as separate authorities:

| Source | Role |
|---|---|
| `conversations/*.db` | durable existence + resume authority |
| `cache/conversation_metadata.json` | enrichment: title, workspace URI, turn count, last modified time |
| `cache/last_conversations.json` | fallback cwd mapping + latest-per-workspace hint only |

Discovery unions candidate ids from all three sources, then filters to the
current repo only when a cwd can be resolved from metadata or the last-map
fallback.

Timestamp order uses `last_modified_time` when available and falls back to the
conversation DB mtime when metadata is absent. `agy` timestamps with nanosecond
fractions are normalized to Python-supported microseconds before parsing.

Cards with an existing DB but missing metadata remain visible and get a
`metadata pending` note instead of looking like blank sessions.

## Consequences

### Positive

- Real `agy` sessions are no longer dropped merely because they are absent from
  `last_conversations.json`.
- Fresh-session id capture can resolve a new `agy` conversation even before
  metadata enrichment appears.
- Incomplete cache state is visible on the board as a recoverable state, not a
  silent disappearance.

### Negative / cost

- `agy_store` performs a small union across cache files and DB filenames instead
  of reading one JSON file.
- Sessions with no resolvable cwd remain excluded from repo-scoped board
  discovery until metadata or last-map state identifies their workspace.

## References

- ADR-0008: [`0008-agy-antigravity-harness.md`](0008-agy-antigravity-harness.md)
- HLD: [`../design/react-architecture.md`](../design/react-architecture.md)
