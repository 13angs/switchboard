/**
 * One runnable check for the dispatch prompt (ADR-0030 §SD4). What is checkable
 * offline is not whether the prompt "reads well" — it is that the prompt stays a
 * set of *pointers* and never quietly becomes a copy of the rules, and that a
 * project missing a file does not get told to read one that isn't there.
 * Run: npm run check:dispatch
 */
import {
  composePrompt,
  assignmentId,
  type DispatchRole,
} from "./dispatch-prompt";
import type { WorkspaceProject, WorkspaceSlice } from "./api";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

const slice: WorkspaceSlice = {
  id: "M2",
  title: "เขียนตารางนโยบาย 24 หมวดลงโค้ด",
  day: "อ. 09-08",
  column: "todo",
  note: "โค้ดตรงกับ ADR",
};

const project: WorkspaceProject = {
  name: "ai-chatbot",
  slices: [slice],
  columns: {},
  client: "winona",
  team: "forge",
  default_role: "Product Owner",
  has: { scope: true, risks: true, hld: false },
};

const role: DispatchRole = {
  role: "Developer",
  tier: "standard",
  model: "claude-sonnet-5",
};

// ── the role and its tier are stated, not implied ──
const p = composePrompt(project, slice, role);
assert(p.includes("Developer"), "role named");
assert(
  p.includes("standard"),
  "tier named — the session should know what it runs on",
);

// ── the slice is identified well enough to find it again ──
assert(p.includes("M2"), "slice id present");
assert(p.includes(slice.title), "slice title present");
assert(p.includes("อ. 09-08"), "day present");

// ── pointers, never the rules themselves ──
// The team-os spine is cited by path. If a future edit inlines the definition of
// done, this check is what notices: the prompt would carry the list, not the path.
assert(
  p.includes("team-os/ways-of-working/definition-of-done.md"),
  "DoD cited by path",
);
assert(
  p.includes("team-os/ways-of-working/stuck-rule.md"),
  "stuck rule cited by path",
);
assert(p.includes("team-os/decisions/README.md"), "ADR rule cited by path");
assert(
  !p.includes("เขียนเทสต์"),
  "the DoD items themselves must not be inlined",
);

// ── the one rule short enough to carry is the one everything hangs off ──
assert(p.includes("ย้อนกลับได้"), "the reversible/irreversible rule is stated");
assert(
  p.includes("ADR ก่อนลงมือ"),
  "ADR-before-acting is stated, not left to the file",
);

// ── a project without risks.md is not told to read risks.md ──
const bare: WorkspaceProject = {
  ...project,
  has: { scope: false, risks: false, hld: false },
};
const pb = composePrompt(bare, slice, role);
assert(!pb.includes("scope.md"), "absent scope.md not cited");
assert(!pb.includes("risks.md"), "absent risks.md not cited");
assert(
  pb.includes("slices.md"),
  "slices.md always cited — the board read it to get here",
);

// ── assignment: honest about the segment the board cannot resolve ──
assert(
  assignmentId(project, slice) === "winona/-/forge/m2",
  "id built from frontmatter",
);
assert(p.includes("Assignment: winona/-/forge/m2"), "id reaches the prompt");
assert(
  p.includes("resolve เอง"),
  "the unresolved segment is flagged, not passed off as done",
);

// ── a project with no client/team declared does not invent one ──
const anon: WorkspaceProject = { ...project, client: "", team: "" };
assert(
  assignmentId(anon, slice) === "internal/-/-/m2",
  "unknown owner falls back, not guessed",
);

// ── a slice with no id still produces a usable slug ──
const noId: WorkspaceSlice = { ...slice, id: "—", title: "buffer วันพุธ" };
assert(!assignmentId(project, noId).endsWith("/"), "slug never empty");

console.log("dispatch-prompt check: OK");
