/** Offline checks for the belt grouping (src/lib/belt.ts). Run: npm run check:belt */
import type { WorkspaceProject, WorkspaceSlice } from './api';
import {
  BELT_COLUMNS,
  UNSTAGED,
  columnOf,
  columnsFor,
  conflictLabel,
  defaultGrouping,
} from './belt';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

const row = (over: Partial<WorkspaceSlice> = {}): WorkspaceSlice => ({
  id: 'X1',
  title: 'ชิ้นหนึ่ง',
  day: '',
  column: 'todo',
  note: '',
  role: null,
  blocked_by: [],
  handoff: null,
  stage: '',
  part_of: '',
  criteria: null,
  axis_conflict: null,
  ...over,
});

const project = (slices: WorkspaceSlice[]): WorkspaceProject =>
  ({ name: 'demo', slices }) as unknown as WorkspaceProject;

// ── the grouping a project opens in comes from the file, not from a setting ──
assert(
  defaultGrouping([project([row(), row()])]) === 'status',
  'a register with no station declared opens in the status grouping',
);
assert(
  defaultGrouping([project([row(), row({ stage: 'review' })])]) === 'belt',
  'one declared station is enough to open along the belt',
);
assert(
  defaultGrouping([]) === 'status',
  'no projects at all is not a reason to show nine empty columns',
);

// ── columns ──
assert(columnsFor('status').length === 5, 'the status grouping keeps its five columns');
assert(
  columnsFor('belt').length === BELT_COLUMNS.length + 1,
  'the belt grouping is nine stations plus the unplaced column',
);
assert(
  columnsFor('belt')[0].key === UNSTAGED.key,
  'unplaced rows lead — they are the ones the reader has to act on',
);
assert(
  columnsFor('belt')[1].key === 'backlog' &&
    columnsFor('belt')[9].key === 'done',
  'the nine stations keep belt order',
);

// ── a row lands in the column of whichever axis is being read ──
const inReview = row({ column: 'todo', stage: 'review' });
assert(columnOf(inReview, 'status') === 'todo', 'status grouping reads the glyph column');
assert(columnOf(inReview, 'belt') === 'review', 'belt grouping reads the stage');
assert(
  columnOf(row({ column: 'todo' }), 'belt') === UNSTAGED.key,
  'a row with no station falls into the unplaced column, never out of the board',
);

// ── a criterion row sits at its parent's station, not in the unplaced column ──
const parent = row({ id: 'P1', stage: 'review' });
const child = row({ id: 'P1a', part_of: 'P1' });
assert(
  columnOf(child, 'belt', [parent, child]) === 'review',
  "a criterion rides its parent's station",
);
assert(
  columnOf(child, 'belt', []) === UNSTAGED.key,
  'a parent the file does not contain leaves the child unplaced, not guessed',
);
assert(
  columnOf(row({ id: 'P2a', part_of: 'P2' }), 'belt', [row({ id: 'P2' })]) ===
    UNSTAGED.key,
  'an unplaced parent leaves its criteria unplaced too — the honest answer',
);
assert(
  columnOf(row({ part_of: 'P1', stage: 'done' }), 'belt', [parent]) === 'done',
  "a criterion that declares its own station keeps it over the parent's",
);
assert(
  columnOf(child, 'status', [parent]) === 'todo',
  'the status grouping is untouched by any of this',
);

// ── conflict wording ──
assert(conflictLabel(row()) === null, 'agreeing axes carry no badge');
assert(
  conflictLabel(row({ axis_conflict: 'stuck-open' })) === 'ค้างไม่ปิด',
  'belt done + glyph open reads as ค้างไม่ปิด',
);
assert(
  conflictLabel(row({ axis_conflict: 'skipped-gate' })) === 'ปิดข้ามด่าน',
  'glyph closed from a station that is not done reads as ปิดข้ามด่าน',
);

console.log('belt check: OK');
