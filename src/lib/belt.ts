/**
 * The belt view — grouping the board's cards by `stage` instead of by status
 * glyph (row-status.md § สายพาน, accepted 2026-09-12).
 *
 * Two axes, two groupings, one screen. `/work` still has the two screens
 * ADR-0043 §SD1 capped it at: this is a *grouping* of the board screen, not a
 * third screen — same rows, same payload, read along the other axis.
 *
 * Kept as pure functions so the choice of grouping is checkable offline
 * (belt.check.ts) rather than only observable by opening the page.
 */
import type { WorkspaceProject, WorkspaceSlice } from './api';

export type Grouping = 'status' | 'belt';

/** The nine stations, in belt order. Mirrors control_plane/workspace.py
 *  STAGE_ORDER, which mirrors row-status.md § สายพาน — the same
 *  hardcoded-copy trade-off route-lint Check 9 took, for the same reason:
 *  nine literal strings beat parsing a Thai table in three places. */
export const BELT_COLUMNS = [
  { key: 'backlog', label: '1. Backlog' },
  { key: 'techdesign', label: '2. Tech Design' },
  { key: 'readydev', label: '3. Ready for Dev' },
  { key: 'inprogress', label: '4. In Progress' },
  { key: 'review', label: '5. Code Review' },
  { key: 'readyqa', label: '6. Ready for QA' },
  { key: 'readydeploy', label: '7. Ready for Deploy' },
  { key: 'deployed', label: '8. Deployed / UAT' },
  { key: 'done', label: '9. Done' },
] as const;

/** Where a row with no declared station goes.
 *
 *  Not a station and not hidden: `stage` is opt-in per file AND per row, so a
 *  file can adopt the column and still leave rows blank while the owner works
 *  out where they sit. Dropping those rows would make the belt view lie by
 *  omission — the one thing a board that only reads must never do. */
export const UNSTAGED = { key: '', label: 'ยังไม่ประกาศสถานี' } as const;

/** Which grouping a project opens in.
 *
 *  Per project, from the file itself: a register that has started declaring
 *  stations is asking to be read along the belt; one that has not would show
 *  nine empty columns and one full `ยังไม่ประกาศสถานี`, which is worse than
 *  the view it replaced. Nothing is stored — the same reasoning ADR-0043 §SD1
 *  gave for not pinning the screen applies to the grouping inside it.
 */
export function defaultGrouping(projects: WorkspaceProject[]): Grouping {
  return projects.some((p) => p.slices.some((s) => s.stage)) ? 'belt' : 'status';
}

/** The columns a grouping renders, in reading order.
 *
 *  `ยังไม่ประกาศสถานี` leads rather than trails: a row nobody has placed is
 *  the one the reader has to do something about, and the belt's own first
 *  station is where it would land once placed.
 */
export function columnsFor(
  grouping: Grouping,
): readonly { key: string; label: string }[] {
  return grouping === 'belt'
    ? [UNSTAGED, ...BELT_COLUMNS]
    : [
        { key: 'done', label: 'เสร็จแล้ว' },
        { key: 'running', label: 'กำลังทำ' },
        { key: 'next', label: 'ถัดไป' },
        { key: 'todo', label: 'รอคิว' },
        { key: 'owner', label: 'คนเคาะ' },
      ];
}

/** Which column of `grouping` this row belongs in.
 *
 *  `off` (a grid day with no work) has no belt station and never gains one —
 *  it is context, not a queue — so it keeps being hidden by the caller in
 *  either grouping, exactly as before.
 *
 *  A criterion row (`part-of` set) sits at **its parent's** station. It is not
 *  a piece of work with a position of its own — it is one condition of the
 *  parent's — so leaving it unplaced would fill `ยังไม่ประกาศสถานี` with rows
 *  nobody has to place, and put a criterion further from the card that counts
 *  it. Measured on the first register to adopt the columns: three of the four
 *  unplaced rows were criteria of `S22`. The row is still rendered as its own
 *  card; only where it sits changes.
 */
export function columnOf(
  slice: WorkspaceSlice,
  grouping: Grouping,
  siblings: readonly WorkspaceSlice[] = [],
): string {
  if (grouping !== 'belt') return slice.column;
  if (slice.stage) return slice.stage;
  if (!slice.part_of) return UNSTAGED.key;
  // One hop, never a chain: `part-of` names a parent, and route-lint Check 10
  // already refuses a cycle. A parent that is itself unplaced leaves the child
  // unplaced too, which is the honest answer.
  const parent = siblings.find((s) => s.id === slice.part_of);
  return parent?.stage || UNSTAGED.key;
}

/** The badge text for a row whose two axes disagree, or `null`.
 *
 *  Wording, not a verdict: the board prints what it read and never writes a
 *  correction back (meta/adr-slices-stage-axis-2026-09.md §SD3).
 */
export function conflictLabel(slice: WorkspaceSlice): string | null {
  if (slice.axis_conflict === 'stuck-open') return 'ค้างไม่ปิด';
  if (slice.axis_conflict === 'skipped-gate') return 'ปิดข้ามด่าน';
  return null;
}
