/**
 * ponytail: one runnable check for the `/work` role register (ADR-0043, S33).
 * The board has no UI test harness, so what is checkable is the pure half —
 * the join between the two halves of roles.md, the row order, and that each
 * half degrades on its own instead of blanking the table.
 * Run: npm run check:lib   (esbuild-bundled, executed by node)
 */
import {
  roleRegister,
  assignmentQuery,
  pointsAtNothing,
  pointsAtNothingIn,
  projectLevel,
  type RegisterRow,
} from './role-register';
import type {
  WorkspaceDispatch,
  WorkspaceRegister,
  RegisterRole,
} from './api';

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`check failed: ${msg}`);
}

function role(
  slug: string,
  office: string,
  disciplines: string[],
  records: Partial<RegisterRole['records']> = {},
): RegisterRole {
  return {
    role: slug,
    office,
    disciplines,
    disciplines_raw: disciplines.join(' · '),
    disciplines_dropped: [],
    records: {
      raw: '',
      text: '',
      kind: 'not-files',
      targets: [],
      rejected: [],
      note: '',
      ...records,
    },
  };
}

const register: WorkspaceRegister = {
  source: 'team-os/people/roles.md',
  section: 'แกนความเป็นเจ้าของ',
  present: true,
  reason: '',
  roles: [
    role('cto', 'build', ['arch', 'security'], {
      raw: '`meta/adr-*.md`',
      kind: 'files',
      targets: [
        {
          token: 'meta/adr-*.md',
          glob: 'meta/adr-*.md',
          levels: {
            workspace: { have: 16, paths: ['meta/adr-one.md'], more: 15 },
            project: { have: 0, paths: [], more: 0, projects: 0 },
          },
        },
      ],
    }),
    role('developer', 'build', ['dev'], { raw: 'commit body', note: 'commit body' }),
    role('qa', 'run', []),
  ],
  heavy_when: {
    readable: false,
    column: 'ขึ้น heavy เมื่อ',
    slice: 'S19',
    reason: 'ข้ามโดยเจตนา',
  },
};

const dispatch: WorkspaceDispatch = {
  present: true,
  tiers: { heavy: 'claude-opus-5', standard: 'claude-sonnet-5', light: 'claude-haiku-4-5' },
  roles: [
    // Deliberately a different order from the ownership table, the way the two
    // real sections of roles.md are ordered differently.
    { role: 'Developer', tier: 'standard', model: 'claude-sonnet-5', effort: 'medium' },
    { role: 'CTO', tier: 'heavy', model: 'claude-opus-5', effort: 'xhigh' },
    { role: 'QA', tier: 'standard', model: 'claude-sonnet-5', effort: 'high' },
  ],
  source: { tiers: 'sop', roles: 'roles.md' },
};

// ── the join, and whose order wins ──
const view = roleRegister({ register, dispatch });
assert(
  view.rows.map((r) => r.slug).join(',') === 'cto,developer,qa',
  `row order comes from § แกนความเป็นเจ้าของ, got ${view.rows.map((r) => r.slug)}`,
);
assert(view.rows.every((r) => r.side === 'both'), 'every row carries both halves');

const cto = view.rows[0];
assert(cto.name === 'CTO', 'the display name comes from the tier table');
assert(cto.tier?.tier === 'heavy' && cto.tier.effort === 'xhigh', 'tier + effort joined');
assert(cto.ownership?.office === 'build', 'office joined');

// `Product Owner` → `product-owner` is the whole reason the join needs a slug.
const spaced = roleRegister({
  register: { ...register, roles: [role('product-owner', 'business', ['forge'])] },
  dispatch: {
    ...(dispatch as Extract<WorkspaceDispatch, { present: true }>),
    roles: [
      { role: 'Product Owner', tier: 'standard', model: 'claude-sonnet-5', effort: 'medium' },
    ],
  },
});
assert(spaced.rows.length === 1 && spaced.rows[0].side === 'both', 'display name slugs to the row');

// ── each half degrades on its own (§SD7) ──
const noOwnership = roleRegister({
  register: { ...register, present: false, roles: [], reason: 'อ่านไม่ได้' },
  dispatch,
});
assert(noOwnership.rows.length === 3, 'rows still come from the tier table');
assert(
  noOwnership.rows.every((r) => r.ownership === null && r.tier !== null),
  'the ownership columns blank, the tier column survives',
);
assert(noOwnership.rows.every((r) => r.side === 'dispatch'), 'the row says which half it has');

const noDispatch = roleRegister({
  register,
  dispatch: { present: false, reason: 'อ่านแผนที่ไม่ได้' },
});
assert(noDispatch.rows.length === 3, 'rows still come from the ownership table');
assert(
  noDispatch.rows.every((r) => r.tier === null && r.ownership !== null),
  'the tier column blanks, the ownership columns survive',
);
assert(noDispatch.rows[0].name === 'cto', 'with no tier table the slug is the name');

