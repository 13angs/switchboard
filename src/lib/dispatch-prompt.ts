/**
 * Composing the prompt a dispatched session opens with (ADR-0030 §SD4).
 *
 * The shape follows team-os, not this repo's old launcher: a role, the slice it
 * owns, and *pointers* to the files that hold the rules. Never the rules
 * themselves — `team/README.md § Core principle` is "reference, never copy",
 * and a prompt that inlines a rule becomes a second copy of it that drifts.
 *
 * Two shapes live here (ADR-0036 §SD5): `act` for a row that is waiting on
 * work, and `prepare` for a row the owner has marked 🖐️ — same spine, same
 * sentences, different job. Splitting them is the whole point of the ADR: one
 * button that meant two things would be worse than the closed door it replaced.
 *
 * A third shape, `composeRitualPrompt`, dispatches a *ritual* rather than a
 * slice (ADR-0036 §SD4). It lives in this module rather than a new file because
 * it is the same spine and the same sentences over a different subject — and
 * because one file is one place to check that none of the three ever starts
 * inlining the rules it points at.
 *
 * A fourth shape, `composeGrillPrompt` (ADR-0037), dispatches a *grill* — a
 * project with no settled row yet. It differs from the other three in the one
 * way ADR-0037 §SD1 spells out: its role is the literal string `forge`, never
 * resolved through the 7-role table, because the row that would carry a role
 * does not exist until the grill session writes it.
 *
 * `composeFillGapsPrompt` (ADR-0040) is not a fifth shape — it is a second
 * entry point to the fourth. Same role, same tier picker, same new tab, same
 * "PR touching only slices.md" contract; the only difference is that the grill
 * arrives with an agenda that has already been measured.
 *
 * Since ADR-0047 the `act` shape carries one more block: the session, not the
 * owner, is the one who presses the card onward when its station's work is done
 * (§SD1). It keeps the "pointers, never the rules" discipline the hard way —
 * the block does not print the transition table, it tells the session to ASK
 * the gate (`GET /work/transitions`) and press what the gate returns.
 *
 * Kept in pure functions so they are checkable offline (dispatch-prompt.check.ts)
 * and so the operator previews the exact text that will be typed, not an
 * approximation of it.
 */
import type { Ritual, WorkspaceProject, WorkspaceSlice } from "./api";
import { roleSlug } from "./project-gaps";

export interface DispatchRole {
  role: string;
  /** The role's office, off the same roles.md row as its tier (S37). `""`
   *  when § แกนความเป็นเจ้าของ does not carry this role — the id then prints
   *  the convention's `-` rather than inventing an office. */
  office: string;
  tier: string;
  model: string;
  /** ADR-0032 — omitted (null) when roles.md carries no effort column, or
   *  the tier is `light`, which rejects `--effort` outright. */
  effort: string | null;
}

/**
 * Which prompt a card opens with — and, at the same time, which word its button
 * wears (ADR-0036 §SD5).
 *
 * `owner` is the 🖐️ column. It became dispatchable without the mark losing a
 * letter of its meaning: what 🖐️ draws a line under is the *action*, not the
 * *thinking*, so that session prepares the decision and stops. Every other
 * column keeps the original "go do it" shape. Resolved in one place so the
 * button's word and the prompt behind it can never say two different things.
 */
export type PromptShape = "act" | "prepare";

export function promptShapeFor(column: string): PromptShape {
  return column === "owner" ? "prepare" : "act";
}

/** The word on the card's button. Not a tooltip: the owner reads this board on
 *  a tablet, where hover does not exist (the lesson S9 already paid for). */
export function dispatchLabel(shape: PromptShape): string {
  return shape === "prepare" ? "เตรียมเรื่อง" : "สั่งงาน";
}

/** Files every dispatched role reads before starting — the team-os spine. */
const SPINE = [
  'team-os/ways-of-working/definition-of-done.md — "เสร็จ" แปลว่าอะไร',
  "team-os/ways-of-working/stuck-rule.md — ติดแล้วทำยังไง",
  "team-os/decisions/README.md § กฎการเขียน ADR",
];

/** The three rules short enough to carry rather than cite — everything else in
 *  team-os hangs off them, and a session that has not read a file yet still has
 *  to know when to stop. Shared by every prompt shape so they cannot drift. */
const CENTRAL_RULES = [
  "**ย้อนกลับได้ → ตัดสินเอง · ย้อนกลับไม่ได้ → เขียน ADR ก่อนลงมือ** (ไม่ใช่เขียนย้อนหลัง)",
  "ไม่แน่ใจว่าย้อนกลับได้ไหม → ถือว่าย้อนกลับไม่ได้ แล้วถาม",
  "ติด 3 รอบแล้วไม่ขยับ → หยุด แล้วโพสต์ 3 บรรทัด (ติด: / ลองแล้ว: / ต้องการ:)",
];

