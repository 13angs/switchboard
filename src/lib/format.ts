/** Formatting helpers — pure functions, no React dependency. */

export function ago(ts: string | null): string {
  if (!ts) return '—';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return '<1m';
  if (diff < 3600) return Math.floor(diff / 60) + 'm';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h';
  return Math.floor(diff / 86400) + 'd';
}

export function costStr(usd: number | null | undefined): string {
  if (usd == null) return '';
  return `$${usd.toFixed(2)}`;
}

export function sidShort(sessionId: string | null | undefined): string {
  if (!sessionId) return '—';
  return sessionId.slice(0, 12);
}

export function basename(path: string): string {
  const parts = path.split('/');
  return parts[parts.length - 1] || path;
}

export function dirname(path: string): string {
  const parts = path.split('/');
  parts.pop();
  return parts.join('/') || '/';
}
