/**
 * Which project `/work` is showing, resolved from the URL (slices.md S20).
 *
 * The selection lives in `?project=` and nowhere else. `localStorage` would be
 * one value shared by every tab, and the board is deliberately a multi-tab
 * surface now — S13 pins a dispatched session in its own tab and ADR-0037 §SD3
 * opens grill in a fresh one. A URL-carried value lets each tab stand on one
 * project, survive a reload, and be bookmarked; a stored one would make those
 * tabs fight over a single slot.
 *
 * Absent or unrecognised → the all-projects view. The board reads a tree it
 * does not own: a project can lose its `slices.md` between two loads of a
 * pinned tab, and showing everything plus saying so beats an empty screen that
 * looks like "no work left".
 */

/** Every project — the value the `<select>` carries for the all view. */
const ALL = '';

/** Reads `?project=` out of a `location.search` string. Blank → all. */
export function readProjectParam(search: string): string | null {
  const raw = new URLSearchParams(search).get('project');
  const name = raw?.trim() ?? '';
  return name === ALL ? null : name;
}

/** Rewrites `?project=` inside a `location.search`, leaving other params alone.
 *  Returns a leading `?` only when something is left to carry. */
export function withProjectParam(search: string, name: string | null): string {
  const sp = new URLSearchParams(search);
  if (name === null || name.trim() === ALL) sp.delete('project');
  else sp.set('project', name.trim());
  const qs = sp.toString();
  return qs ? `?${qs}` : '';
}

export interface ProjectSelection<P> {
  /** What the `<select>` should show as chosen — `''` for the all view, and
   *  also `''` when the URL named something this board cannot show. */
  value: string;
  /** The projects to render. */
  shown: P[];
  /** The URL's project name when it matched nothing, else null. The caller
   *  prints it: a pinned tab that silently widened to every project is the
   *  same class of quiet failure as a calendar bar that loses its button. */
  unknown: string | null;
  /** The resolved name, or `null` for the all view — including the case where
   *  the URL named a project this board cannot show. Added for the register
   *  screen (ADR-0043 Amendment, S34), which filters *a column* rather than
   *  choosing what to render, so `shown` has nothing to tell it. Derived here
   *  rather than re-tested by each caller: two readers of one selection are
   *  two things that can disagree about what "unknown" widens to. */
  picked: string | null;
}

/** Picks the projects to render for a requested name. */
export function resolveProjectSelection<P extends { name: string }>(
  projects: P[],
  requested: string | null,
): ProjectSelection<P> {
  if (requested === null) {
    return { value: ALL, shown: projects, unknown: null, picked: null };
  }
  const hit = projects.find((p) => p.name === requested);
  if (!hit) {
    return { value: ALL, shown: projects, unknown: requested, picked: null };
  }
  return { value: hit.name, shown: [hit], unknown: null, picked: hit.name };
}
