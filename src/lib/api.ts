/** Typed fetch wrappers — all server communication centralised here. */

import type { BoardState, Transcript, RichTranscript, OkResponse } from './types';

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

export async function startSession(
  harness: string,
  provider: string,
  label?: string
): Promise<{ session_id: string | null; session_started: boolean }> {
  const res = await fetch(`${BASE}/session/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ harness, provider, label }),
  });
  return res.json();
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
