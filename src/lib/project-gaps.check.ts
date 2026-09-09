/**
 * One runnable check for the per-project gap analysis (ADR-0040 §SD2).
 *
 * What is worth pinning offline is not the arithmetic — it is the two readings
 * that would be wrong in a way nobody notices: a `✅` row counting as "this
 * role has no work here", and an unreadable role table reading as "no gaps".
 * Run: npm run check:gaps
 */
import { projectGaps } from "./project-gaps";
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
  gaps.rowsPerRole["Tech Lead"] === 1,
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
  gaps.rowsPerRole["Developer"] === 2,
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

console.log("project-gaps check: OK");
