/** API contract types — mirrors control_plane/discovery.py SessionCard.to_dict() */

export type Activity = 'Working' | 'Awaiting' | 'Idle' | 'Blocked';

export interface SessionCard {
  session_id: string;
  title: string;
  activity: Activity;
  last_ts: string | null;
  turn_count: number;
  total_cost_usd: number | null;
  pr_number: number | null;
  pr_url: string | null;
  pr_state: string | null;
  git_branch: string | null;
  worktree_path: string | null;
  jsonl_path: string | null;
  harness: string | null;
  provider: string | null;
  dismissed: boolean;
  auto_archived: boolean;
  merged_pr: string | null;
  note_kind: string | null;
  note_text: string | null;
  note_code: string | null;
  note_tail: string | null;
  /** Session health score — Branch C, ADR-0016. null when jsonl unreadable. */
  health?: HealthScore | null;
}

export interface BoardState {
  generated_at: string;
  repo: string;
  activities: string[];
  launchers: Launcher[];
  providers: string[];
  sessions: SessionCard[];
}

export interface Launcher {
  harness: string;
  providers: string[];
}

export interface TranscriptMessage {
  role: string;
  text: string;
  ts: string;
}

export interface Transcript {
  session_id: string;
  messages: TranscriptMessage[];
}

// ── Rich transcript (ADR-0006) ──

export interface RichContentBlock {
  type: 'text' | 'thinking' | 'tool_use' | 'tool_result' | string;
  text?: string;
  thinking?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  content?: string | RichContentBlock[];
  [key: string]: unknown; // defensive: unknown block types pass through
}

export interface RichMessage {
  role: string;
  ts: string;
  content: RichContentBlock[];
  model?: string;
  stop_reason?: string;
  usage?: {
    input_tokens?: number | null;
    output_tokens?: number | null;
  };
}

export interface RichTranscript {
  session_id: string;
  messages: RichMessage[];
}

export interface SessionStartResponse {
  session_id: string | null;
  session_started: boolean;
  message?: string;
}

export interface OkResponse {
  ok: boolean;
  session_id?: string;
  session_ids?: string[];
  count?: number;
  killed?: boolean;
}

// ── Health Score (Branch C, ADR-0016) ──

export interface HealthScore {
  status: 'healthy' | 'warning' | 'unhealthy';
  stale: 'healthy' | 'warning' | 'unhealthy';
  loop: 'healthy' | 'warning' | 'unhealthy';
  error: 'healthy' | 'warning' | 'unhealthy';
  stale_hrs: number;
  loop_count: number;
  error_count: number;
  error_total: number;
}

export interface NotificationEvent {
  type: 'approval_required' | 'input_ready' | string;
  session_id: string | null;
  harness: string;
  provider: string;
  title: string | null;
  detected_at: string;
  fingerprint: string;
}
