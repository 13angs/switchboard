/**
 * One runnable check for the per-project gap analysis (ADR-0040 §SD2).
 *
 * What is worth pinning offline is not the arithmetic — it is the two readings
 * that would be wrong in a way nobody notices: a `✅` row counting as "this
 * role has no work here", and an unreadable role table reading as "no gaps".
 * Run: npm run check:gaps
 */
import {
  projectGaps,
  projectRoleGaps,
  rowsPerRole,
  roleSlug,
  normalizeLocation,
} from "./project-gaps";
import type { WorkspaceProject, WorkspaceResponse } from "./api";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

const row = (id: string, column: string, role: string | null) => ({
  id,
  title: id,
  day: "",
  column,
  note: "",
  role,
  blocked_by: [],
  handoff: null,
});

const project: WorkspaceProject = {
  name: "demo",
  slices: [
    row("D1", "done", "Tech Lead"),
    row("D2", "todo", "Developer"),
    row("D3", "owner", "Developer"),
    row("D4", "todo", null),
  ],
  columns: {},
  client: "internal",
  team: "dev",
  default_role: "Developer",
  has: { scope: true, slices: true, risks: false, rollout: false, retro: false, hld: true },
};

const dispatch: Pick<WorkspaceResponse, "dispatch"> = {
  dispatch: {
    present: true,
    tiers: { heavy: "claude-opus-5", standard: "claude-sonnet-5", light: "claude-haiku-4-5" },
    roles: [
      { role: "CTO", tier: "heavy", model: "claude-opus-5", effort: "xhigh" },
      { role: "Tech Lead", tier: "heavy", model: "claude-opus-5", effort: "high" },
      { role: "Developer", tier: "standard", model: "claude-sonnet-5", effort: "medium" },
      { role: "QA", tier: "standard", model: "claude-sonnet-5", effort: "high" },
    ],
    source: { tiers: "sop", roles: "roles" },
  },
};

const gaps = projectGaps(project, dispatch);

// ── the slot axis follows `has`, in declared order ──
assert(
  gaps.missingSlots.join(",") === "risks,rollout,retro",
  "missing slots come out in the order the template declares them",
);
assert(
  gaps.presentSlots.join(",") === "scope,slices,hld",
  "present slots are listed too — the panel is a checklist, not a scold",
);

// ── a `✅` row still means the role has work here ──
assert(
  gaps.rowsPerRole[roleSlug("Tech Lead")] === 1,
  "a done row counts: the question is 'ever had work', not 'open now'",
);
assert(
  !gaps.rolesWithoutRows.includes("Tech Lead"),
  "a role whose only row is finished is NOT a gap (ADR-0040 §SD2)",
);
assert(
  gaps.rolesWithoutRows.join(",") === "CTO,QA",
  "only roles with no row at all are gaps, in roles.md order",
);

// ── a row with no resolved role belongs to nobody, and inflates nobody ──
assert(
  gaps.rowsPerRole[roleSlug("Developer")] === 2,
  "the null-role row is not silently credited to the project's default",
);

// ── the badge is the sum of both axes, and one function feeds badge + panel ──
assert(
  gaps.count === gaps.missingSlots.length + gaps.rolesWithoutRows.length,
  "the number on the button is exactly what the panel lists",
);
assert(gaps.count === 5, "3 slots + 2 roles");

// ── an unreadable role table is not zero gaps ──
const dark = projectGaps(project, {
  dispatch: { present: false, reason: "no tier map" },
});
assert(dark.rolesUnknown, "the role axis reports itself unavailable");
assert(
  dark.rolesWithoutRows.length === 0 && dark.count === dark.missingSlots.length,
  "an unavailable axis contributes nothing rather than inventing zeros",
);
assert(
  dark.missingSlots.length === 3,
  "the slot axis keeps working when the role axis cannot",
);

// ── the counter behind both screens is one function (ADR-0041 §SD5) ──
const second: WorkspaceProject = {
  ...project,
  name: "other",
  slices: [row("O1", "todo", "QA"), row("O2", "done", "Tech Lead")],
};
const across = rowsPerRole([project, second]);
assert(
  across[roleSlug("Tech Lead")] === 2 && across[roleSlug("QA")] === 1,
  "the workspace-wide count is the per-project count summed, not a second parser",
);
assert(
  projectGaps(project, dispatch).rowsPerRole[roleSlug("Tech Lead")] === 1,
  "and the single-project call still answers for that project alone",
);
assert(
  Object.keys(across).every((k) => k === roleSlug(k)),
  "keys are slugs, so they join against /roles/activity rows without a second map",
);

// ── the collapse onto the role (ADR-0042) ──

const slots: WorkspaceResponse["slots"] = {
  source: "declared",
  reason: "",
  slots: [
    { key: "scope", kind: "file", where: "scope.md" },
    { key: "slices", kind: "file", where: "slices.md" },
    { key: "risks", kind: "file", where: "risks.md" },
    { key: "rollout", kind: "file", where: "rollout.md" },
    { key: "retro", kind: "file", where: "retro.md" },
    { key: "hld", kind: "dir", where: "docs/design" },
  ],
  unmapped: [],
};

