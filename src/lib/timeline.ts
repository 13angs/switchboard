/** Tool-call timeline — pure presentation logic (ADR-0017 §SD3, ADR-0025 §SD3).
 *
 * Kept out of the component so the one thing worth checking — that a missing
 * duration says *why* it is missing — is runnable without a DOM. See
 * `timeline.check.ts`.
 */

export type DurationState = 'measured' | 'pending' | 'unsupported';

export interface TimelineEntry {
  tool: string;
  category: string; // 'read' | 'write' | 'edit' | 'bash' | 'other'
  args_summary: string;
  args: Record<string, unknown>;
  ts: string | null;
  duration_ms: number | null;
  duration_state: DurationState;
  result_summary: string | null;
  result_ts: string | null;
}

export interface TimelineResponse {
  session_id: string;
  harness: string;
  entries: TimelineEntry[];
}

export const TIMELINE_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'read', label: 'Read' },
  { key: 'write', label: 'Write' },
  { key: 'edit', label: 'Edit' },
  { key: 'bash', label: 'Bash' },
] as const;

export type FilterKey = (typeof TIMELINE_FILTERS)[number]['key'];

const TOOL_ICONS: Record<string, string> = {
  read: '📖',
  write: '✏️',
  edit: '🔧',
  bash: '💻',
};

export function toolIcon(category: string): string {
  return TOOL_ICONS[category] ?? '•';
}

export function matchesFilter(entry: TimelineEntry, filter: FilterKey): boolean {
  return filter === 'all' || entry.category === filter;
}

/** "234ms" / "1.2s" / "4m 12s" — a duration a human reads at a glance. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export interface DurationDisplay {
  text: string;
  title: string;
}

/**
 * What goes in the duration cell.
 *
 * ADR-0025 §SD2 exists because `null` was carrying two unrelated meanings. The
 * panel must not render them the same way: `⋯` is "wait, it is still running",
 * `—` is "this will never have a number". Collapsing them back into one glyph
 * here would undo the endpoint's whole reason for reporting the state.
 */
export function durationDisplay(entry: TimelineEntry): DurationDisplay {
  if (entry.duration_state === 'measured' && entry.duration_ms != null) {
    return {
      text: formatDuration(entry.duration_ms),
      title: `${entry.duration_ms}ms`,
    };
  }
  if (entry.duration_state === 'pending') {
    return { text: '⋯', title: 'still running' };
  }
  return { text: '—', title: 'this harness records no timestamps' };
}

/**
 * True when *no* entry in the session can ever carry a duration.
 *
 * ADR-0025 §SD3: a column of identical dashes is worse than one honest
 * sentence, so the panel drops the column entirely in this case. An empty
 * timeline is not "unsupported" — there is simply nothing to say yet.
 */
export function allUnsupported(entries: TimelineEntry[]): boolean {
  return entries.length > 0 && entries.every((e) => e.duration_state === 'unsupported');
}

/** The one-line replacement for the dropped column. */
export function noTimingNote(harness: string): string {
  return `No timing — the ${harness} transcript has no timestamps.`;
}
