# Design records

Why Switchboard is shaped the way it is. These are the decisions, not a manual —
for how to run it, see the [root README](../README.md).

## Architecture decision records

Each ADR states the problem, the options weighed, the option taken, and what it
costs. They are **immutable once accepted**: a decision that no longer holds
gets superseded by a later ADR rather than edited, so the record shows what was
believed at the time and why it changed.

| # | Decision |
| --- | --- |
| [0001](adr/0001-react-typescript-vite.md) | React + TypeScript + Vite for the UI |
| [0002](adr/0002-harness-adapter-registry.md) | Harness adapter registry — how a new agent CLI gets added |
| [0003](adr/0003-agent-page-view-param.md) | One agent page, view selected by query param |
| [0004](adr/0004-single-agent-shell-in-tab-view-switch.md) | Single agent shell, in-tab view switching |
| [0005](adr/0005-chat-read-only-transcript-viewer.md) | Chat starts read-only |
| [0006](adr/0006-rich-transcript-api.md) | Rich transcript API |
| [0007](adr/0007-chat-session-insights-actions.md) | Session insights + actions in chat |
| [0008](adr/0008-agy-antigravity-harness.md) | Adding the `agy` harness |
| [0009](adr/0009-agy-discovery-source-contract.md) | `agy` discovery source contract |
| [0010](adr/0010-discovery-cache-poll-consolidation.md) | Discovery cache + poll consolidation |
| [0011](adr/0011-agy-transcript-reader.md) | `agy` transcript reader |
| [0012](adr/0012-bulk-session-archive-actions.md) | Bulk session archive actions |
| [0013](adr/0013-analytics-files-endpoint.md) | Analytics + files endpoint |
| [0014](adr/0014-multi-harness-cost-pricing-registry.md) | Multi-harness cost extraction + pricing registry |
| [0015](adr/0015-raw-json-transcript-toggle.md) | Raw-JSON transcript toggle |
| [0016](adr/0016-session-health-score.md) | Session health score — 3-signal graduated heuristic |
| [0017](adr/0017-tool-call-timeline.md) | Tool-call timeline |
| [0018](adr/0018-rate-limit-self-healing.md) | Rate-limit self-healing |
| [0019](adr/0019-prebuilt-dist-for-constrained-hosts.md) | CI builds the UI; constrained hosts fetch the artifact |
| [0020](adr/0020-rename-to-switchboard.md) | Why the project is called Switchboard |

## Frontend architecture

[`design/react-architecture.md`](design/react-architecture.md) — component
tree, state ownership, and where each piece of session state lives.

## What is not here

- **The living HLD.** Current structure, interfaces, and build order are tracked
  in an internal design doc that changes with the code and references planning
  material specific to the authoring workspace. Publishing a half-linked
  snapshot of it would be worse than not publishing it; the root README covers
  the architecture at the level an external reader needs.
- **Superseded v1 records.** The first architecture was task/branch-centric
  rather than session-centric. Its ADRs are coupled to the authoring
  workspace's own tooling and are omitted; ADR-0001 onward describe the design
  that actually shipped.