const target = (token: string, level: "project" | "workspace", have: number, total: number | null) =>
  ({ token, level, have, total });

const pipeline: WorkspaceResponse["pipeline"] = {
  source: "docs/sops/sop-pipeline-handoff.md",
  stations: { present: true, reason: "", stages: [], per_role: {} },
  signatures: {
    present: true,
    reason: "",
    projects: 33,
    enforcement: {
      declared: true,
      roles: [],
      mechanism: "",
      mechanism_exists: false,
      enforced: 0,
      unenforced: 0,
      total: 0,
    },
    rows: [
      {
        role: "tech-lead",
        closes: "living HLD",
        // The one token whose two registers spell it differently.
        targets: [target("docs/design/*", "project", 6, 33)],
        note: "",
        rejected: [],
      },
      {
        role: "cto",
        closes: "no arch/security debt",
        targets: [target("meta/adr-*.md", "workspace", 16, null)],
        note: "",
        rejected: [],
      },
      {
        role: "qa",
        closes: "project-level pass",
        targets: [],
        note: "thread ของ PR ใบนี้",
        rejected: [],
      },
      {
        role: "developer",
        closes: "commit body",
        targets: [target("rollout.md", "project", 0, 33)],
        note: "",
        rejected: [],
      },
    ],
  },
};

const full = { dispatch: dispatch.dispatch, slots, pipeline };
const belt = projectRoleGaps(project, full);
const by = (slug: string) => belt.roles.find((r) => r.slug === slug)!;

assert(
  normalizeLocation("docs/design/*") === normalizeLocation("docs/design"),
  "the join key normalises a glob and a bare directory to the same string",
);
assert(
  by("tech-lead").surfaces[0].slot === "hld",
  "`docs/design/*` in §7.2 joins to the `hld` slot — the whole point of §SD1",
);
assert(
  by("tech-lead").surfaces[0].present === true &&
    by("tech-lead").surfaces[0].joinKey === "docs/design",
  "and it is answered with *this* project's file, with the join key printable",
);

// ── one file, one place on screen ──
const shown = belt.roles.flatMap((r) => r.surfaces.map((s) => s.token));
assert(
  shown.length === new Set(shown).size,
  "no file is printed twice — a concatenation of the two axes would",
);
assert(
  belt.ownerless.map((s) => s.key).join(",") === "scope,slices,risks,retro",
  "a slot no §7.2 line claims stays in its own section (§SD2), never filed under a near role",
);
assert(
  by("developer").missing.join(",") === "rollout",
  "a role with rows but no signing file is still a gap — invisible on the old axes",
);
assert(by("developer").rows === 2 && by("developer").gap, "…and it has rows");

// ── a workspace-level target is ◐, not ✗ (§SD3) ──
assert(
  by("cto").surfaces[0].level === "workspace" && by("cto").surfaces[0].present === null,
  "`meta/adr-*.md` has nothing under projects/<name>/ to measure",
);
assert(
  by("cto").missing.length === 0,
  "so it can never be reported as this project's missing file",
);
assert(by("cto").gap, "cto is still a gap here — but only because it has no rows");

// ── every role stays on screen, in roles.md order ──
assert(
  belt.roles.map((r) => r.slug).slice(0, 4).join(",") === "cto,tech-lead,developer,qa",
  "roles.md order, and a role with nothing here is the point — never filtered out",
);
assert(
  by("developer").known && by("developer").role === "Developer",
  "the display name from roles.md wins over the signature table's slug",
);

// ── the badge counts what the panel lists, once ──
assert(
  belt.count ===
    belt.roles.filter((r) => r.gap).length +
      belt.ownerless.filter((s) => !s.present).length,
  "the number on the button is exactly what the panel lists",
);
assert(belt.count === 5, "3 roles with a gap + risks/retro with no owner");
assert(
  projectGaps(project, dispatch).count === 5,
  "same total as the two lists here — but `rollout` moved under `developer` " +
    "instead of standing alone, which is what stops it printing twice",
);

// ── §7.2 unreadable: the file axis goes dark, the role axis does not (§SD6) ──
const noSig = projectRoleGaps(project, {
  ...full,
  pipeline: {
    ...pipeline,
    signatures: { ...pipeline.signatures, present: false, reason: "no table", rows: [] },
  },
});
assert(
  noSig.ownerless.length === slots.slots.length,
  "with no owners declared, every slot lands in the ownerless section",
);
assert(
  noSig.roles.length === belt.roles.length &&
    noSig.roles.every((r) => r.surfaces.length === 0),
  "the role rows survive — 'has this project any row for you' needs no SOP",
);
assert(noSig.count === 5, "2 roles with no row + risks/rollout/retro with no owner");
assert(
  noSig.roles.find((r) => r.slug === "cto")!.gap,
  "and they still report gaps from the row count alone",
);

// ── dispatch unreadable: no table, no buttons (§SD6) ──
const noRoles = projectRoleGaps(project, {
  ...full,
  dispatch: { present: false, reason: "no tier map" },
});
assert(noRoles.rolesUnknown, "the panel says which half it lost");

console.log("project-gaps check: OK");
