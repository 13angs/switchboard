/** Offline checks for the board's two modes (src/lib/belt.ts).
 *  Run: npm run check:belt */
import type { BeltRegistry, WorkspaceProject, WorkspaceSlice } from './api';
import {
  ALL_VIEW,
  UNSTAGED,
  beltBadge,
  chipsFor,
  columnOf,
  columnsFor,
  conflictLabel,
  matchesView,
  stationLabel,
  workflowOf,
  type WorkView,
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
  workflow: '',
  workflow_known: true,
  kind: '',
  kind_known: true,
  criteria: null,
  axis_conflict: null,
  ...over,
});

const project = (slices: WorkspaceSlice[]): WorkspaceProject =>
  ({ name: 'demo', slices }) as unknown as WorkspaceProject;

const REGISTRY: BeltRegistry = {
  source: 'team-os/ways-of-working/workflows.md',
  degraded: false,
  reason: '',
  workflows: {
    dev: {
      label: 'Software Delivery',
      stages: [
        'backlog',
        'techdesign',
        'readydev',
        'inprogress',
        'review',
        'readyqa',
        'readydeploy',
        'deployed',
        'done',
      ],
    },
    ops: { label: 'Operations', stages: ['incoming', 'assess', 'execute', 'verify', 'done'] },
  },
  kinds: ['project', 'routine'],
};

const devView: WorkView = { mode: 'workflow', workflow: 'dev', kind: '' };
const opsView: WorkView = { mode: 'workflow', workflow: 'ops', kind: '' };

// ── the board opens on status, across every belt (ADR-0051 §SD2) ────────────
assert(ALL_VIEW.mode === 'status', 'the default view is the status grouping');
assert(
  columnsFor(ALL_VIEW, REGISTRY).map((c) => c.key).join() ===
    'done,running,next,todo,owner',
  'the status grouping is the one vocabulary that spans every belt',
);

// ── a belt renders ITS OWN stations, in the register's order ────────────────
assert(
  columnsFor(opsView, REGISTRY).map((c) => c.key).join() ===
    ',incoming,assess,execute,verify,done',
  'the ops belt shows the ops stations, led by ยังไม่ประกาศสถานี',
);
assert(
  columnsFor(devView, REGISTRY).length === 10,
  'the dev belt still shows its nine stations plus the unplaced column',
);
assert(
  !columnsFor(opsView, REGISTRY).some((c) => c.key === 'review'),
  'no belt borrows another belt’s station',
);
assert(
  columnsFor({ mode: 'workflow', workflow: 'nope', kind: '' }, REGISTRY).length === 1,
  'a belt the register does not have renders no stations rather than dev’s',
);
assert(
  columnsFor(devView, null).length === 1,
  'no register means no stations invented on the page',
);

// ── §SD4: a station and no belt reads as dev ────────────────────────────────
assert(workflowOf(row({ stage: 'review' })) === 'dev', 'a station implies the dev belt');
assert(workflowOf(row()) === '', 'a row on no belt claims none');
assert(
  workflowOf(row({ stage: 'verify', workflow: 'ops' })) === 'ops',
  'a declared belt wins over the default',
);

// ── the two filters are independent (§SD3) ──────────────────────────────────
const opsRoutine = row({ id: 'R1', stage: 'verify', workflow: 'ops', kind: 'routine' });
const devRoutine = row({ id: 'R2', stage: 'review', kind: 'routine' });
const devProject = row({ id: 'P1', stage: 'review', kind: 'project' });
assert(
  matchesView(devRoutine, { mode: 'status', workflow: '', kind: 'routine' }),
  'a kind filter does not switch the grouping',
);
assert(
  !matchesView(devProject, { mode: 'status', workflow: '', kind: 'routine' }),
  'a kind filter still filters',
);
assert(
  matchesView(devRoutine, devView) && matchesView(opsRoutine, opsView),
  'routine work exists on both belts — kind never implies a belt',
);
assert(
  !matchesView(opsRoutine, devView),
  'a belt filter keeps other belts out',
);
assert(
  matchesView(devRoutine, { mode: 'workflow', workflow: 'dev', kind: 'routine' }) &&
    !matchesView(devProject, { mode: 'workflow', workflow: 'dev', kind: 'routine' }),
  'both filters can be on at once and neither replaces the other',
);

