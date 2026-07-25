---
title: "ADR-0012: Bulk session archive actions"
type: adr
created: 2026-07-21
status: accepted
scope: switchboard
implements: "13angs/switchboard PR #1"
---

# ADR-0012: Bulk Session Archive Actions

## Context

Switchboard already supports single-session manual dismiss through
`POST /session/<id>/dismiss` and restore through
`POST /session/<id>/undismiss`. Don needs the same cleanup workflow for many
sessions at once from the Board, especially after accumulated stale sessions.

The existing archive model is intentionally a local JSON ledger, not destructive
transcript deletion. A "delete" action in this surface therefore means
"dismiss/archive from the active board" unless a later ADR explicitly designs a
destructive retention feature.

Constraints:

- Archive semantics stay server-owned in `ArchiveLedger`.
- Session cards remain keyed by `session_id`.
- Board polling uses cached discovery, so archive mutations must invalidate the
  state cache.
- Batch cleanup should not issue N independent HTTP requests or N ledger writes.
- Archive view must be reversible.

## Options

| Option | Shape | Benefits | Costs |
| --- | --- | --- | --- |
| A. Client loops existing single-session endpoints | Board calls `/session/<id>/dismiss` once per selected session | No new API | N requests, N ledger writes, partial-failure semantics are hard to reason about |
| B. Add batch archive endpoints | Board posts `{session_ids}` to `/sessions/dismiss` or `/sessions/undismiss` | One request, one ledger write, explicit contract, easy to test | Adds two REST endpoints and typed client wrappers |
| C. Add destructive delete | Remove transcript files / store rows | Frees disk and matches literal "delete" | High-risk data loss; unclear harness-store ownership; violates current archive-ledger design |

## Decision

Choose **B: batch archive endpoints**.

Add:

- `ArchiveLedger.dismiss_many(session_ids, repo_root, auto=False)`
- `ArchiveLedger.undismiss_many(session_ids, repo_root)`
- `POST /sessions/dismiss` with body `{session_ids: string[]}`
- `POST /sessions/undismiss` with body `{session_ids: string[]}`
- Board-level selection state scoped to the currently visible Active/Archive
  cards.

`dismiss_many` refreshes entries for every unique, non-empty `session_id` and
persists the JSON ledger once. `undismiss_many` removes entries that currently
exist and also persists once when anything changed. Both preserve request order
in their response.

The Board renders checkbox selection on cards plus a bulk action bar:

- Active view: `Dismiss selected`
- Archive view: `Restore selected`
- Shared controls: selected count, `Select all visible`, `Clear`

## Consequences

- "Delete" remains non-destructive archive/dismiss behavior.
- Batch actions are reversible through Archive view restore.
- State cache invalidation happens once per bulk request.
- Existing single-session endpoints stay as card-level convenience actions.
- A future destructive purge/retention policy requires a separate ADR because it
  would touch harness-owned transcript stores.

## Validation Contract

Implementation must include tests for:

- batch dismiss writes the archive ledger once for many ids
- batch restore writes the archive ledger once when entries are removed
- HTTP routes accept duplicate ids and return unique accepted ids
- invalid `session_ids` payloads return `400`

Frontend implementation must pass the existing TypeScript/Vite build.
