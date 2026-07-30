/**
 * ponytail: one runnable check for the timeline's duration cell (ADR-0025 §SD3).
 *
 * The endpoint went to the trouble of distinguishing "still running" from "this
 * harness has no clock" (ADR-0025 §SD2). That distinction is only worth
 * anything if the panel keeps it — a UI that renders both as a blank cell puts
 * the design right back where ADR-0025 found it. That is what this checks.
 * No test framework.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import {
  allUnsupported,
  durationDisplay,
  formatDuration,
  matchesFilter,
  noTimingNote,
  toolIcon,
  type TimelineEntry,
} from './timeline';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

function entry(over: Partial<TimelineEntry>): TimelineEntry {
  return {
    tool: 'Read',
    category: 'read',
    args_summary: 'CLAUDE.md',
    args: {},
    ts: '2026-07-24T10:00:00Z',
    duration_ms: null,
    duration_state: 'pending',
    result_summary: null,
    result_ts: null,
    ...over,
  };
}

// ── the three states never share a rendering ──
const measured = durationDisplay(
  entry({ duration_state: 'measured', duration_ms: 234 })
);
const pending = durationDisplay(entry({ duration_state: 'pending' }));
const unsupported = durationDisplay(entry({ duration_state: 'unsupported' }));

assert(measured.text === '234ms', `measured shows a figure, got ${measured.text}`);
assert(pending.text === '⋯', `pending shows the running glyph, got ${pending.text}`);
assert(unsupported.text === '—', `unsupported shows a dash, got ${unsupported.text}`);
assert(
  new Set([measured.text, pending.text, unsupported.text]).size === 3,
  'the three duration states must not collapse into one glyph — that is the ' +
    'exact failure ADR-0025 §SD2 was written to prevent'
);
assert(pending.title === 'still running', 'pending says why');
assert(
  unsupported.title === 'this harness records no timestamps',
  'unsupported says why, and does not blame the tool'
);

// ── a measured 0ms is a figure, not an absence ──
// Same trap as $0.00 in cost-display: a real instant call must not read as
// "no data".
const instant = durationDisplay(entry({ duration_state: 'measured', duration_ms: 0 }));
assert(instant.text === '0ms', `0ms stays a figure, got ${instant.text}`);

// ── duration scale reads at a glance ──
assert(formatDuration(98) === '98ms', 'sub-second stays in ms');
assert(formatDuration(1200) === '1.2s', 'seconds get one decimal');
assert(formatDuration(252_000) === '4m 12s', 'minutes split out');
assert(formatDuration(288_878) === '4m 49s', 'a real 289s call from the store');

// ── the column drops only when EVERY entry is unsupported ──
const agyOnly = [entry({ duration_state: 'unsupported' }), entry({ duration_state: 'unsupported' })];
const mixed = [entry({ duration_state: 'unsupported' }), entry({ duration_state: 'measured', duration_ms: 5 })];
assert(allUnsupported(agyOnly), 'a wholly clockless session drops the column');
assert(!allUnsupported(mixed), 'one measured entry keeps the column for all of them');
assert(
  !allUnsupported([]),
  'an empty timeline has nothing to say yet — it is not "unsupported"'
);
assert(
  noTimingNote('agy') === 'No timing — the agy transcript has no timestamps.',
  'the note names the harness'
);

// ── filters ──
const bashEntry = entry({ tool: 'Bash', category: 'bash', args_summary: 'ls -la' });
assert(matchesFilter(bashEntry, 'all'), 'All matches everything');
assert(matchesFilter(bashEntry, 'bash'), 'Bash chip catches a bash call');
assert(
  !matchesFilter(bashEntry, 'edit'),
  'Bash must not fall in the Edit chip — FILE_TOOLS maps it to edit, the ' +
    'timeline deliberately does not (ADR-0017 §SD3)'
);
assert(toolIcon('read') === '📖' && toolIcon('nonsense') === '•', 'icons degrade');

console.log('timeline check: OK');
