/**
 * Which role the operator says they are sitting as right now (slices.md
 * S43a) — the `role` field `POST /work/transition` sends, kept separate from
 * a row's own `role` cell.
 *
 * row-status.md § ตารางการส่งต่อ 🔑: *สิทธิ์ผูกกับการส่งต่อ ไม่ใช่กับสถานี* —
 * and the same separation holds one level up. Sitting in a seat is not
 * owning a row: `senior-developer` may be sitting at the keyboard while most
 * cards on the board carry `role: developer`, and the transition buttons
 * must answer against the seat, not the card.
 *
 * Lives in `?actingRole=`, the same reasoning `project-filter.ts` gives for
 * `?project=` (slices.md S20): a multi-tab board where each tab can stand on
 * its own acting role, survive a reload, and be bookmarked — `localStorage`
 * would make every tab fight over one seat.
 */

const PARAM = 'actingRole';

/** `roles.md § โมเดลต่อ role` writes `Senior Developer`, `DevOps`; every
 *  permission table the transition gate reads (`row-status.md § ตารางการ
 *  ส่งต่อ`, `roles.md § แกนความเป็นเจ้าของ`) writes `senior-developer`,
 *  `devops`. `control_plane/workspace.py`'s own `_slug()` bridges the two for
 *  the office lookup; this is the same bridge on the browser side — the
 *  acting-role seat is sent to `/work/transitions` and `/work/transition` as
 *  a slug, never as the dispatch table's display label, or every button
 *  reads "wrong role" no matter which one is picked. */
export function slugifyRole(label: string): string {
  return label.trim().toLowerCase().replace(/[\s_]+/g, '-');
}

/** Reads `?actingRole=` out of a `location.search` string. Blank → nobody
 *  sworn in yet. */
export function readActingRoleParam(search: string): string | null {
  const raw = new URLSearchParams(search).get(PARAM);
  const name = raw?.trim() ?? '';
  return name === '' ? null : name;
}

/** Rewrites `?actingRole=` inside a `location.search`, leaving other params
 *  alone. Returns a leading `?` only when something is left to carry. */
export function withActingRoleParam(search: string, role: string | null): string {
  const sp = new URLSearchParams(search);
  const trimmed = role?.trim() ?? '';
  if (trimmed === '') sp.delete(PARAM);
  else sp.set(PARAM, trimmed);
  const qs = sp.toString();
  return qs ? `?${qs}` : '';
}

/** The role a picker should show as chosen: the URL value when it is the
 *  slug of one of the workspace's own 7 roles, else `null` — a role dropped
 *  from `roles.md` since the tab was bookmarked must not leave every
 *  transition button silently answering for a seat nobody holds any more.
 *  `roles` is `dispatch.roles` verbatim (display labels); the comparison
 *  slugifies each one rather than trusting the URL to already be a slug. */
export function resolveActingRole(
  roles: readonly { role: string }[],
  requested: string | null,
): string | null {
  if (requested === null) return null;
  return roles.some((r) => slugifyRole(r.role) === requested) ? requested : null;
}