/**
 * The assignment id, as far as the board can honestly resolve it.
 *
 * `client` comes from the project's own slices.md frontmatter. The other two
 * segments come from the **role that is being dispatched** (S37): its slug is
 * one of the seven `roles.md` declares, and its office rides the same row.
 * Before S37 this printed the file's `team:` — a *discipline* slug such as
 * `arch` — into the role segment and `-` into the office, and
 * `.githooks/commit-msg` rejected every commit that carried it (measured
 * 2026-09-11 on `W17`: `internal/-/arch/w17`, refused on both segments).
 *
 * Passing no actor is still valid and still honest: both segments fall back to
 * `-`, the convention's own "not resolved, nothing was read" marker. Callers
 * that have a picked role should pass it — a guessed office misfiles work
 * where `git log --grep '^Assignment: <client>/'` will never find it again.
 *
 * The shape of the prompt does not change the id: preparing a decision on a row
 * and doing the row are the same piece of work, and `git log --grep` has to
 * find both under one slug.
 */
export function assignmentId(
  project: WorkspaceProject,
  slice: WorkspaceSlice,
  actor?: DispatchRole | null,
): string {
  const client = project.client || "internal";
  const office = actor?.office || "-";
  // The tier table writes `Senior Developer`; the id — and every other surface
  // that joins these two halves — uses the slug.
  const role = actor?.role ? roleSlug(actor.role) : "-";
  const slug = (slice.id !== "—" && slice.id ? slice.id : slice.title)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40);
  return `${client}/${office}/${role}/${slug || "untitled"}`;
}

/**
 * The onward press, handed to the session that is about to do the work
 * (ADR-0047 §SD1).
 *
 * Three things this deliberately does NOT do:
 *
 *   - It does not name the next station. `GET /work/transitions` answers that
 *     for the row's *current* stage and the acting role, out of the same
 *     `evaluate()` the POST calls — so the prompt cannot hold a stale copy of
 *     `row-status.md § ตารางการส่งต่อ` (ADR-0044 §SD5). A prompt that named the
 *     station would also be wrong the first time a row is dispatched from a
 *     station its author did not have in mind.
 *   - It does not soften a refusal. 403/409 is the gate's answer, and the one
 *     failure mode worth naming out loud is the one the endpoint cannot catch:
 *     it trusts the `role` in the body, so a session that re-fires as another
 *     role walks straight through a door that was shut for it.
 *   - It does not appear on a row with nothing to press — no id, or no `stage`
 *     (the belt is opt-in per row, and `candidates()` returns [] for a row that
 *     never joined it).
 *
 * `boardOrigin` comes from the browser that composed the prompt. Empty means
 * the caller could not resolve it, and the block says so with a placeholder
 * rather than inventing a port — the same honesty the `-` segments of an
 * assignment id are built on.
 */
function handoffBlock(
  project: WorkspaceProject,
  slice: WorkspaceSlice,
  role: DispatchRole,
  boardOrigin: string,
): string[] {
  if (!slice.id || slice.id === "—") return [];
  if (!slice.stage) return [];

  const origin = boardOrigin || "<ที่อยู่ของบอร์ด>";
  const actor = roleSlug(role.role);
  const q = new URLSearchParams({
    project: project.name,
    slice_id: slice.id,
    role: actor,
  }).toString();
  const body = JSON.stringify({
    project: project.name,
    slice_id: slice.id,
    to_stage: "<to_stage ที่ข้อ 1 ตอบว่า allowed>",
    role: actor,
    office: role.office || "-",
    client: project.client || "internal",
    form: {},
  });

  return [
    "เมื่องานของสถานีนี้จบ — **กดส่งต่อเอง ไม่ต้องรอเจ้าของคลิก** (ADR-0047 §SD1):",
    `1. ถามด่านว่าเส้นไหนเปิดให้ \`${actor}\` — อย่าอ่านตารางมาตัดสินเอง:`,
    `   \`curl -fsS '${origin}/work/transitions?${q}'\``,
    "2. ยิงเส้นที่ตอบว่า `allowed: true`:",
    `   \`curl -fsS -X POST '${origin}/work/transition' -H 'Content-Type: application/json' -d '${body}'\``,
    "   เส้นที่ `requires` บอกว่าต้องมีฟอร์ม ให้เติมค่าจริงลง `form` — ข้อความไปอยู่ใน body ของ commit ไม่ใช่ในเซลล์ (ADR-0046 §SD2)",
    "- ถูกปฏิเสธ (403/409) คือ **คำตอบ** ไม่ใช่สิ่งกีดขวาง — `reason` บอกว่าอะไรยังไม่ผ่าน · **ห้ามยิงซ้ำด้วย role อื่นเพื่อให้ผ่าน** ด่านเชื่อ `role` ที่คุณบอกมัน",
    "- **การกดที่ 1 ไม่ใช่ของคุณ** — `readydev → inprogress` เป็นของคนที่สั่งงาน (ADR-0047 §SD5)",
    "- `deployed → done` คือ merge · diff ที่ชน stop-list จะได้คำตอบว่า *รอเจ้าของเคาะ* — นั่นคือจุดจบของงานคุณ ไม่ใช่เหตุให้หาทางอื่น (ADR-0047 §SD3)",
    "",
  ];
}

