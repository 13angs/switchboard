import type {
  WorkspaceDispatch,
  WorkspaceRegister,
  RegisterRole,
  RegisterLevel,
  RegisterTarget,
} from './api';
import { roleSlug } from './project-gaps';

/**
 * The register as one table, joined from the two halves roles.md keeps in two
 * sections and the board reads with two parsers (ADR-0043 §SD2 · §SD7).
 *
 * Joined here rather than on the server for the reason ADR-0041 §SD1 gave and
 * ADR-0042 §SD1 repeated: the tier half already reaches the page as
 * `dispatch`, because it is what pins a session when a card is pressed. A
 * second server-side copy of it would be a second thing that can disagree with
 * the dropdown on the same screen.
 */
export interface RegisterTier {
  tier: string;
  model: string;
  effort: string | null;
}

export interface RegisterRow {
  slug: string;
  /** Display name from `§ โมเดลต่อ role` when that half resolves, else the
   *  slug — the ownership table writes `cto`, the tier table writes `CTO`. */
  name: string;
  /** `null` when `§ แกนความเป็นเจ้าของ` could not be read: a blank set of
   *  columns, never a blank table (§SD7). */
  ownership: RegisterRole | null;
  /** `null` when `§ โมเดลต่อ role` could not be read — the board cannot pin a
   *  tier either, and the screen says so rather than printing a default. */
  tier: RegisterTier | null;
  /** Which half carried this row. `ownership`/`dispatch` alone means the two
   *  tables have drifted, which is the one thing this screen detects for free. */
  side: 'both' | 'ownership' | 'dispatch';
}

export interface RoleRegisterView {
  rows: RegisterRow[];
  /** Both halves unreadable ⇒ no rows at all, and both reasons printed. */
  empty: boolean;
}

/**
 * Row order is `§ แกนความเป็นเจ้าของ`'s own.
 *
 * Not an arbitrary pick: the two tables really are ordered differently in the
 * workspace today (`§ โมเดลต่อ role` puts DevOps and Product Owner ahead of
 * Developer and QA), and `§ แกนความเป็นเจ้าของ` is the register S33 asks the
 * board to print. When it cannot be read, the tier table's order stands in.
 */
export function roleRegister(data: {
  register: WorkspaceRegister;
  dispatch: WorkspaceDispatch;
}): RoleRegisterView {
  const tiers = new Map<string, RegisterTier>();
  const names = new Map<string, string>();
  if (data.dispatch.present) {
    for (const r of data.dispatch.roles) {
      const slug = roleSlug(r.role);
      tiers.set(slug, { tier: r.tier, model: r.model, effort: r.effort });
      names.set(slug, r.role);
    }
  }

  const owned = new Map<string, RegisterRole>();
  const order: string[] = [];
  if (data.register.present) {
    for (const row of data.register.roles) {
      owned.set(row.role, row);
      order.push(row.role);
    }
  }
  // A role the tier table names and the ownership table does not still gets a
  // row — that drift is exactly what `risks.md S-01` looks like from the
  // outside, and hiding it would make the screen agree with itself instead of
  // with the file.
  for (const slug of names.keys()) {
    if (!order.includes(slug)) order.push(slug);
  }

  const rows = order.map((slug) => {
    const ownership = owned.get(slug) ?? null;
    const tier = tiers.get(slug) ?? null;
    return {
      slug,
      name: names.get(slug) ?? slug,
      ownership,
      tier,
      side: ownership && tier ? 'both' : ownership ? 'ownership' : 'dispatch',
    } as RegisterRow;
  });

  return { rows, empty: rows.length === 0 };
}

/**
 * The reverse query for a role that does not record into a file (§SD4).
 *
 * Shaped from the row's own slug rather than typed out per role, in the form
 * `sop-work-ownership.md § Reverse track` publishes — so the day a role is
 * added the command is right without anyone editing this file.
 */
export function assignmentQuery(slug: string): string {
  return `git log --all --grep '^Assignment: .*/${slug}/'`;
}

/** Every target of a row resolved to zero paths at both levels. Used to warn
 *  that a pattern the register names points at nothing yet — which is a
 *  different sentence from "this role does not record into a file". */
export function pointsAtNothing(row: RegisterRow): boolean {
  const records = row.ownership?.records;
  if (!records || records.kind !== 'files' || records.targets.length === 0) {
    return false;
  }
  return records.targets.every(
    (t) => t.levels.workspace.have === 0 && t.levels.project.have === 0,
  );
}


/**
 * Column ⑤'s project half, narrowed to one project (ADR-0043 Amendment, S34).
 *
 * `null` project ⇒ the aggregate, exactly as before. A named project ⇒ that
 * project's own row out of `by_project`, and **a real zero when it has none** —
 * never the aggregate standing in, which would let a filtered screen show
 * another project's files under this project's name.
 *
 * An older payload with no `by_project` degrades to `unknown` rather than to
 * the aggregate, for the same reason: the honest answer to "does this project
 * carry it" is *the board cannot tell*, and ADR-0043 §SD7 already prints that
 * shape rather than guessing.
 */
export function projectLevel(
  target: RegisterTarget,
  project: string | null,
): { level: RegisterLevel | null; scoped: boolean } {
  const level = target.levels.project;
  if (!project) return { level, scoped: false };
  if (!level.by_project) return { level: null, scoped: true };
  const row = level.by_project.find((p) => p.project === project);
  return {
    level: row ?? { have: 0, paths: [], more: 0, projects: 0 },
    scoped: true,
  };
}

/** Whether a row still points at anything once the picker narrows it. Used to
 *  print *this project has none* without claiming the register asked for none. */
export function pointsAtNothingIn(row: RegisterRow, project: string | null): boolean {
  const records = row.ownership?.records;
  if (!records || records.kind !== 'files' || records.targets.length === 0) {
    return false;
  }
  return records.targets.every((t) => {
    const { level } = projectLevel(t, project);
    const inProject = level ? level.have : 0;
    // With a project picked, a workspace-level match is not this project's
    // answer — it is the ◐ case ADR-0042 §SD3 settled, printed as its own line.
    return project ? inProject === 0 : t.levels.workspace.have === 0 && inProject === 0;
  });
}
