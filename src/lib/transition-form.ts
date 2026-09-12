/**
 * Which text boxes a transition's dialog draws (slices.md S43c) — never the
 * permission decision itself. `POST /work/transition`
 * (`control_plane/transition.py`) is the only place that decides whether a
 * move is *allowed*; a card's transition button already reflects that
 * (`GET /work/transitions` — S43b). This table only decides which fields to
 * ask the operator to type before sending whatever they wrote, and the same
 * server check runs again on submit exactly as it always has (ADR-0044 §SD5)
 * — a dialog that draws the wrong boxes fails at that check, it does not
 * bypass it.
 *
 * Hardcoded to the belt's nine stations rather than inferred from
 * `row-status.md`'s prose `requires` column: that column is written for a
 * human to read, and guessing a field name out of its wording is the exact
 * failure mode the workspace's own risk register warns about
 * (`projects/switchboard/risks.md` `S-01`). Changing what a move needs is
 * still a decision that lands in `_CHECKS` first (`control_plane/
 * transition.py`); this table is kept in lockstep by hand, the same
 * hardcoded-copy trade-off `src/lib/belt.ts` takes with `STAGE_ORDER`.
 */

export type FormShape = 'none' | 'handoff' | 'reason' | 'release';

const SHAPES: Record<string, FormShape> = {
  'review>readyqa': 'handoff',
  'readyqa>inprogress': 'reason',
  'readydeploy>inprogress': 'reason',
  'deployed>inprogress': 'reason',
  'done>inprogress': 'reason',
  'readydeploy>deployed': 'release',
};

export function formShapeFor(fromStage: string, toStage: string): FormShape {
  return SHAPES[`${fromStage}>${toStage}`] ?? 'none';
}
