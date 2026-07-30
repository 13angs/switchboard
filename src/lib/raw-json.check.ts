/**
 * ponytail: one runnable check for the raw-JSON serializer (ADR-0015 §SD3).
 * The pretty-print contract and the circular-reference guard are the only
 * logic in Branch B — the rest is render wiring. No test framework.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import { serializeMessages } from './raw-json';
import type { RichMessage } from './types';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

const msgs: RichMessage[] = [
  { role: 'user', ts: '2026-07-18T09:30:00Z', content: [{ type: 'text', text: 'fix the bug' }] },
  {
    role: 'assistant',
    ts: '2026-07-18T09:30:15Z',
    model: 'test-model',
    stop_reason: 'end_turn',
    usage: { input_tokens: 3, output_tokens: 5 },
    content: [
      { type: 'thinking', thinking: 'line1\nline2' },
      { type: 'tool_use', id: 't1', name: 'Read', input: { file_path: 'a.ts' } },
      { type: 'tool_result', tool_use_id: 't1', content: 'ok' },
    ],
  },
];

// ── pretty-print contract (§SD3: JSON.stringify(data, null, 2)) ──
const out = serializeMessages(msgs);
assert(out.ok, 'well-formed messages serialize');
assert(out.ok && out.text === JSON.stringify(msgs, null, 2), '2-space indent, no transformation');
assert(out.ok && out.text.includes('\n  {'), 'indented, not a single line');

// The whole point of the raw view: metadata the bubbles drop must survive.
assert(out.ok && out.text.includes('"stop_reason": "end_turn"'), 'stop_reason survives');
assert(out.ok && out.text.includes('"usage"'), 'usage survives');
assert(out.ok && out.text.includes('"tool_use_id": "t1"'), 'tool_result linkage survives');
assert(out.ok && out.text.includes('"thinking"'), 'thinking blocks survive');

// ── empty transcript renders, does not error ──
const empty = serializeMessages([]);
assert(empty.ok && empty.text === '[]', `empty → "[]", got ${JSON.stringify(empty)}`);

// ── circular reference: the ADR-0015 § Risk mitigation ──
// JSON.stringify throws; the view must show an error banner, not crash the page.
const circular: Record<string, unknown> = { role: 'user', ts: 'x', content: [] };
circular.self = circular;
const bad = serializeMessages([circular as unknown as RichMessage]);
assert(!bad.ok, 'circular reference is caught, not thrown');
assert(!bad.ok && bad.error.length > 0, 'failure carries a message to display');

console.log('raw-json check: OK');