export function composePrompt(
  project: WorkspaceProject,
  slice: WorkspaceSlice,
  role: DispatchRole,
  shape: PromptShape = "act",
  boardOrigin = "",
): string {
  const lines: string[] = [];

  lines.push(
    `คุณรับบทบาท **${role.role}** ของ team-os (รันบน tier \`${role.tier}\`)`,
  );
  lines.push("");

  const head = `${slice.id !== "—" ? `${slice.id} · ` : ""}${slice.title}`;
  const day = slice.day ? ` (${slice.day})` : "";

  if (shape === "prepare") {
    lines.push(`เรื่องที่รอ **เจ้าของเคาะ**: **${head}**${day}`);
    if (slice.note) lines.push(`บริบทของแถว: ${slice.note}`);
    lines.push("");
    lines.push(
      "คุณไม่ได้ถูกเรียกมาลงมือ — มาเตรียมเรื่องให้เคาะได้ · ส่งมอบสามอย่าง:",
    );
    lines.push(
      "1. **ตัวเลือกที่มีจริง** — พร้อมสิ่งที่ต้องแลกของแต่ละข้อ ไม่ใช่รายการที่ดูดีทุกข้อ",
    );
    lines.push(
      "2. **หลักฐาน** — ไฟล์ที่เปิดหรือคำสั่งที่รันเองในเซสชันนี้ · ของที่ไม่ได้เปิดให้เขียนว่ายังไม่ได้ตรวจ",
    );
    lines.push(
      "3. **ข้อเสนอหนึ่งข้อ** — เลือกมาทางเดียวพร้อมเหตุผล · เสนอไม่ได้ให้บอกว่า **ข้อมูลไหนที่ขาด** ไม่ใช่ยื่นเมนูเปล่า",
    );
  } else {
    lines.push(`งาน: **${head}**${day}`);
    if (slice.note) lines.push(`เสร็จเมื่อ: ${slice.note}`);
  }
  lines.push("");

  lines.push("อ่านก่อนเริ่ม — อย่าเดาจากชื่อไฟล์:");
  lines.push(`- projects/${project.name}/slices.md — แถวของงานนี้`);
  if (project.has.scope)
    lines.push(`- projects/${project.name}/scope.md — อะไรอยู่นอกขอบเขต`);
  if (project.has.risks)
    lines.push(`- projects/${project.name}/risks.md — ความเสี่ยงที่เปิดอยู่`);
  for (const s of SPINE) lines.push(`- ${s}`);
  lines.push("");

  lines.push("กฎกลางของ team-os:");
  for (const r of CENTRAL_RULES) lines.push(`- ${r}`);
  lines.push("");

  if (shape === "prepare") {
    // The ⛔ block names the actions the 🖐️ mark holds back, by name. A generic
    // "be careful" line would leave the session to guess which half of the row
    // it may touch — and guessing is what the mark exists to stop.
    lines.push("⛔ สามอย่างนี้แถวนี้กันไว้ให้เจ้าของ — เซสชันนี้ทำแทนไม่ได้:");
    lines.push(
      "- **merge PR** — เปิดใบใหม่ได้ กดรวมไม่ได้ · ไฟเขียวเป็นราย PR ของเจ้าของเท่านั้น",
    );
    lines.push("- **ลบ branch · ปิด PR ของใบที่ค้างอยู่**");
    lines.push(
      "- **เคาะคำถามขององค์กรแทนเจ้าของ** — ราคา · ขอบเขตสัญญา · ลำดับความสำคัญข้ามโปรเจกต์ · นิสัยการทำงานที่ต้องเปลี่ยนเอง",
    );
    lines.push(
      "เส้นที่เครื่องหมาย 🖐️ ขีดคือ **การกระทำ** ไม่ใช่ **การคิด** — อ่าน วิเคราะห์ ร่าง เสนอ ได้เต็มที่ · ไม่แน่ใจว่าข้อไหนนับเป็นการกระทำ → ถือว่าใช่ แล้วถาม",
    );
    lines.push("");
  }

  // Only the `act` shape presses. A 🖐️ row is dispatched to *prepare* a
  // decision and stop (ADR-0036 §SD5) — handing it the onward press would undo
  // the line that mark draws.
  if (shape === "act") {
    for (const l of handoffBlock(project, slice, role, boardOrigin)) lines.push(l);
  }

  const id = assignmentId(project, slice, role);
  lines.push(`Assignment: ${id}`);
  // Only when a segment really did not resolve. Printing the caveat next to a
  // complete id taught the operator to edit an id that was already correct.
  if (id.includes("/-/")) {
    lines.push(
      "(ช่องที่เป็น `-` คือช่องที่บอร์ดอ่านไม่ได้ — resolve เองจาก docs/sops/sop-work-ownership.md ก่อนคอมมิต)",
    );
  }

  return lines.join("\n");
}


