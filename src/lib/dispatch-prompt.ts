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
 * Kept in pure functions so they are checkable offline (dispatch-prompt.check.ts)
 * and so the operator previews the exact text that will be typed, not an
 * approximation of it.
 */
import type { WorkspaceProject, WorkspaceSlice } from "./api";

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
  lines.push(
    "- **ย้อนกลับได้ → ตัดสินเอง · ย้อนกลับไม่ได้ → เขียน ADR ก่อนลงมือ** (ไม่ใช่เขียนย้อนหลัง)",
  );
  lines.push("- ไม่แน่ใจว่าย้อนกลับได้ไหม → ถือว่าย้อนกลับไม่ได้ แล้วถาม");
  lines.push(
    "- ติด 3 รอบแล้วไม่ขยับ → หยุด แล้วโพสต์ 3 บรรทัด (ติด: / ลองแล้ว: / ต้องการ:)",
  );
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
