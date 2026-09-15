import type { DispatchRole } from './dispatch-prompt';

export type DispatchHarness = 'claude' | 'codex';

/** Resolve a role's tier using only the selected harness's declared lineup. */
export function resolveDispatchTarget(
  role: DispatchRole,
  harness: DispatchHarness,
  codexTiers: Record<string, string> | null,
) {
  if (harness === 'claude') {
    return { harness, provider: 'claude', model: role.model, effort: role.effort };
  }
  const model = codexTiers?.[role.tier];
  if (!model) throw new Error(`Codex tier ${role.tier} is unavailable`);
  return { harness, provider: 'openai', model, effort: role.effort };
}