/**
 * The prompt a calendar bar opens with (ADR-0036 §SD4).
 *
 * A ritual is not a project, so `composePrompt`'s (project, slice, role) shape
 * does not fit — but the spine, the three central rules and the "pointers, never
 * the rules" discipline are identical, so this is a sibling here rather than a
 * second module.
 *
 * `ritual.reads` is the register's own definition pointer (a runbook and its
 * step numbers). It is cited, never unrolled: copying the steps into the prompt
 * would make the prompt a second copy of the runbook, which is exactly what
 * `team/README.md § Core principle` forbids.
 *
 * The id carries all four segments — the office resolves out of roles.md, so
 * there is nothing here for the session to fill in — and carries no date: it
 * names the *ritual*, not today's run of it, so `git log --grep` returns the
 * whole series (rituals.md § อ่านตารางนี้ยังไง, rule 1).
 */
export function composeRitualPrompt(
  ritual: Ritual,
  role: DispatchRole,
  when: { date: string; start: string; end: string },
): string {
  const lines: string[] = [];

  lines.push(
    `คุณรับบทบาท **${role.role}** ของ team-os (รันบน tier \`${role.tier}\`)`,
  );
  lines.push("");

  lines.push(
    `จังหวะประจำวัน: **${ritual.name}** — ${when.date} · ${when.start}–${when.end}`,
  );
  lines.push(
    "เสร็จเมื่อ: เดินจังหวะนี้จบตามนิยามของมัน แล้วบันทึกผลไว้ที่ที่จังหวะนั้นบันทึก",
  );
  lines.push("");

  lines.push("อ่านก่อนเริ่ม — อย่าเดาจากชื่อไฟล์:");
  if (ritual.reads) {
    lines.push(`- ${ritual.reads} — นิยามของจังหวะนี้ + ขั้นที่ต้องรัน`);
  } else {
    lines.push(
      "- ⚠️ ทะเบียนไม่ได้ชี้ว่านิยามของจังหวะนี้อยู่ไฟล์ไหน — หาให้เจอก่อนลงมือ อย่าเดาขั้นตอนเอง",
    );
  }
  lines.push(
    "- team-os/ways-of-working/rituals.md § เจ้าของของแต่ละจังหวะ — แถวของจังหวะนี้",
  );
  for (const f of SPINE) lines.push(`- ${f}`);
  lines.push("");

  lines.push("กฎกลางของ team-os:");
  for (const r of CENTRAL_RULES) lines.push(`- ${r}`);
  lines.push("");

  // The one rule this surface can break by accident: a ritual session works
  // inside meta/daily/*, where "record what happened" and "change what the day
  // committed to" look like the same edit. Cited, not unrolled.
  lines.push(
    "⛔ **แผนของวันเป็นข้อผูกพัน ไม่ใช่กระดาษทด** — บันทึกสิ่งที่เกิดขึ้นแล้วได้เสมอ · แต่แถวใน `§ ⏱️ ตารางเวลา` · การจัดสรรของ Focus · `plan.md` เปลี่ยนไม่ได้ถ้าไม่มีคำอนุมัติของเจ้าของสำหรับการเปลี่ยนนั้น (CLAUDE.md § Always-On Safety Net)",
  );
  lines.push("");

  lines.push(`Assignment: ${ritual.assignment}`);
  lines.push(
    "(id ระบุ *จังหวะ* ไม่ใช่รอบของวันนี้ — ห้ามต่อท้ายวันที่ ไม่งั้นตัดซีรีส์ของตัวเองใน `git log --grep`)",
  );

  return lines.join("\n");
}

/**
 * The prompt a "grill slices" button opens with (ADR-0037 §SD1–§SD5).
 *
 * Grill is not "act" on an existing row and not "prepare" a decision on one —
 * it runs *before* a row exists, to produce one. That is why `role` never goes
 * through `_default_role_for_team()` on the caller's side either: `forge` is
 * written straight into the prompt and into the `DispatchRole` the caller
 * builds from the picker (ADR-0037 §SD1), not resolved from `dispatch.roles`,
 * because there is no card yet to resolve a role from.
 *
 * `role.tier`/`role.model`/`role.effort` come from the two dropdowns ADR-0037
 * §SD2 puts in the dialog instead of a role list — the caller is responsible
 * for defaulting them to heavy/high and reading the model id off the same
 * `dispatch.tiers` map the 7-role table uses, so this function stays as
 * ignorant of *where the tier came from* as `composePrompt` already is.
 */
