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

/** What to render in a cost slot, or null when there is nothing to say.
 *  `unpriced` is true for "we have no rate for this model" — which is a
 *  different fact from "$0.00" and must not share a cell with it (ADR-0022). */
export interface CostDisplay {
  text: string;
  title: string;
  unpriced: boolean;
}

/**
 * Resolve the three cost states a session can be in (ADR-0022 §SD2/§SD4):
 *
 *   - a figure — priced; suffixed `+` and flagged when it is a subtotal because
 *     some model in the session had no rate
 *   - `unpriced` — usage exists but no model in it could be priced
 *   - nothing — no usage data at all (agy sessions, empty transcripts)
 *
 * Note $0.00 is a *figure*, not an absence: a session can legitimately cost
 * nothing, and blanking it would make it indistinguishable from `unpriced`.
 */
export function costDisplay(
  usd: number | null | undefined,
  partial?: boolean,
  unpricedModels?: string[] | null,
): CostDisplay | null {
  const unknown = unpricedModels ?? [];
  if (usd == null) {
    if (unknown.length === 0) return null;
    return {
      text: 'unpriced',
      title: `No rate in pricing.json for: ${unknown.join(', ')}`,
      unpriced: true,
    };
  }
  if (partial && unknown.length > 0) {
    return {
      text: `${costStr(usd)}+`,
      title: `Priced subtotal — no rate for: ${unknown.join(', ')}`,
      unpriced: false,
    };
  }
  return { text: costStr(usd), title: 'Session cost (USD)', unpriced: false };
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
