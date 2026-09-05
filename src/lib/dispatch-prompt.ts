/**
 * Composing the prompt a dispatched session opens with (ADR-0030 §SD4).
 *
 * The shape follows team-os, not this repo's old launcher: a role, the slice it
 * owns, and *pointers* to the files that hold the rules. Never the rules
 * themselves — `team/README.md § Core principle` is "reference, never copy",
 * and a prompt that inlines a rule becomes a second copy of it that drifts.
 *
 * Kept in one pure function so it is checkable offline (dispatch-prompt.check.ts)
 * and so the operator previews the exact text that will be typed, not an
 * approximation of it.
 */
import type { WorkspaceProject, WorkspaceSlice } from "./api";

export interface DispatchRole {
  role: string;
  tier: string;
  model: string;
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
): string {
  const lines: string[] = [];

  lines.push(
    `คุณรับบทบาท **${role.role}** ของ team-os (รันบน tier \`${role.tier}\`)`,
  );
  lines.push("");
  lines.push(
    `งาน: **${slice.id !== "—" ? `${slice.id} · ` : ""}${slice.title}**` +
      (slice.day ? ` (${slice.day})` : ""),
  );
  if (slice.note) lines.push(`เสร็จเมื่อ: ${slice.note}`);
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

  lines.push(`Assignment: ${assignmentId(project, slice)}`);
  lines.push(
    "(ช่อง office เป็น `-` เพราะบอร์ดอ่านไม่ได้ — resolve เองจาก docs/sops/sop-work-ownership.md ก่อนคอมมิต)",
  );

  return lines.join("\n");
}
