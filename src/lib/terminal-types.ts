import type { PullRequestRef } from './prs';

/** Session data for the terminal/chat pages — subset of SessionCard relevant
 *  to the active session view. Mirrors what the server returns via /state
 *  (filtered to the current session_id). */
export interface SessionInfo {
  session_id: string;
  title: string;
  activity: string;
  harness: string | null;
  provider: string | null;
  pr_number: number | null;
  pr_url: string | null;
  pr_state: string | null;
  pull_requests?: PullRequestRef[];
  git_branch: string | null;
  worktree_path: string | null;
  dismissed: boolean;
  auto_archived: boolean;
  turn_count: number;
  total_cost_usd: number | null;
  /** Cost state — ADR-0022 §SD2/§SD4. See SessionCard for the full contract. */
  cost_partial?: boolean;
  unpriced_models?: string[] | null;
  age: string;
  status: 'connecting' | 'connected' | 'ended' | 'waiting';
  /** Session health score — Branch C, ADR-0016. null when jsonl unreadable. */
  health?: import('./types').HealthScore | null;
}

/** Sample file entry for the Files panel (prototype uses static data;
 *  real implementation will read from git diff in the worktree). */
export interface FileEntry {
  name: string;
  adds: number;
  dels: number;
  staged: boolean;
}

/** Status for StatusGlyph / StatusBar */
export type SessionStatus = 'connecting' | 'connected' | 'ended' | 'waiting';
export type SessionDrawerView = 'active' | 'archive';
export type SessionAlertKind = 'approval' | 'ready';

export interface SessionAlert {
  kind: SessionAlertKind;
  label: string;
}

// ── Shared component props ──

export interface TopbarProps {
  session: SessionInfo | null;
  sessionAlert?: SessionAlert | null;
  leftOpen: boolean;
  rightOpen: boolean;
  agentView?: 'terminal' | 'chat' | 'files';
  onAgentViewChange?: (view: 'terminal' | 'chat' | 'files') => void;
  showChatToggle?: boolean;
  onToggleLeft: () => void;
  onToggleRight: () => void;
  onChat?: () => void;
  onKill: () => void;
  onClose: () => void;
}

export interface LeftDrawerProps {
  open: boolean;
  sessions: SessionInfo[];
  activeSessionId: string | null;
  view: SessionDrawerView;
  onViewChange: (view: SessionDrawerView) => void;
  onSelectSession: (sessionId: string) => void;
}

export interface RightDrawerProps {
  open: boolean;
  session: SessionInfo | null;
  pullRequests?: PullRequestRef[];
  view?: 'terminal' | 'files';
  onViewChange?: (v: 'terminal' | 'files') => void;
  /** Omit ViewToggle entirely (Chat page, HLD §3.4). */
  hideViewToggle?: boolean;
}

export interface StatusBarProps {
  status: SessionStatus;
  sessionId: string | null;
}
