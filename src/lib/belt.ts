/**
 * The two modes of the board screen — status across every belt, or one belt's
 * stations (ADR-0051 §SD1–§SD3; row-status.md § สายพาน).
 *
 * Two axes, two groupings, one screen. `/work` still has the two screens
 * ADR-0043 §SD1 capped it at: this is a *grouping* of the board screen, not a
 * third screen — same rows, same payload, read along the other axis.
 *
 * What changed on 2026-09-19: `stage` stopped being a workspace-wide
 * vocabulary. A station belongs to a belt, the belts live in the workspace's
 * own register (`team-os/ways-of-working/workflows.md`, on the payload as
 * `belt`), and the page reads that register instead of holding a copy.
 *
 * Kept as pure functions so the choice of grouping is checkable offline
 * (belt.check.ts) rather than only observable by opening the page.
 */
import type { BeltRegistry, WorkspaceProject, WorkspaceSlice } from './api';

/** What the board is showing.
 *
 *  Three fields, not one string, and deliberately so (ADR-0051 §SD3): the chip
 *  row reads as one control but holds two different kinds of filter — a belt
 *  is a *workflow* filter that also switches the grouping, `routine` is a
 *  *kind* filter that does not. Collapsing them into `selected: string` costs
 *  nothing today and costs a rewrite the first time someone wants routine work
 *  on the ops belt.
 */
export interface WorkView {
  mode: 'status' | 'workflow';
  /** Which belt, when `mode === 'workflow'`. `''` otherwise. */
  workflow: string;
  /** `kind` filter, independent of `mode`. `''` = every kind. */
  kind: string;
}

export const ALL_VIEW: WorkView = { mode: 'status', workflow: '', kind: '' };

/** The belt a row walks when it names a station and no belt.
 *  Mirrors control_plane/workspace.py DEFAULT_WORKFLOW (§SD4). */
export const DEFAULT_WORKFLOW = 'dev';

/** Where a row with no declared station goes.
 *
 *  Not a station and not hidden: `stage` is opt-in per file AND per row, so a
 *  file can adopt the column and still leave rows blank while the owner works
 *  out where they sit. Dropping those rows would make the belt view lie by
 *  omission — the one thing a board that only reads must never do. */
export const UNSTAGED = { key: '', label: 'ยังไม่ประกาศสถานี' } as const;

/** The status columns — the one vocabulary that spans every belt (§SD1). */
export const STATUS_COLUMNS = [
  { key: 'done', label: 'เสร็จแล้ว' },
  { key: 'running', label: 'กำลังทำ' },
  { key: 'next', label: 'ถัดไป' },
  { key: 'todo', label: 'รอคิว' },
  { key: 'owner', label: 'คนเคาะ' },
] as const;

/** Human wording for the `dev` belt's nine stations, unchanged from the day
 *  the belt view shipped. Only a *display* name: the register upstream owns
 *  which stations exist and in what order, and a belt this map says nothing
 *  about renders its station key as written. */
const DEV_STATION_LABELS: Record<string, string> = {
  backlog: 'Backlog',
  techdesign: 'Tech Design',
  readydev: 'Ready for Dev',
  inprogress: 'In Progress',
  review: 'Code Review',
  readyqa: 'Ready for QA',
  readydeploy: 'Ready for Deploy',
  deployed: 'Deployed / UAT',
  done: 'Done',
};

export function stationLabel(station: string): string {
  return DEV_STATION_LABELS[station] ?? station;
}

/** The belt this row is on, as the reader resolved it. */
export function workflowOf(slice: WorkspaceSlice): string {
  return slice.workflow || (slice.stage ? DEFAULT_WORKFLOW : '');
}

/** `DEV · Code Review`, or `null` when the row is not on a belt.
 *
 *  Shown on every card in the status grouping, which is the one place cards
 *  from different belts sit next to each other (§SD2). A belt the register
 *  does not have still gets a badge, with its raw value — the board shows what
 *  it read and flags it, it does not substitute and it does not hide (§SD4).
 */
