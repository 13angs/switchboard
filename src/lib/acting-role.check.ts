/**
 * One runnable check for the S43a acting-role picker's URL state — the same
 * pure-half discipline `project-filter.check.ts` uses.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import {
  readActingRoleParam,
  withActingRoleParam,
  resolveActingRole,
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

// ── resolving what the picker shows selected ──
const roles = [{ role: 'developer' }, { role: 'qa' }, { role: 'product-owner' }];
assert(resolveActingRole(roles, null) === null, 'no seat picked → null');
assert(resolveActingRole(roles, 'qa') === 'qa', 'a declared role is the pick');
assert(
  resolveActingRole(roles, 'intern') === null,
  'a role the table does not declare resolves to nobody, not a guess',
);
assert(
  resolveActingRole([], 'qa') === null,
  'an unreadable table picks nobody rather than trusting the URL blind',
);

console.log('acting-role check: OK');
