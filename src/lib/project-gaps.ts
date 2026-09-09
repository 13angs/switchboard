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
  /** Rows per role, for the ones that do have some. */
  rowsPerRole: Record<string, number>;
  /** `missingSlots + rolesWithoutRows` — the number on the card's button. */
  count: number;
  /** True when `dispatch` could not be read, so the role axis is unavailable.
   *  The slot axis still works; the panel says which half is missing. */
  rolesUnknown: boolean;
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

  const rowsPerRole: Record<string, number> = {};
  for (const slice of project.slices) {
    if (!slice.role) continue;
    rowsPerRole[slice.role] = (rowsPerRole[slice.role] ?? 0) + 1;
  }

  // Narrowed inline rather than through a `rolesUnknown` flag: the flag is a
  // boolean, and TypeScript cannot carry a discriminant through one.
  const rolesWithoutRows = data.dispatch.present
    ? data.dispatch.roles.map((r) => r.role).filter((role) => !rowsPerRole[role])
    : [];
  const rolesUnknown = !data.dispatch.present;

  return {
    missingSlots,
    presentSlots,
    rolesWithoutRows,
    rowsPerRole,
    count: missingSlots.length + rolesWithoutRows.length,
    rolesUnknown,
  };
}
