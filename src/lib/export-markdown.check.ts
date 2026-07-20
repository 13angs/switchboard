/**
 * ponytail: one runnable check for the serializer + turn grouping — the
 * smallest thing that fails if the logic breaks. No test framework.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import { turnOf } from './turns';
import { messagesToMarkdown, messageToMarkdown } from './export-markdown';
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
    content: [
      { type: 'thinking', thinking: 'line1\nline2' },
      { type: 'text', text: 'Looking.' },
      { type: 'tool_use', id: 't1', name: 'Read', input: { file_path: 'a.ts' } },
      { type: 'tool_result', tool_use_id: 't1', content: 'has ``` fence inside' },
      { type: 'mystery', foo: 1 },
    ],
  },
  { role: 'user', ts: '2026-07-18T09:31:00Z', content: [{ type: 'text', text: 'thanks' }] },
  { role: 'assistant', ts: '2026-07-18T09:31:05Z', content: [{ type: 'text', text: 'np' }] },
];

// turn grouping — incl. the "Turn ?" regression (boundary label after turn 1)
assert(JSON.stringify(turnOf(msgs)) === '[1,1,2,2]', `turnOf basic, got ${JSON.stringify(turnOf(msgs))}`);
assert(
  JSON.stringify(turnOf([{ role: 'user' }, { role: 'user' }, { role: 'assistant' }])) === '[1,1,1]',
  'turnOf consecutive users stay in one turn'
);
assert(JSON.stringify(turnOf([{ role: 'assistant' }, { role: 'user' }])) === '[0,1]', 'leading assistant = turn 0');

// serializer — every block type survives, full markdown
const one = messageToMarkdown(msgs[1]);
assert(one.includes('> 💭 line1') && one.includes('> line2'), 'thinking → blockquote, all lines');
assert(one.includes('🔧 `Read(file_path=a.ts)`'), 'tool_use → inline label');
assert(one.includes('<details><summary>📋'), 'tool_result → details');
assert(/`{4,}/.test(one), 'fence grows past embedded ```');
assert(one.includes('"mystery"'), 'unknown block → JSON stub, not dropped');

// document — turn headings + meta header
const doc = messagesToMarkdown(msgs, { title: 'T', harness: 'claude', provider: 'p', totalCostUsd: 2.47 });
assert(doc.includes('## Turn 1') && doc.includes('## Turn 2'), 'turn headings');
assert(doc.includes('**Cost:** $2.47'), 'cost in header');
assert(doc.includes('**Model:** test-model'), 'model from last assistant message');

const noCost = messagesToMarkdown(msgs, { title: 'T', harness: null, provider: null, totalCostUsd: null });
assert(!noCost.includes('**Cost:**'), 'null cost omitted from header');

console.log('export-markdown check OK');
