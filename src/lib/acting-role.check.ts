/**
 * One runnable check for the S43a acting-role picker's URL state — the same
 * pure-half discipline `project-filter.check.ts` uses.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import {
  readActingRoleParam,
  withActingRoleParam,
  resolveActingRole,
  slugifyRole,
} from './acting-role';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

// ── reading the param ──
assert(readActingRoleParam('') === null, 'no query string → nobody sworn in');
assert(readActingRoleParam('?actingRole=') === null, 'empty value → nobody sworn in');
assert(readActingRoleParam('?actingRole=%20%20') === null, 'whitespace value → nobody sworn in');
assert(
  readActingRoleParam('?project=switchboard&actingRole=qa') === 'qa',
  'the role survives other params',
);

// ── writing the param, other params untouched ──
assert(
  withActingRoleParam('?project=switchboard', 'qa') ===
    '?project=switchboard&actingRole=qa',
  'keeps siblings',
);
assert(
  withActingRoleParam('?actingRole=developer', 'qa') === '?actingRole=qa',
  'replaces rather than appends a second role',
);
assert(withActingRoleParam('?actingRole=qa', null) === '', 'clearing drops the param entirely');
assert(
  withActingRoleParam('?project=switchboard&actingRole=qa', null) === '?project=switchboard',
  'dropping the role leaves the rest of the query alone',
);

// ── slugifying a dispatch-table label into a permission-table token ──
// roles.md § โมเดลต่อ role writes the display form; row-status.md § ตารางการ
// ส่งต่อ (and roles.md § แกนความเป็นเจ้าของ) write the slug the gate checks
// against. A picker that sent the display form straight through would have
// every button read "wrong role" no matter which one was picked — this is
// the exact bug a live run surfaced (S43a, 2026-09-12).
assert(slugifyRole('Senior Developer') === 'senior-developer', 'space → hyphen, lowercased');
assert(slugifyRole('DevOps') === 'devops', 'already one word, just lowercased');
assert(slugifyRole('CTO') === 'cto', 'an acronym lowercases like anything else');
assert(slugifyRole('Product Owner') === 'product-owner', 'two words → one hyphen');
assert(slugifyRole('  QA  ') === 'qa', 'surrounding whitespace is trimmed, not hyphenated');

// ── resolving what the picker shows selected ──
// `roles` here is `dispatch.roles` as workspace.py hands it back — display
// labels, not slugs — because that is what the real payload looks like.
const roles = [
  { role: 'Developer' },
  { role: 'QA' },
  { role: 'Product Owner' },
  { role: 'Senior Developer' },
];
assert(resolveActingRole(roles, null) === null, 'no seat picked → null');
assert(resolveActingRole(roles, 'qa') === 'qa', 'a slug matching a slugified label is the pick');
assert(
  resolveActingRole(roles, 'senior-developer') === 'senior-developer',
  'a multi-word label resolves by its slug, not its display form',
);
assert(
  resolveActingRole(roles, 'QA') === null,
  'the display label itself is not an acceptable URL value — only the slug',
);
assert(
  resolveActingRole(roles, 'intern') === null,
  'a role the table does not declare resolves to nobody, not a guess',
);
assert(
  resolveActingRole([], 'qa') === null,
  'an unreadable table picks nobody rather than trusting the URL blind',
);

console.log('acting-role check: OK');