export function beltBadge(
  slice: WorkspaceSlice,
): { belt: string; station: string; known: boolean } | null {
  const belt = workflowOf(slice);
  if (!belt) return null;
  return {
    belt: belt.toUpperCase(),
    station: slice.stage ? stationLabel(slice.stage) : '',
    known: slice.workflow_known !== false,
  };
}

/** Which belts (and kinds) the shown registers actually use, in register order.
 *
 *  Offered from what the rows declare rather than from the whole register: a
 *  chip that leads to an empty board is noise, the same reasoning `/work`
 *  already applied before offering the belt at all.
 */
export function chipsFor(
  projects: readonly WorkspaceProject[],
  registry: BeltRegistry | null,
): { workflows: { key: string; label: string }[]; kinds: string[] } {
  const rows = projects.flatMap((p) => p.slices);
  const usedBelts = new Set(rows.map(workflowOf).filter(Boolean));
  const usedKinds = new Set(rows.map((s) => s.kind).filter(Boolean));
  const declared = Object.entries(registry?.workflows ?? {});
  const workflows = declared
    .filter(([key]) => usedBelts.has(key))
    .map(([key, w]) => ({ key, label: w.label || key }));
  // A belt no register declares still gets a chip, under its raw name: the
  // rows on it exist and must be reachable (§SD4).
  for (const key of usedBelts) {
    if (!workflows.some((w) => w.key === key)) workflows.push({ key, label: key });
  }
  const kinds = (registry?.kinds ?? []).filter((k) => usedKinds.has(k));
  for (const k of usedKinds) if (!kinds.includes(k)) kinds.push(k);
  return { workflows, kinds };
}

/** The columns a view renders, in reading order.
 *
 *  `ยังไม่ประกาศสถานี` leads rather than trails: a row nobody has placed is
 *  the one the reader has to do something about, and the belt's own first
 *  station is where it would land once placed.
 */
export function columnsFor(
  view: WorkView,
  registry: BeltRegistry | null,
): readonly { key: string; label: string }[] {
  if (view.mode !== 'workflow') return STATUS_COLUMNS;
  const stages = registry?.workflows?.[view.workflow]?.stages ?? [];
  return [
    UNSTAGED,
    ...stages.map((s, i) => ({ key: s, label: `${i + 1}. ${stationLabel(s)}` })),
  ];
}

/** Whether a row belongs in this view at all.
 *
 *  Two independent tests, never one: the belt filter applies only in workflow
 *  mode, the kind filter applies in both (§SD3).
 */
export function matchesView(slice: WorkspaceSlice, view: WorkView): boolean {
  if (view.kind && slice.kind !== view.kind) return false;
  if (view.mode !== 'workflow') return true;
  return workflowOf(slice) === view.workflow;
}

/** Which column of `view` this row belongs in.
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
  view: WorkView,
  siblings: readonly WorkspaceSlice[] = [],
): string {
  if (view.mode !== 'workflow') return slice.column;
  // A belt the register does not have has no stations to place a row on, and
  // the row's own `stage` text is a station of a belt nobody declared. Placing
  // it by that text would put the card in a column this view does not render,
  // which drops it off the board — the one thing §SD4 forbids. It is unplaced,
  // which is the honest answer, and its ⚠ badge says why.
  // Found by driving the real page, not by a unit check: the card simply was
  // not there, and every count still added up.
  if (slice.workflow_known === false) return UNSTAGED.key;
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
 *  correction back (meta/adr-slices-stage-axis-2026-09.md §SD3). Which station
 *  counts as "the end" is the reader's call, not this function's — it is the
 *  row's own belt's last station (ADR-0051 §SD5), resolved in
 *  control_plane/workspace.py before `axis_conflict` is set.
 */
export function conflictLabel(slice: WorkspaceSlice): string | null {
  if (slice.axis_conflict === 'stuck-open') return 'ค้างไม่ปิด';
  if (slice.axis_conflict === 'skipped-gate') return 'ปิดข้ามด่าน';
  return null;
}
