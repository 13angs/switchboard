import type { SessionInfo } from './terminal-types';

function providerLabel(provider: string | null | undefined): string {
  if (provider === 'deepseek') return 'DeepSeek';
  if (provider === 'ollama') return 'Ollama';
  if (provider === 'openai') return 'OpenAI';
  if (provider === 'google') return 'Google';
  if (provider === 'claude') return 'Claude';
  return provider || 'Session';
}

export function sessionDisplayTitle(
  label: string | null,
  session: SessionInfo | null,
  sessionId: string | null,
  provider: string | null
): string {
  if (label) return label;
  if (session?.title) return session.title;
  if (session?.git_branch) return session.git_branch;
  if (sessionId) return `${providerLabel(session?.provider || provider)} ${sessionId.slice(0, 8)}`;
  if (provider) return `New ${providerLabel(provider)}`;
  return 'New session';
}
