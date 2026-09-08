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
 * Kept in pure functions so they are checkable offline (dispatch-prompt.check.ts)
 * and so the operator previews the exact text that will be typed, not an
 * approximation of it.
 */
import type { Ritual, WorkspaceProject, WorkspaceSlice } from "./api";

export interface DispatchRole {
  role: string;
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
 * `client` and `team` come from the project's own slices.md frontmatter. The
 * *office* segment is not written anywhere the board reads, so it is left as
 * `-` and the session is told to resolve it — the convention's own marker for
 * "not resolved", rather than a guess that would silently misfile the work.
 *
 * The shape of the prompt does not change the id: preparing a decision on a row
 * and doing the row are the same piece of work, and `git log --grep` has to
 * find both under one slug.
 */
export function assignmentId(
  project: WorkspaceProject,
  slice: WorkspaceSlice,
): string {
  const client = project.client || "internal";
  const team = project.team || "-";
  const slug = (slice.id !== "—" && slice.id ? slice.id : slice.title)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40);
  return `${client}/-/${team}/${slug || "untitled"}`;
}

export function composePrompt(
  project: WorkspaceProject,
  slice: WorkspaceSlice,
  role: DispatchRole,
  shape: PromptShape = "act",
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

  lines.push(`Assignment: ${assignmentId(project, slice)}`);
  lines.push(
    "(ช่อง office เป็น `-` เพราะบอร์ดอ่านไม่ได้ — resolve เองจาก docs/sops/sop-work-ownership.md ก่อนคอมมิต)",
  );

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
