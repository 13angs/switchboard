---
title: "ADR-0020: Rename Agent View → Switchboard — the descriptive-name space is occupied"
type: adr
created: 2026-07-25
status: accepted
project: switchboard
related: "README.md § Status and support"
implementation: "13angs/switchboard#7"
teams: [software-design]
---

# ADR-0020: Rename Agent View → Switchboard

## Context

The repo was about to be made public. A name check before publishing found that
**`agent-view` is already taken, and so is every descriptive alternative in the
category.**

> This is the published copy of the decision. The authoring workspace keeps a
> fuller version that also records the internal planning context; the naming
> decision, evidence, and exclusion list below are identical.

Measured 2026-07-25 via `gh search repos`:

| Name | Occupied by | Their one-liner | Pushed |
| --- | --- | --- | --- |
| `agent-view` | `Frayo44/agent-view` | *(no description)* | 2026-07-18 |
| `agent-viewer` | `hallucinogen/agent-viewer` | Kanban board for managing Claude Code agents in tmux | 2026-07-17 |
| `agent-deck` | `asheshgoplani/agent-deck` | Terminal session manager for AI coding agents. One TUI for Claude, Gemini, OpenCode, Codex | 2026-07-25 |
| `AgentDeck` | `puritysb/AgentDeck` | Physical controller & multi-surface dashboard for AI coding agents | 2026-07-25 |
| `agent-console` | `eqtylab/agent-console` | Live view of Claude Code sessions and the ability to search them | 2026-05-22 |
| `agent-cockpit` | `daronyondem/agent-cockpit` | Run AI assistants from your browser. Keep your data. Switch vendors anytime. | 2026-07-25 |
| — | `AgentsMesh/AgentsMesh` | Run a hundred AI coding agents… steer them all from one console | 2026-07-24 |
| — | `Wirasm/kild` | Developer cockpit for orchestrating coding-agent teams | 2026-07-24 |
| — | `xmanrui/OpenClaw-bot-review` | Lightweight web dashboard for viewing all your Bots/Agents/Models/Sessions | 2026-07-25 |

Roughly **nine direct competitors, most pushed within ten days** of this ADR.
`hallucinogen/agent-viewer` in particular is the same product in a different
shell (tmux TUI instead of a browser board).

Two facts follow, and they point in opposite directions:

1. The category is now commodity. A rename cannot fix that.
2. A *descriptive* name puts this project visibly inside the lookalike pile —
   `agent-{view,viewer,deck,console,cockpit}` are indistinguishable at a glance.

The second is fixable, and it is the one that matters for the reason the repo is
going public at all: it is a portfolio artifact. Its value is that it was built
and that the design record exists, not that the niche is unclaimed.

## Decision — rename to `switchboard`

`13angs/switchboard`, product name **Switchboard**. `gh search repos
"switchboard session"` returns **zero** results; there is no collision in this
space.

The name was chosen for scope fidelity, not flavour. A switchboard operator
patches connections between many lines and **does not join any call** — which is
the boundary every ADR in this series has defended: *"observes and launches; it
does not merge PRs"*. The project's vocabulary rules already ban `task driver`, `fan-out coordinator`, and
`auto-merge`; a name implying orchestration or autonomy would have contradicted
the accepted architecture. This is why `agent-cockpit`-shaped names were
rejected even where available.

### Options considered

| Option | Mechanism | Rejected because |
| --- | --- | --- |
| **A. Keep `agent-view`** | zero cost | Collides with two near-identical projects; the name also caps perceived scope at read-only, while the app writes to PTYs, spawns sessions, and scores health |
| **B. Another `agent-X`** | `agent-deck` / `agent-console` / `agent-cockpit` | Pays the full rename cost **and still lands in the lookalike pile** — `agent-deck` collides head-on with a same-day TUI doing the same job. Worst of both |
| **C. `pitwall`** | F1 pit wall — engineers read telemetry from many cars, call strategy, do not drive | Good metaphor, free on `13angs`, but collides across motorsport (`kikkia/pitwall.me`, `Swizzjack/lmu-pitwall`, `domhnallmorr/Pitwall_Flet`) so search discovery lands on F1 tooling |
| **D. `switchboard`** ✅ | human operator, many lines, patches connections, joins none | — |

Timing was part of the decision: the repo was still private with 0 stars, 0
forks, 8 commits, and no inbound links. That is the cheapest this rename will
ever be, and it had to happen before the profile README and CV started citing a
URL.

### What was deliberately not renamed

- `src/components/shared/Topbar.tsx` `aria-label="Agent view"` and
  `react-architecture.md`'s *"Agent view (`terminal`/`chat`/`files`)"* — these
  name the **agent page's view switcher**, not the product. A case-insensitive
  sweep would have silently corrupted an accessibility label.
- `meta/daily/*.md` — dated day records. Rewriting them would make a
  2026-07-21 entry claim a name that did not exist until 07-25.
- `research/agent-view-comparable-systems/` — folder *and* contents. It is a
  2026-07-23 snapshot bound to a real NotebookLM notebook titled
  `RESEARCH-agent-view-comparable-systems-2026-07-07`
  (`sources/sources-q7.json`); renaming breaks that provenance. A banner in
  `report.md` points here instead.
- ADR-0019's quoted commit subject `bb1beeb initial agent-view split` — quoting
  a commit means quoting what git actually records.

## Consequences

- **Positive:** a name with no competitor collision, and one whose metaphor
  states the architectural boundary instead of overselling past it.
- **Positive:** GitHub redirects the old URL and carried PR #7 across, so
  existing clones, remotes, and PR links keep resolving.
- **Negative:** a non-descriptive name loses keyword discovery — nobody
  searching *"claude code session manager"* finds `switchboard`. Mitigation is
  the repo description plus topics, which is what they are for; this is an
  accepted trade for a portfolio artifact whose visitors arrive from a profile,
  not from search.
- **Negative:** `AGENT_VIEW_REPO` → `SWITCHBOARD_REPO` is a breaking rename for
  anyone who exported the old variable. Author-only today.
- **Risk:** "switchboard" already carries an unrelated meaning in the authoring
  workspace's own documentation. Different domain; accepted.
- **Risk:** `control_plane/config.py` still carries a workspace-shaped `.env`
  fallback (`projects/switchboard/repos/switchboard/.env`). Renamed for
  consistency, not redesigned — genericizing that lookup is a separate change.

## Verification

- `python3 -m pytest tests/ -q` → **125 passed** after the app-repo sweep
- `package.json` / `package-lock.json` / `pricing.json` parse clean;
  `bash -n scripts/fetch-dist.sh` ok
- workspace sweep: 37 living-doc files rewritten; remaining `agent-view`
  matches are only the intentional exclusions listed above
- markdown link check across the workspace: the rename introduced **no** new
  broken relative links (the 9 pre-existing ones under `projects/*/docs/` are
  wrong-depth `../docs/sops/` paths that predate this change and resolve
  identically before and after, since folder depth is unchanged)
