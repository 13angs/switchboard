import { resolveDispatchTarget } from './dispatch-target';

const role = {
  role: 'Developer', office: 'build', tier: 'standard',
  model: 'claude-sonnet-5', effort: 'medium',
};
const codexTiers = {
  light: 'gpt-5.6-luna', standard: 'gpt-5.6-terra', heavy: 'gpt-5.6-sol',
};

const claude = resolveDispatchTarget(role, 'claude', codexTiers);
if (claude.model !== 'claude-sonnet-5' || claude.provider !== 'claude') {
  throw new Error('Claude dispatch changed');
}
const codex = resolveDispatchTarget(role, 'codex', codexTiers);
if (codex.model !== 'gpt-5.6-terra' || codex.provider !== 'openai') {
  throw new Error('Codex must use its own tier map');
}
if (codex.effort !== 'medium') throw new Error('role effort must travel');
let rejected = false;
try {
  resolveDispatchTarget(role, 'codex', null);
} catch {
  rejected = true;
}
if (!rejected) throw new Error('missing Codex map must not inherit Claude');
