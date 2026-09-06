/** Typed fetch wrappers — all server communication centralised here. */

import type { BoardState, Transcript, RichTranscript, OkResponse } from './types';
import type { TimelineResponse } from './timeline';

const BASE = '';

export async function fetchBoardState(): Promise<BoardState> {
  const res = await fetch(`${BASE}/state`);
  if (!res.ok) throw new Error(`/state ${res.status}`);
  return res.json();
}

export async function fetchTranscript(
  sessionId: string,
  since?: string,
  format?: 'rich'
): Promise<Transcript> {
  const sp = new URLSearchParams();
  if (since) sp.set('since', since);
  if (format) sp.set('format', format);
  const qs = sp.toString();
  const res = await fetch(
    `${BASE}/session/${encodeURIComponent(sessionId)}/transcript${qs ? '?' + qs : ''}`
  );
  if (!res.ok) throw new Error(`transcript ${res.status}`);
  return res.json();
}

/** Fetch rich transcript with structured content blocks (ADR-0006). */
export async function fetchRichTranscript(
  sessionId: string,
  since?: string
): Promise<RichTranscript> {
  return fetchTranscript(sessionId, since, 'rich') as Promise<unknown> as Promise<RichTranscript>;
}

/** Tool-call timeline for one session (ADR-0017 §SD1). Not polled. */
export async function fetchTimeline(sessionId: string): Promise<TimelineResponse> {
  const res = await fetch(`${BASE}/session/${encodeURIComponent(sessionId)}/timeline`);
  if (!res.ok) throw new Error(`timeline ${res.status}`);
  return res.json();
}

export async function killSession(
  sessionId: string
): Promise<OkResponse> {
  const res = await fetch(
    `${BASE}/session/${encodeURIComponent(sessionId)}/kill`,
    { method: 'POST' }
  );
  if (!res.ok) throw new Error(`kill ${res.status}`);
  return res.json();
}

export async function dismissSession(
  sessionId: string
): Promise<OkResponse> {
  const res = await fetch(
    `${BASE}/session/${encodeURIComponent(sessionId)}/dismiss`,
    { method: 'POST' }
  );
  if (!res.ok) throw new Error(`dismiss ${res.status}`);
  return res.json();
}

export async function dismissSessions(
  sessionIds: string[]
): Promise<OkResponse> {
  const res = await fetch(`${BASE}/sessions/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_ids: sessionIds }),
  });
  if (!res.ok) throw new Error(`dismiss many ${res.status}`);
  return res.json();
}

export async function undismissSessions(
  sessionIds: string[]
): Promise<OkResponse> {
  const res = await fetch(`${BASE}/sessions/undismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_ids: sessionIds }),
  });
  if (!res.ok) throw new Error(`undismiss many ${res.status}`);
  return res.json();
}

/** Fetch file contents from the agent's worktree (view=files feature). */
export async function fetchFileContent(
  sessionId: string,
  filePath: string
): Promise<{ path: string; content: string; size: number }> {
  const sp = new URLSearchParams();
  sp.set('path', filePath);
  const res = await fetch(
    `${BASE}/session/${encodeURIComponent(sessionId)}/file?${sp.toString()}`
  );
  if (!res.ok) throw new Error(`file ${res.status}`);
  return res.json();
}

export interface StartSessionOptions {
  /** Pins the tier (ADR-0030). Omitting it inherits the model of whatever
   *  launched the server — not a safer default, just an unstated one. */
  model?: string;
  /** Typed into the PTY and left unsent; a person presses Enter. */
  prompt?: string;
}

export interface StartSessionResponse {
  session_id: string | null;
  session_started: boolean;
  harness?: string;
  provider?: string;
  model?: string | null;
  prompt_typed?: boolean;
  message?: string;
}

export async function startSession(
  harness: string,
  provider: string,
  label?: string,
  options?: StartSessionOptions
): Promise<StartSessionResponse> {
  const res = await fetch(`${BASE}/session/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ harness, provider, label, ...options }),
  });
  const data = await res.json().catch(() => null);
  // The spawn path rejects an unknown model rather than quietly ignoring it —
  // surface that instead of returning a body with no session in it.
  if (!res.ok) throw new Error(data?.error || `session/start ${res.status}`);
  return data as StartSessionResponse;
}

// ── Analytics (v2.5) ──

export interface AnalyticsResponse {
  generated_at: string;
  repo: string;
  days: number;
  harness: string;
  summary: {
    total_sessions: number;
    total_operations: number;
    unique_files: number;
  };
  per_harness: Record<string, {
    sessions: number;
    operations: number;
    unique_files: number;
  }>;
  top_files: Array<{
    path: string;
    total_ops: number;
    reads: number;
    edits: number;
    writes: number;
    sessions: number;
    harnesses: Record<string, number>;
  }>;
}

export async function fetchAnalytics(
  days: number,
  harness: string,
): Promise<AnalyticsResponse> {
  const res = await fetch(
    `${BASE}/analytics/files?days=${days}&harness=${encodeURIComponent(harness)}`
  );
  if (!res.ok) throw new Error(`analytics ${res.status}`);
  return res.json();
}

// ── Workspace overview (v3.0, ADR-0029) ──

export interface WorkspaceSlice {
  id: string;
  title: string;
  day: string;
  column: string;
  note: string;
}

export interface WorkspaceProject {
  name: string;
  slices: WorkspaceSlice[];
  columns: Record<string, number>;
  /** From the project's own slices.md frontmatter — used to build the Assignment id. */
  client: string;
  team: string;
  /** The `dispatch.roles[].role` this project's `team:` resolves to, or null
   *  when it does not match a known role or discipline (ADR-0033). */
  default_role: string | null;
  has: { scope: boolean; risks: boolean; hld: boolean };
}

/** Role → tier → model, read from the workspace (ADR-0030 §SD1). Never held here. */
export type WorkspaceDispatch =
  | { present: false; reason: string }
  | {
      present: true;
      tiers: Record<string, string>;
      roles: { role: string; tier: string; model: string }[];
      source: { tiers: string; roles: string };
    };

export interface WorkspaceResponse {
  generated_at: string;
  repo: string;
  head: string;
  stale_by: string;
  projects: WorkspaceProject[];
  totals: {
    projects_with_slices: number;
    slices: Record<string, number>;
  };
  gaps:
    | { present: false }
    | { present: true; total: number; closed: number; reduced: number; open: number };
  dispatch: WorkspaceDispatch;
}


export async function fetchWorkspace(refresh = false): Promise<WorkspaceResponse> {
  const res = await fetch(`${BASE}/workspace${refresh ? '?refresh=1' : ''}`);
  if (!res.ok) throw new Error(`workspace ${res.status}`);
  return res.json();
}