export function composeGrillPrompt(
  project: WorkspaceProject,
  role: DispatchRole,
): string {
  const lines: string[] = [];

  lines.push(
    `คุณรับบทบาท **${role.role}** ของ team-os (รันบน tier \`${role.tier}\`)`,
  );
  lines.push("");

  lines.push(
    `งาน: **กริล** เจ้าของเรื่องที่ยังไม่ตกผลึกเป็นแถว ให้กลายเป็นแถวใหม่ใน **projects/${project.name}/slices.md**`,
  );
  lines.push(
    "เสร็จเมื่อ: สัมภาษณ์จนตกผลึกครบ (grill-me) แล้วเปิด PR ที่แก้เฉพาะไฟล์นั้น — **ไม่ implement เอง** ในเซสชันนี้",
  );
  lines.push("");

  lines.push("อ่านก่อนเริ่ม — อย่าเดาจากชื่อไฟล์:");
  lines.push(
    `- projects/${project.name}/slices.md — โครง + กฎการเขียนแถวของไฟล์นี้`,
  );
  if (project.has.scope)
    lines.push(`- projects/${project.name}/scope.md — อะไรอยู่นอกขอบเขต`);
  if (project.has.risks)
    lines.push(`- projects/${project.name}/risks.md — ความเสี่ยงที่เปิดอยู่`);
  lines.push(
    "- docs/sops/sop-forge-planning.md — ขั้นตอน elicit → structure → grill → finalize",
  );
  lines.push("- tools/grill-me/grill-ruleset.md — วิธี grill-me");
  lines.push(
    "- docs/sops/sop-work-ownership.md § Team-Slug-Approved — วิธีเขียน `Assignment:`/`Team-Slug-Approved:` ของเซสชันนี้",
  );
  for (const s of SPINE) lines.push(`- ${s}`);
  lines.push("");

  lines.push("กฎกลางของ team-os:");
  for (const r of CENTRAL_RULES) lines.push(`- ${r}`);
  lines.push("");

  lines.push("⛔ ขอบเขตของเซสชันนี้ — เซสชันนี้ทำแทนไม่ได้:");
  lines.push(
    `- **แก้ได้เฉพาะ \`slices.md\` ของ ${project.name}** — ห้ามแตะโค้ดหรือไฟล์อื่นในเซสชันนี้`,
  );
  lines.push(
    "- **ไม่ implement เอง** — ผลลัพธ์ของเซสชันนี้คือแถวที่ตกผลึกแล้ว ไม่ใช่โค้ด",
  );
  lines.push(
    "- **ไม่มีทางลัด** — เปิด worktree → PR → หยุดรอเจ้าของไฟเขียว เหมือนงานเขียนไฟล์อื่นทุกเส้นทาง",
  );
  lines.push("");

  const client = project.client || "internal";
  const today = new Date().toISOString().slice(0, 10);
  lines.push(
    `Assignment: ${client}/-/${role.role}/<task-slug-ที่ตกผลึกได้ตอนจบ>`,
  );
  lines.push(
    "(ช่อง office เป็น `-` เพราะบอร์ดอ่านไม่ได้ — resolve เองจาก docs/sops/sop-work-ownership.md ก่อนคอมมิต · ช่อง task-slug ยังไม่มีเพราะแถวยังไม่ตกผลึก — ตั้งเองตอนจบ ห้ามเว้นว่าง)",
  );
  lines.push(`Team-Slug-Approved: Don ${today} — grill session, no role owns elicitation yet`);
  lines.push(
    "(การกดปุ่ม grill ของเจ้าของคือ green light รายครั้งสำหรับ trailer นี้ — ADR-0037 §SD1)",
  );

  return lines.join("\n");
}

/**
 * The prompt the "เติมช่องที่ขาด" button opens with (ADR-0040 §SD1).
 *
 * Everything mechanical is `composeGrillPrompt`'s: role `forge` written
 * straight in, the tier from the dialog's two dropdowns, a PR that touches only
 * this project's `slices.md`, and the `Team-Slug-Approved:` trailer ADR-0037
 * §SD1 established. What this adds is the agenda — the roles with no row at all
 * and the template slots with no file — measured by the board rather than
 * re-derived by the session.
 *
 * The ⛔ block is the part that matters most. A gap is *evidence*, not an
 * instruction: `platform-core` may genuinely have no QA work, and turning six
 * empty slots into six rows would produce a tidy file that lies. So the session
 * is told to grill first and to write down why a slot stays empty — which is
 * team-os/README.md's own rule 3, "ช่องที่ว่างต้องเขียนว่าว่าง".
 */