// ── placement inside a view ─────────────────────────────────────────────────
assert(
  columnOf(row({ column: 'todo', stage: 'review' }), ALL_VIEW) === 'todo',
  'the status grouping places a row by its glyph, never by its station',
);
assert(
  columnOf(row({ stage: 'verify', workflow: 'ops' }), opsView) === 'verify',
  'a belt places a row at its station',
);
assert(
  columnOf(row({ stage: '' }), devView) === UNSTAGED.key,
  'a row with no station is shown as unplaced, never dropped',
);
const parent = row({ id: 'S22', stage: 'readydev' });
assert(
  columnOf(row({ id: 'S22a', part_of: 'S22' }), devView, [parent]) === 'readydev',
  'a criterion row sits at its parent’s station',
);
assert(
  columnOf(row({ id: 'S9a', part_of: 'S9' }), devView, [parent]) === UNSTAGED.key,
  'a criterion whose parent is unplaced stays unplaced — the honest answer',
);

// ── §SD4: an unknown belt is shown and flagged, never substituted ───────────
const stranger = row({ workflow: 'marketing', workflow_known: false, stage: 'draft' });
const badge = beltBadge(stranger);
assert(badge && badge.belt === 'MARKETING' && badge.known === false, 'raw value, flagged');
assert(
  workflowOf(stranger) === 'marketing',
  'an unknown belt is never rewritten to dev',
);
assert(
  chipsFor([project([stranger])], REGISTRY).workflows.some((w) => w.key === 'marketing'),
  'a belt nobody declared still gets a chip — its rows have to be reachable',
);

assert(
  columnOf(stranger, { mode: 'workflow', workflow: 'marketing', kind: '' }) ===
    UNSTAGED.key,
  'a row on an undeclared belt is unplaced, never sent to a column that view does not render',
);
assert(
  columnsFor({ mode: 'workflow', workflow: 'marketing', kind: '' }, REGISTRY).some(
    (c) => c.key === UNSTAGED.key,
  ),
  'and the column it lands in is one that belt actually renders',
);

// ── chips come from the rows in front of you, not from the whole register ───
const chips = chipsFor([project([devProject, opsRoutine])], REGISTRY);
assert(
  chips.workflows.map((w) => w.key).join() === 'dev,ops',
  'chips are offered in register order for the belts actually in use',
);
assert(
  chips.workflows[0].label === 'Software Delivery',
  'a chip shows the register’s label, not the bare key',
);
assert(
  chips.kinds.join() === 'project,routine',
  'kind chips are offered the same way',
);
assert(
  chipsFor([project([row()])], REGISTRY).workflows.length === 0 &&
    chipsFor([project([row()])], REGISTRY).kinds.length === 0,
  'a register declaring nothing offers no chip that opens an empty board',
);

// ── badges, labels, conflicts ───────────────────────────────────────────────
assert(beltBadge(row()) === null, 'a row on no belt carries no badge');
assert(
  beltBadge(row({ stage: 'review' }))?.station === 'Code Review',
  'the dev belt keeps the wording it shipped with',
);
assert(stationLabel('verify') === 'verify', 'an unlabelled station shows its key');
assert(
  conflictLabel(row({ axis_conflict: 'stuck-open' })) === 'ค้างไม่ปิด' &&
    conflictLabel(row({ axis_conflict: 'skipped-gate' })) === 'ปิดข้ามด่าน' &&
    conflictLabel(row()) === null,
  'the board prints the disagreement and never corrects it',
);

console.log('belt.check.ts OK');
