/**
 * ponytail: one runnable check for the cost tooltip's provenance suffix
 * (ADR-0026 §SD3). The rate's correctness has no offline oracle; what is
 * checkable is that the date reaches the reader — and that a cell with nothing
 * priced behind it does not claim one. No test framework.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import { costDisplay } from './format';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

// ── priced figure carries the date ──
const priced = costDisplay(1.5, false, [], '2026-07-30');
assert(priced?.text === '$1.50', `figure rendered, got ${priced?.text}`);
assert(priced!.title.includes('rates checked 2026-07-30'), 'date reaches the tooltip');

// ── priced subtotal keeps BOTH the unpriced list and the date ──
const partial = costDisplay(1.5, true, ['deepseek-v4-pro'], '2026-01-15');
assert(partial?.text === '$1.50+', 'subtotal keeps its + marker');
assert(partial!.title.includes('deepseek-v4-pro'), 'unpriced models still named');
assert(partial!.title.includes('rates checked 2026-01-15'), 'date survives alongside');

// ── nothing priced → no date claim ──
// The cell has no rate behind it; appending a verification date would imply one.
const unpriced = costDisplay(null, false, ['deepseek-v4-pro'], '2026-07-30');
assert(unpriced?.unpriced === true, 'unpriced state preserved');
assert(!unpriced!.title.includes('rates checked'), 'no date on an unpriced cell');

// ── absent date degrades silently, no dangling separator ──
const noDate = costDisplay(1.5, false, []);
assert(noDate!.title === 'Session cost (USD)', `clean title, got ${noDate!.title}`);
assert(!noDate!.title.includes('·'), 'no orphan separator when the date is absent');

// ── $0.00 is still a figure, not an absence (ADR-0022 §SD4 regression) ──
const zero = costDisplay(0, false, [], '2026-07-30');
assert(zero?.text === '$0.00' && zero.unpriced === false, '$0.00 stays a figure');

console.log('cost-display check: OK');