export function composeFillGapsPrompt(
  project: WorkspaceProject,
  gaps: { missingSlots: string[]; rolesWithoutRows: string[]; rolesUnknown: boolean },
  role: DispatchRole,
): string {
  const lines: string[] = [];

  lines.push(
    `คุณรับบทบาท **${role.role}** ของ team-os (รันบน tier \`${role.tier}\`)`,
  );
  lines.push("");

  lines.push(
    `งาน: **กริลช่องที่ขาด** ของ **${project.name}** ให้กลายเป็นแถวใน **projects/${project.name}/slices.md**`,
  );
  lines.push(
    "เสร็จเมื่อ: ทุกช่องข้างล่างถูกตัดสินแล้วว่า *เปิดเป็นแถว* หรือ *ปล่อยว่างพร้อมเหตุผลที่เขียนไว้* แล้วเปิด PR ที่แก้เฉพาะไฟล์นั้น — **ไม่ implement เอง**",
  );
  lines.push("");

  lines.push("ช่องที่บอร์ดวัดได้ ณ ตอนกด — ทั้งสองแกนมาจากที่ที่ workspace ประกาศไว้เอง:");
  if (gaps.missingSlots.length > 0) {
    lines.push(
      `- **เอกสาร/ระยะที่ยังไม่มีไฟล์** (team-os/projects/README.md § ช่องที่ต้นแบบมี): ${gaps.missingSlots
        .map((s) => `\`${s}\``)
        .join(" · ")}`,
    );
  } else {
    lines.push("- **เอกสาร/ระยะ**: ครบทุกช่องที่ประกาศไว้");
  }
  if (gaps.rolesUnknown) {
    lines.push(
      "- ⚠️ **แกน role อ่านไม่ได้** — บอร์ดอ่านแผนที่ role → model ไม่ออก ⇒ ตรวจเองจาก team-os/people/roles.md § แกนความเป็นเจ้าของ อย่าเดา",
    );
  } else if (gaps.rolesWithoutRows.length > 0) {
    lines.push(
      `- **role ที่ไม่มีแถวเลยในไฟล์นี้** (team-os/people/roles.md, นับทุกคอลัมน์ รวม \`✅\`): ${gaps.rolesWithoutRows
        .map((r) => `\`${r}\``)
        .join(" · ")}`,
    );
  } else {
    lines.push("- **role**: ทุก role มีอย่างน้อยหนึ่งแถวแล้ว");
  }
  lines.push("");

  lines.push("อ่านก่อนเริ่ม — อย่าเดาจากชื่อไฟล์:");
  lines.push(
    `- projects/${project.name}/slices.md — โครง + กฎการเขียนแถวของไฟล์นี้ + แถวที่มีอยู่แล้ว`,
  );
  if (project.has.scope)
    lines.push(`- projects/${project.name}/scope.md — อะไรอยู่นอกขอบเขต`);
  if (project.has.risks)
    lines.push(`- projects/${project.name}/risks.md — ความเสี่ยงที่เปิดอยู่`);
  lines.push(
    "- team-os/projects/README.md § ช่องที่ต้นแบบมี แต่ workspace ยังไม่มี — ช่องแต่ละช่องตอบคำถามอะไร",
  );
  lines.push(
    "- team-os/people/roles.md § แกนความเป็นเจ้าของ — role ไหนถือ discipline อะไร",
  );
  lines.push("- docs/sops/sop-forge-planning.md — elicit → structure → grill → finalize");
  lines.push("- tools/grill-me/grill-ruleset.md — วิธี grill-me");
  lines.push(
    "- docs/sops/sop-work-ownership.md § Team-Slug-Approved — วิธีเขียน `Assignment:`/`Team-Slug-Approved:` ของเซสชันนี้",
  );
  for (const s of SPINE) lines.push(`- ${s}`);
  lines.push("");

  lines.push("กฎกลางของ team-os:");
  for (const r of CENTRAL_RULES) lines.push(`- ${r}`);
  lines.push("");

  lines.push("⛔ ขอบเขตของเซสชันนี้ — เซสชันนี้ทำแทนไม่ได้:");
  lines.push(
    "- **ห้ามแปลงช่องว่างเป็นแถวแบบหนึ่งต่อหนึ่ง** — ช่องที่ว่างเป็น *หลักฐาน* ไม่ใช่ *คำสั่ง* · บางช่องว่างอย่างถูกต้อง (โปรเจกต์นี้อาจไม่มีงานของ role นั้นจริง ๆ) · กริลก่อน แล้วค่อยตัดสินทีละช่อง",
  );
  lines.push(
    "- **ช่องที่ตัดสินว่าให้ว่างต่อ ต้องเขียนไว้ว่าทำไม** — ปล่อยเงียบไม่ได้ (team-os/README.md กฎข้อ 3: *ช่องที่ว่างต้องเขียนว่าว่าง*)",
  );
  lines.push(
    `- **แก้ได้เฉพาะ \`slices.md\` ของ ${project.name}** — ห้ามแตะโค้ดหรือไฟล์อื่น รวมทั้งไฟล์ที่ช่องข้างบนบอกว่ายังไม่มี (สร้าง \`rollout.md\`/\`retro.md\` ขึ้นมาเองไม่ใช่งานของเซสชันนี้ — เปิดเป็น *แถว* ให้มีคนทำ)`,
  );
  lines.push(
    "- **ไม่ implement เอง** และ **ไม่มีทางลัด** — worktree → PR → หยุดรอเจ้าของไฟเขียว",
  );
  lines.push("");

  const client = project.client || "internal";
  const today = new Date().toISOString().slice(0, 10);
  lines.push(
    `Assignment: ${client}/-/${role.role}/<task-slug-ที่ตกผลึกได้ตอนจบ>`,
  );
  lines.push(
    "(ช่อง office เป็น `-` เพราะบอร์ดอ่านไม่ได้ — resolve เองจาก docs/sops/sop-work-ownership.md ก่อนคอมมิต · ช่อง task-slug ตั้งเองตอนจบ ห้ามเว้นว่าง)",
  );
  lines.push(
    `Team-Slug-Approved: Don ${today} — grill session, no role owns elicitation yet`,
  );
  lines.push(
    "(การกดปุ่มของเจ้าของคือ green light รายครั้งสำหรับ trailer นี้ — ADR-0037 §SD1 · ADR-0040 §SD1)",
  );

  return lines.join("\n");
}

