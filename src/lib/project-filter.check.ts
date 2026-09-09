/**
 * ponytail: one runnable check for the `/work` project selector (slices.md S20).
 * The board has no UI test harness, so what is checkable is the pure half —
 * which projects a given `?project=` renders, and that an unrecognised name
 * widens to every project *and says so* instead of blanking the board.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import {
  readProjectParam,
  withProjectParam,
  resolveProjectSelection,
} from './project-filter';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

const projects = [
  { name: 'ai-chatbot' },
  { name: 'switchboard' },
  { name: 'workspace' },
];

// ── reading the param ──
assert(readProjectParam('') === null, 'no query string → all view');
assert(readProjectParam('?project=') === null, 'empty value → all view');
assert(readProjectParam('?project=%20%20') === null, 'whitespace value → all view');
assert(
  readProjectParam('?refresh=1&project=switchboard') === 'switchboard',
  'named project survives other params',
);

// ── writing the param, other params untouched ──
assert(
  withProjectParam('?refresh=1', 'switchboard') === '?refresh=1&project=switchboard',
  `keeps siblings, got ${withProjectParam('?refresh=1', 'switchboard')}`,
);
assert(
  withProjectParam('?project=workspace', 'switchboard') === '?project=switchboard',
  'replaces rather than appends a second project',
);
assert(withProjectParam('?project=workspace', null) === '', 'all view drops the param entirely');
assert(
  withProjectParam('?refresh=1&project=workspace', null) === '?refresh=1',
  'dropping project leaves the rest of the query alone',
);

// ── resolving what to render ──
const all = resolveProjectSelection(projects, null);
assert(all.shown.length === 3 && all.value === '' && all.unknown === null, 'null → every project');

const one = resolveProjectSelection(projects, 'switchboard');
assert(one.shown.length === 1 && one.shown[0].name === 'switchboard', 'named project renders alone');
assert(one.value === 'switchboard' && one.unknown === null, 'select reflects the pinned project');

// ── a pinned tab whose project left the board ──
// It widens instead of blanking, and hands the caller the name to print.
const gone = resolveProjectSelection(projects, 'partner-offer');
assert(gone.shown.length === 3, 'unknown name falls back to every project');
assert(gone.unknown === 'partner-offer', 'the unrecognised name is reported, not swallowed');
assert(gone.value === '', 'the select does not sit on a value it has no option for');

// ── an empty board stays empty, and still reports the miss ──
const emptyBoard = resolveProjectSelection([] as { name: string }[], 'switchboard');
assert(emptyBoard.shown.length === 0, 'no projects in, no projects out');
assert(emptyBoard.unknown === 'switchboard', 'still names what the URL asked for');

console.log('project-filter check: OK');
