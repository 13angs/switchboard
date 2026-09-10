/**
 * What one project is still missing, measured against what team-os says a
 * project should have (ADR-0040 §SD2).
 *
 * Two axes, and the workspace declares each in its own file:
 *   - **slots** — `team-os/projects/README.md § ช่องที่ต้นแบบมี …`, which the
 *     server已 resolved into `WorkspaceResponse.slots` + each project's `has`.
 *   - **roles** — `team-os/people/roles.md § โมเดลต่อ role`, which arrives as
 *     `dispatch.roles`. A role is missing when the project's slices.md has *no
 *     row at all* for it — every column counts, `✅` included, because the
 *     question is "has this project ever had work for this role", not "how many
 *     are open" (that one is ADR-0039 §SD6).
 *
 * Computed here rather than on the server on purpose: the card's badge and the
 * dialog must never disagree, and both already hold the `/workspace` payload.
 * The server adds only what a browser cannot know — the scoped commit counts.
 */
import type { WorkspaceProject, WorkspaceResponse } from './api';

export interface ProjectGaps {
  /** Declared slots this project has no file for, in the declared order. */
  missingSlots: string[];
  /** Declared slots it does have — shown so the panel is a checklist, not a scold. */
  presentSlots: string[];
  /** Roles with zero rows in this project's slices.md, in `roles.md` order. */
  rolesWithoutRows: string[];
  /** Rows per role, keyed by role *slug*, for the ones that do have some. */
  rowsPerRole: Record<string, number>;
  /** `missingSlots + rolesWithoutRows` — the number on the card's button. */
  count: number;
  /** True when `dispatch` could not be read, so the role axis is unavailable.
   *  The slot axis still works; the panel says which half is missing. */
  rolesUnknown: boolean;
}

/**
 * Rows per role across any set of projects, keyed by the role slug.
 *
 * The one counter behind two screens (ADR-0041 §SD5): the card's own gap badge
 * passes a single project, the belt panel passes every project on the board.
 * They must never report two different numbers for the same file, which is the
 * same reason ADR-0040 kept this arithmetic in the browser to begin with.
 *
 * Keyed by slug rather than by the display name a row carries (`Product Owner`)
 * because the belt panel joins these counts against `/roles/activity`, whose
 * rows are slugs — the two payloads read the same roles.md table and must line
 * up on it.
 */
export function rowsPerRole(projects: WorkspaceProject[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const project of projects) {
    for (const slice of project.slices) {
      if (!slice.role) continue;
      const key = roleSlug(slice.role);
      counts[key] = (counts[key] ?? 0) + 1;
    }
  }
  return counts;
}

/** `Product Owner` → `product-owner`. Mirrors `workspace._slug`, which is what
 *  puts the third slot of an `Assignment:` id into this shape. */
export function roleSlug(role: string): string {
  return role.trim().toLowerCase().replace(/[\s_]+/g, '-');
}

export function projectGaps(
  project: WorkspaceProject,
  data: Pick<WorkspaceResponse, 'dispatch'>,
): ProjectGaps {
  const missingSlots: string[] = [];
  const presentSlots: string[] = [];
  // Object key order is the order the slots were declared in — the server
  // builds `has` by walking the declared list, so the panel reads top to bottom
  // the way the template is written.
  for (const [key, present] of Object.entries(project.has)) {
    (present ? presentSlots : missingSlots).push(key);
  }

  // Slug-keyed, so this project's half and the workspace-wide belt panel are
  // literally the same call (§SD5).
  const perRole = rowsPerRole([project]);

  // Narrowed inline rather than through a `rolesUnknown` flag: the flag is a
  // boolean, and TypeScript cannot carry a discriminant through one.
  const rolesWithoutRows = data.dispatch.present
    ? data.dispatch.roles
        .map((r) => r.role)
        .filter((role) => !perRole[roleSlug(role)])
    : [];
  const rolesUnknown = !data.dispatch.present;

  return {
    missingSlots,
    presentSlots,
    rolesWithoutRows,
    rowsPerRole: perRole,
    count: missingSlots.length + rolesWithoutRows.length,
    rolesUnknown,
  };
}