/**
 * The prompt one row of the collapsed gap panel opens with (ADR-0042 §SD5).
 *
 * The fifth shape, and the first one whose PR may touch a file that is not
 * `slices.md`. Every other board prompt hands work *about* the register;
 * this one hands a role the line it signs at close-out
 * (`sop-pipeline-handoff.md § 7.2`) and asks it to make that line signable.
 *
 * Mechanically it is `composePrompt`'s sibling, not `composeGrillPrompt`'s: the
 * role is one of the 7 and resolves through `dispatch.roles`, so the tier is
 * pinned from the table (ADR-0030) and there is no `Team-Slug-Approved:`
 * trailer — that trailer exists for `forge`, which is a `team/` slug and not a
 * role (ADR-0037 §SD1).
 *
 * The ⛔ block is where the honesty lives. A board that measures `rollout.md`
 * missing in 6 of 6 projects is one loop away from six empty files that move a
 * number and close nothing, which is the failure ADR-0040 §SD1 named for rows
 * and this one inherits for files. Three sentences push back — check §7.3 for a
 * file already doing the job, refuse to create a file with no real content, and
 * stay inside this project — but the guard that actually holds is structural:
 * one press, one role, one project, and no "do them all" button anywhere.
 */
export function composeRoleGapPrompt(
  project: WorkspaceProject,
  gap: {
    role: string;
    slug: string;
    rows: number;
    closes: string;
    note: string;
    surfaces: { token: string; level: string; present: boolean | null }[];
    missing: string[];
  },
  role: DispatchRole,
): string {
  const lines: string[] = [];
  const files = gap.surfaces.filter((s) => s.level === 'project' && s.present === false);
  const noRows = gap.rows === 0;

  lines.push(
    `คุณรับบทบาท **${role.role}** ของ team-os (รันบน tier \`${role.tier}\`)`,
  );
  lines.push("");

  lines.push(
    `งาน: **ปิดช่องที่ขาดของ \`${gap.slug}\` ใน \`${project.name}\`** — บรรทัดของ role นี้ที่ด่านปิดรอบ`,
  );
  if (gap.closes) {
    lines.push(
      `บรรทัดของคุณใน \`sop-pipeline-handoff.md § 7.2\`: *"${gap.closes}"*${
        gap.note ? ` · โน้ตของแถว: *"${gap.note}"*` : ""
      }`,
    );
  }
  lines.push("");

  lines.push("ช่องที่บอร์ดวัดได้ ณ ตอนกด (ของโปรเจกต์นี้เท่านั้น):");
  lines.push(
    noRows
      ? `- **แถวใน \`slices.md\` ที่เขียนชื่อ role นี้: 0 แถว** — ไม่เคยมีงานของ role นี้ในโปรเจกต์นี้เลย`
      : `- แถวใน \`slices.md\` ที่เขียนชื่อ role นี้: ${gap.rows} แถว`,
  );
  if (files.length > 0) {
    lines.push(
      `- **พื้นผิวตอนปิดที่ยังไม่มีไฟล์**: ${files
        .map((s) => `\`${s.token}\``)
        .join(" · ")}`,
    );
  } else if (gap.surfaces.length === 0) {
    lines.push(
      "- พื้นผิวตอนปิดของ role นี้ **ไม่ใช่ไฟล์ในโปรเจกต์** ⇒ บอร์ดวัดให้ไม่ได้ และเซสชันนี้ปิดมันไม่ได้ด้วย",
    );
  } else {
    lines.push("- พื้นผิวตอนปิดที่เป็นไฟล์ของโปรเจกต์นี้: มีครบแล้ว");
  }
  lines.push("");

  // The deliverable is the half that differs from every other shape: a row is
  // a line in a file that already exists, a signing surface may not exist yet.
  lines.push("เสร็จเมื่อ — PR เดียวที่แก้เฉพาะใต้ `projects/" + project.name + "/`:");
  if (files.length > 0) {
    lines.push(
      `1. **${files
        .map((s) => `\`${s.token}\``)
        .join(" · ")} ตอบบรรทัดของ role นี้ได้จริง** — หรือมีคำตอบที่เขียนไว้ว่าทำไมยังไม่ถึงเวลา (ดู ⛔ ข้างล่าง)`,
    );
  }
  if (noRows) {
    lines.push(
      `${files.length > 0 ? "2" : "1"}. **แถวใน \`projects/${project.name}/slices.md\` ที่เขียนชื่อ role นี้** — หรือเหตุผลที่เขียนไว้ว่าโปรเจกต์นี้ไม่มีงานของ role นี้จริง ๆ`,
    );
  }
  lines.push("");

  lines.push("อ่านก่อนเริ่ม — อย่าเดาจากชื่อไฟล์:");
  lines.push(
    `- projects/${project.name}/slices.md — แถวที่มีอยู่แล้ว + กฎการเขียนแถวของไฟล์นี้`,
  );
  if (project.has.scope)
    lines.push(`- projects/${project.name}/scope.md — อะไรอยู่นอกขอบเขต`);
  if (project.has.risks)
    lines.push(`- projects/${project.name}/risks.md — ความเสี่ยงที่เปิดอยู่`);
  lines.push(
    "- docs/sops/sop-pipeline-handoff.md § 7.2 · § 7.3 — บรรทัดของ role นี้ และกติกาที่ให้ใบชื่ออื่นตอบแทนได้",
  );
  lines.push(
    "- team-os/people/roles.md § แกนความเป็นเจ้าของ — role นี้ถือ discipline อะไร และบันทึกที่ไหน",
  );
  for (const s of SPINE) lines.push(`- ${s}`);
  lines.push("");

  lines.push("กฎกลางของ team-os:");
  for (const r of CENTRAL_RULES) lines.push(`- ${r}`);
  lines.push("");

  lines.push("⛔ ขอบเขตของเซสชันนี้ — เซสชันนี้ทำแทนไม่ได้:");
  lines.push(
    "- **ถาม `§ 7.3` ก่อนสร้างไฟล์เสมอ** — ด่านถาม *หน้าที่* ไม่ได้ถาม *ชื่อไฟล์* · มีใบที่ทำหน้าที่นั้นอยู่แล้วใต้ชื่ออื่น (เคสจริง: `ai-chatbot` มีพื้นผิว `rollout` อยู่ที่ `docs/design/reply-bot-reopen-contract.md`) ⇒ ผลลัพธ์ที่ถูกคือ **เขียนว่าใบไหนทำหน้าที่อะไร** ไม่ใช่สร้างไฟล์ใหม่",
  );
  lines.push(
    "- **ห้ามสร้างไฟล์ที่ยังไม่มีเนื้อจริง** — โปรเจกต์ที่ยังไม่เคยปล่อยของ คำตอบที่ซื่อสัตย์คือ *แถวใน `slices.md` ว่ายังไม่ถึงเวลา* ไม่ใช่ไฟล์เปล่าที่ทำให้ตัวเลขบนบอร์ดขยับ (`team-os/ways-of-working/definition-of-done.md § ระดับโปรเจกต์`: *การเดาใส่แย่กว่าการปล่อยว่าง*)",
  );
  lines.push(
    `- **แก้ได้เฉพาะใต้ \`projects/${project.name}/\`** — ห้ามแตะโปรเจกต์อื่นหรือโค้ด · ช่องเดียวกันนี้ขาดในหลายโปรเจกต์ และการไล่เติมให้ครบเป็นงานคนละใบที่เจ้าของกดเอง`,
  );
  lines.push(
    "- **ไม่มีทางลัด** — worktree → PR → เจ้าของอ่านก่อน merge เหมือนงานเขียนไฟล์อื่นทุกเส้นทาง",
  );
  lines.push("");

  const client = project.client || "internal";
  const slug = (files.length > 0 ? files[0].token : `slices-${gap.slug}`)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  lines.push(
    `Assignment: ${client}/-/${gap.slug}/${`${slug}-${project.name}`.slice(0, 40)}`,
  );
  lines.push(
    "(ช่อง office เป็น `-` เพราะบอร์ดอ่านไม่ได้ — resolve เองจาก docs/sops/sop-work-ownership.md ก่อนคอมมิต · ช่อง role คือ role ที่ปุ่มนี้เปิดให้ ห้ามเปลี่ยน)",
  );

  return lines.join("\n");
}
