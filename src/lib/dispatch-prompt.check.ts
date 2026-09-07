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
  promptShapeFor,
  dispatchLabel,
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
  role: null,
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
  effort: "medium",
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

// ── ADR-0036 §SD5 — the 🖐️ column dispatches, in a different shape ──
// One resolver decides both the button's word and the prompt behind it. If a
// future edit lets them disagree, the board would promise "เตรียมเรื่อง" and
// hand the session a "go do it" prompt — the exact failure the ADR splits the
// shapes to prevent.
assert(promptShapeFor("owner") === "prepare", "the 🖐️ column prepares");
for (const c of ["running", "next", "todo"])
  assert(promptShapeFor(c) === "act", `${c} still acts`);
assert(dispatchLabel("prepare") === "เตรียมเรื่อง", "prepare button reworded");
assert(dispatchLabel("act") === "สั่งงาน", "the other three keep their word");

const ownerSlice: WorkspaceSlice = {
  ...slice,
  id: "S-07",
  column: "owner",
  note: "PR ของ dependabot ค้าง 2 ใบ",
};
const prep = composePrompt(project, ownerSlice, role, "prepare");

// The ⛔ block names the withheld actions by name. A generic "be careful" line
// would leave the session to guess which half of the row it may touch.
assert(prep.includes("⛔"), "the withheld actions are marked, not implied");
assert(prep.includes("merge PR"), "merge named as withheld");
assert(prep.includes("ลบ branch"), "branch deletion named as withheld");
assert(
  prep.includes("แทนเจ้าของ"),
  "deciding for the owner named as withheld",
);
// (ข) of the ADR: the mark keeps every letter of its meaning — the line it
// draws is under the action, not under the thinking.
assert(
  prep.includes("**การกระทำ**") && prep.includes("**การคิด**"),
  "the mark's line is action-vs-thinking, spelled out",
);
// What that session hands back is options + evidence + one proposal.
assert(prep.includes("ตัวเลือก"), "options asked for");
assert(prep.includes("หลักฐาน"), "evidence asked for");
assert(prep.includes("ข้อเสนอหนึ่งข้อ"), "exactly one proposal asked for");

// ── the two shapes stay two: the act prompt never grows the ⛔ block ──
const act = composePrompt(project, ownerSlice, role, "act");
assert(!act.includes("⛔"), "the act shape carries no withheld-action block");
assert(act.includes("เสร็จเมื่อ"), "the act shape still states done-when");
assert(
  !prep.includes("เสร็จเมื่อ"),
  "prepare does not tell the session to finish the row",
);
assert(
  composePrompt(project, ownerSlice, role) === act,
  "act is the default shape — the existing three columns are untouched",
);

// ── same row, same id: preparing a decision and doing it are one piece of work ──
assert(
  prep.includes(`Assignment: ${assignmentId(project, ownerSlice)}`),
  "prepare carries the row's own id, not a second one",
);

// ── the spine is read in both shapes ──
for (const f of [
  "team-os/ways-of-working/definition-of-done.md",
  "team-os/ways-of-working/stuck-rule.md",
  "projects/ai-chatbot/slices.md",
])
  assert(prep.includes(f), `${f} cited in the prepare shape too`);

console.log("dispatch-prompt check: OK");