/**
 * One §7.2 target, resolved against *this* project (ADR-0042 §SD1).
 *
 * `present` is deliberately three-valued. `true`/`false` is an answer the board
 * measured under `projects/<name>/`; `null` means there was nothing there to
 * measure — a workspace-level target like `meta/adr-*.md`, or a signature that
 * names no file at all. Printing `null` as ✗ would announce that this project's
 * line is unsigned, which the board does not know (§SD3).
 */
export interface RoleSurface {
  /** The location exactly as §7.2 wrote it. */
  token: string;
  /** The normalised string both registers were joined on. Printed on screen so
   *  a re-word on either side is diagnosable rather than silent (§SD1). */
  joinKey: string;
  /** The declared slot this token resolved to, when it resolved to one. */
  slot: string | null;
  /** `project` — checkable here · `workspace` — measured wider than a project ·
   *  `unknown` — §7.2 names a project-level file the slot register does not. */
  level: 'project' | 'workspace' | 'unknown';
  present: boolean | null;
  /** The belt reader's own workspace-wide numbers, carried for the `null`
   *  cases as provenance — never as this project's answer. */
  have: number;
  total: number | null;
}

/** One row of the collapsed panel: a role, its rows here, and the surfaces it
 *  signs for at close-out. */
export interface RoleGapRow {
  /** Display name from `roles.md § โมเดลต่อ role`, or the slug when the row
   *  comes from somewhere else. */
  role: string;
  slug: string;
  /** Resolves to a tier in `dispatch.roles` ⇒ a session can be pinned for it
   *  (ADR-0030). A role that does not is shown without a button, not hidden. */
  known: boolean;
  rows: number;
  surfaces: RoleSurface[];
  /** Slot keys this role signs that this project has no file for. */
  missing: string[];
  /** §7.2's *สิ่งที่ต้องปิด* cell, verbatim. */
  closes: string;
  /** The prose left in §7.2's *ลงที่ไหน* cell, verbatim — this is how §7.3's
   *  "another file may answer this line" reaches the screen (ADR-0041 §SD6). */
  note: string;
  gap: boolean;
}

/** A declared slot no §7.2 line claims. Kept as its own section rather than
 *  filed under a role that looks close (§SD2). */
export interface OwnerlessSlot {
  key: string;
  where: string;
  present: boolean;
}

export interface ProjectRoleGaps {
  roles: RoleGapRow[];
  ownerless: OwnerlessSlot[];
  /** Roles with a gap + ownerless slots with no file — the card's badge. */
  count: number;
  /** `dispatch` unreadable ⇒ no role table at all, and no buttons (ADR-0030). */
  rolesUnknown: boolean;
  /** §7.2 unreadable ⇒ every slot lands in `ownerless` with this reason, and
   *  the role rows keep their row counts and their buttons (§SD6). */
  signatures: { present: boolean; reason: string };
}

/** `docs/design/*` and `docs/design` are the same place. The two registers were
 *  written by different hands for different readers, so the join is normalised
 *  before it is compared — and the normalised value is what the panel prints. */
export function normalizeLocation(where: string): string {
  return where.trim().replace(/\/\*+$/, '').replace(/\/+$/, '');
}

/**
 * The gap panel collapsed onto one key: the role (ADR-0042 §SD1).
 *
 * Neither register moves. The slots still come from `team-os/projects/README.md`
 * through `project.has` (ADR-0040 §SD2), the owners still come from
 * `sop-pipeline-handoff.md § 7.2` through `pipeline.signatures` (ADR-0041 §SD6),
 * and the join key is the *file location* — because §7.2 does not know what a
 * "slot" is and should not have to.
 *
 * Computed here rather than on the server for the same reason ADR-0040 gave:
 * the card's badge and this table are on one screen, and one screen must not
 * say two things about the same file.
 */
