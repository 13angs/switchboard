/**
 * One runnable check for the S43c form-shape table.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import { formShapeFor } from './transition-form';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

assert(formShapeFor('review', 'readyqa') === 'handoff', 'the handoff form gates review → readyqa');
assert(formShapeFor('readydeploy', 'deployed') === 'release', 'a deploy needs a release/tag');
for (const from of ['readyqa', 'readydeploy', 'deployed', 'done']) {
  assert(formShapeFor(from, 'inprogress') === 'reason', `${from} → inprogress needs a reason`);
}
for (const [from, to] of [
  ['readydev', 'inprogress'],
  ['inprogress', 'review'],
  ['readyqa', 'readydeploy'],
  ['deployed', 'done'],
]) {
  assert(formShapeFor(from, to) === 'none', `${from} → ${to} needs no dialog`);
}
assert(formShapeFor('backlog', 'techdesign') === 'none', 'an undeclared pair needs no dialog either');

console.log('transition-form check: OK');