const neither = roleRegister({
  register: { ...register, present: false, roles: [], reason: 'x' },
  dispatch: { present: false, reason: 'y' },
});
assert(neither.empty && neither.rows.length === 0, 'both halves gone ⇒ no rows, not a fake table');

// ── drift between the two tables is shown, never hidden ──
const drifted = roleRegister({
  register: { ...register, roles: [role('cto', 'build', ['arch'])] },
  dispatch,
});
assert(drifted.rows.length === 3, 'a role only the tier table names still gets a row');
assert(
  drifted.rows.filter((r) => r.side === 'dispatch').map((r) => r.slug).join(',') ===
    'developer,qa',
  'the rows that lost their ownership half are named',
);

// ── the reverse query is built from the row, not typed per role ──
assert(
  assignmentQuery('senior-developer') ===
    "git log --all --grep '^Assignment: .*/senior-developer/'",
  `query shape, got ${assignmentQuery('senior-developer')}`,
);

// ── "the register does not ask for a file" vs "the file is not there yet" ──
const notFiles = view.rows.find((r) => r.slug === 'developer') as RegisterRow;
assert(notFiles.ownership?.records.kind === 'not-files', 'a commit-body row is not-files');
assert(!pointsAtNothing(notFiles), 'not-files is never reported as an empty pattern');
assert(!pointsAtNothing(cto), 'a pattern with matches is not empty');

const empty = roleRegister({
  register: {
    ...register,
    roles: [
      role('devops', 'run', ['infra'], {
        raw: '`rollout.md`',
        kind: 'files',
        targets: [
          {
            token: 'rollout.md',
            glob: 'rollout.md',
            levels: {
              workspace: { have: 0, paths: [], more: 0 },
              project: { have: 0, paths: [], more: 0, projects: 0 },
            },
          },
        ],
      }),
    ],
  },
  dispatch,
});
assert(pointsAtNothing(empty.rows[0]), 'a pattern that matches nothing is reported as empty');

// ── the picker narrows column ⑤ only (ADR-0043 Amendment, S34) ──
const design = {
  token: 'docs/design/*',
  glob: 'docs/design/*',
  levels: {
    workspace: { have: 0, paths: [], more: 0 },
    project: {
      have: 5,
      paths: ['projects/alpha/docs/design/a.md'],
      more: 4,
      projects: 2,
      by_project: [
        { project: 'alpha', have: 3, paths: ['projects/alpha/docs/design/a.md'], more: 2 },
        { project: 'beta', have: 2, paths: ['projects/beta/docs/design/b.md'], more: 1 },
      ],
    },
  },
};

const all = projectLevel(design, null);
assert(!all.scoped && all.level?.have === 5, 'no project picked ⇒ the aggregate, unchanged');

const alpha = projectLevel(design, 'alpha');
assert(alpha.scoped && alpha.level?.have === 3, 'a picked project gets its own count');
assert(
  alpha.level?.paths[0] === 'projects/alpha/docs/design/a.md',
  'and its own paths, not the aggregate sample',
);

// The case a client-side filter over the capped `paths` would get wrong: a
// project with matches that fell outside the sample must not read as zero, and
// a project with none must not inherit the aggregate.
const gamma = projectLevel(design, 'gamma');
assert(gamma.scoped && gamma.level?.have === 0, 'a project with none is a real zero');
assert(gamma.level?.paths.length === 0, 'and shows no other project\'s files');

// An older payload cannot answer per project — that is "cannot tell", not zero.
const legacy = projectLevel(
  {
    ...design,
    levels: { ...design.levels, project: { have: 5, paths: [], more: 5, projects: 2 } },
  },
  'alpha',
);
assert(legacy.level === null, 'no by_project ⇒ the board says it cannot tell');

// ── emptiness is reported per scope ──
const withDesign = roleRegister({
  register: {
    ...register,
    roles: [
      role('tech-lead', 'build', ['software-design'], {
        raw: '`docs/design/*`',
        kind: 'files',
        targets: [design],
      }),
    ],
  },
  dispatch,
}).rows[0];
assert(!pointsAtNothingIn(withDesign, null), 'the aggregate has matches');
assert(!pointsAtNothingIn(withDesign, 'alpha'), 'alpha has matches');
assert(pointsAtNothingIn(withDesign, 'gamma'), 'gamma is empty for this row');
// A workspace-level target is never *this project's* answer — with a project
// picked it counts as empty here and prints as its own line instead.
assert(pointsAtNothingIn(cto, 'alpha'), 'a workspace-level target is not a project answer');
assert(!pointsAtNothing(cto), 'and is still not empty at workspace scope');

console.log('role-register check: OK');