export function projectRoleGaps(
  project: WorkspaceProject,
  data: Pick<WorkspaceResponse, 'dispatch' | 'slots' | 'pipeline'>,
): ProjectRoleGaps {
  const signatures = data.pipeline.signatures;
  const perRole = rowsPerRole([project]);

  // location → declared slot key. Built from the slot register so a slot that
  // moves house keeps its owner without anyone editing this file.
  const slotAt = new Map<string, { key: string; where: string }>();
  for (const s of data.slots.slots) {
    slotAt.set(normalizeLocation(s.where), { key: s.key, where: s.where });
  }

  const claimed = new Set<string>();
  const rowFor = new Map<string, RoleGapRow>();

  if (signatures.present) {
    for (const sig of signatures.rows) {
      const surfaces: RoleSurface[] = sig.targets.map((t) => {
        const joinKey = normalizeLocation(t.token);
        const slot = t.level === 'project' ? (slotAt.get(joinKey) ?? null) : null;
        if (slot) claimed.add(slot.key);
        return {
          token: t.token,
          joinKey,
          slot: slot?.key ?? null,
          // A project-level token the slot register does not name is reported
          // as `unknown`, never guessed at — the same refusal ADR-0040 §SD3
          // made for a slot the board cannot map.
          level: t.level === 'workspace' ? 'workspace' : slot ? 'project' : 'unknown',
          present: slot ? (project.has[slot.key] ?? false) : null,
          have: t.have,
          total: t.total,
        };
      });
      rowFor.set(sig.role, {
        role: sig.role,
        slug: sig.role,
        known: false,
        rows: perRole[sig.role] ?? 0,
        surfaces,
        missing: surfaces
          .filter((s) => s.level === 'project' && s.present === false)
          .map((s) => s.slot as string),
        closes: sig.closes,
        note: sig.note,
        gap: false,
      });
    }
  }

  // roles.md order, every role present — a role with nothing here is the whole
  // point of the panel, so it is never filtered out (ADR-0039 §SD2).
  const order: string[] = [];
  if (data.dispatch.present) {
    for (const r of data.dispatch.roles) {
      const slug = roleSlug(r.role);
      const row = rowFor.get(slug) ?? {
        role: r.role,
        slug,
        known: true,
        rows: perRole[slug] ?? 0,
        surfaces: [],
        missing: [],
        closes: '',
        note: '',
        gap: false,
      };
      row.role = r.role; // the display name the rest of the board shows
      row.known = true;
      rowFor.set(slug, row);
      order.push(slug);
    }
  }
  // Anything the registers carry that roles.md does not — a signature line for
  // a retired role, a `role` cell nobody recognises. Shown last, without a
  // button: no tier to pin it to.
  for (const slug of [...rowFor.keys(), ...Object.keys(perRole)]) {
    if (!order.includes(slug)) {
      order.push(slug);
      if (!rowFor.has(slug)) {
        rowFor.set(slug, {
          role: slug,
          slug,
          known: false,
          rows: perRole[slug] ?? 0,
          surfaces: [],
          missing: [],
          closes: '',
          note: '',
          gap: false,
        });
      }
    }
  }

  const roles = order.map((slug) => {
    const row = rowFor.get(slug) as RoleGapRow;
    row.gap = row.rows === 0 || row.missing.length > 0;
    return row;
  });

  const ownerless: OwnerlessSlot[] = data.slots.slots
    .filter((s) => !claimed.has(s.key))
    .map((s) => ({
      key: s.key,
      where: s.where,
      present: project.has[s.key] ?? false,
    }));

  return {
    roles,
    ownerless,
    count:
      roles.filter((r) => r.gap).length +
      ownerless.filter((s) => !s.present).length,
    rolesUnknown: !data.dispatch.present,
    signatures: { present: signatures.present, reason: signatures.reason },
  };
}
