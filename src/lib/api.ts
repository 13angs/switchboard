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
  /** Pins the thinking depth (ADR-0032) — `low`/`medium`/`high`/`xhigh`/`max`.
   *  Fresh spawns only, same rule as `model`. */
  effort?: string;
  /** Typed into the PTY. Submitted too when `model` is also given — the
   *  board's dispatch dialog signature (ADR-0034 §SD1, ADR-0038 §SD2) —
   *  otherwise left unsent for a person to press Enter on. */
  prompt?: string;
}

export interface StartSessionResponse {
  session_id: string | null;
  /** Server-issued identity for the PTY, present even before session_id is
   *  known (ADR-0028 §SD1). Only grill's new-tab dispatch still carries this
   *  to where it navigates next (ADR-0038 §SD3) — every other shape stays on
   *  /work and has nothing to attach. */
  attach_key: string | null;
  session_started: boolean;
  harness?: string;
  provider?: string;
  model?: string | null;
  effort?: string | null;
  prompt_typed?: boolean;
  /** Whether the submit key reached the PTY too (ADR-0034 §SD4) — only
   *  meaningful when `model` was given; `undefined`/`false` otherwise. Not
   *  surfaced anywhere in the UI by design (ADR-0038 §SD4). */
  prompt_submitted?: boolean;
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
  /** Effective role for this row — its own `role` column cell when the file
   *  has one and it resolves, else the project's `default_role` (ADR-0035). */
  role: string | null;
  /** Sibling rows (same file) this one is still waiting on — `[]` when the
   *  file has no `blocked-by` column, the cell is empty, or every id it named
   *  has since closed (S38, row-status.md § ลำดับก่อนหลัง). Non-empty means
   *  the dispatch button must stay off regardless of `column`: a row sitting
   *  in `todo` with an open blocker has not started, it *cannot* start yet. */
  blocked_by: { id: string; title: string }[];
  /** Set when this row's `role` resolved to something different than it did
   *  at the parent of this file's own last commit (S40) — `null` when the
   *  role is unchanged, the row is new, or there is no prior commit to
   *  compare against. The board announces this itself; nothing writes it
   *  back to `slices.md`. */
  handoff: { from: string; to: string } | null;
  /** Opt-in `stage` column — one of the nine belt stations, or `""` when the
   *  file has no such column, the cell is blank, or the value is not a
   *  declared station (row-status.md § สายพาน · route-lint Check 9). */
  stage: string;
  /** Opt-in `part-of` column — the `#` of the row this one is an acceptance
   *  criterion OF. `""` when this row is a card in its own right. */
  part_of: string;
  /** Set on a PARENT row: how many of its criteria are closed out of how many
   *  exist, counted from the rows pointing here. `null` means "not a parent",
   *  which is a different answer from `{done: 0, total: 0}`. */
  criteria: { done: number; total: number } | null;
  /** The two axes disagreeing — `"stuck-open"` (belt says done, glyph says
   *  open) or `"skipped-gate"` (glyph says closed from a station that is not
   *  `done`). The board prints it; nothing writes a correction back. */
  axis_conflict: 'stuck-open' | 'skipped-gate' | null;
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
  /** One key per slot team-os declares, in declared order (ADR-0040 §SD4).
   *  `scope`/`risks`/`hld` are always among them — those three are also the
   *  fallback set when the declaration cannot be read. */
  has: Record<string, boolean>;
}

/** The files team-os says a project should carry, read from
 *  `team-os/projects/README.md` (ADR-0040 §SD2 · §SD4). `source: "fallback"`
 *  means that table could not be read and `has` is the original three. */
export interface WorkspaceSlots {
  source: 'declared' | 'fallback';
  reason: string;
  slots: { key: string; kind: 'file' | 'dir'; where: string }[];
  /** Template rows that name no file, so the board does not check them —
   *  reported rather than dropped (§SD3). */
  unmapped: string[];
}

/** Role → tier → model, read from the workspace (ADR-0030 §SD1). Never held here. */
export type WorkspaceDispatch =
  | { present: false; reason: string }
  | {
      present: true;
      tiers: Record<string, string>;
      roles: {
        role: string;
        /** Off the same roles.md row as the tier (S37) — `""` when
         *  § แกนความเป็นเจ้าของ does not carry this role. */
        office: string;
        tier: string;
        model: string;
        effort: string | null;
      }[];
      source: { tiers: string; roles: string };
    };

/** One station of the delivery belt and the roles that hold its discipline
 *  (ADR-0041 §SD2 · §SD3). `roles: []` is a real answer, not a missing one:
 *  `close` is a gate, and the SOP says it will never have a discipline leaf. */
export interface PipelineStation {
  stage: string;
  roles: string[];
}

/** Where one role signs when a project closes, and how much of the workspace
 *  already carries that surface (ADR-0041 §SD6). */
export interface PipelineSignature {
  role: string;
  closes: string;
  /** File-shaped locations named by the row, already counted. */
  targets: {
    token: string;
    /** `project` counts folders under `projects/`; `workspace` counts files. */
    level: 'project' | 'workspace';
    have: number;
    /** The denominator — `null` for a workspace-level file count. */
    total: number | null;
  }[];
  /** Prose left in the cell after its path tokens — carried from the SOP
   *  verbatim, which is how §7.3's "another file may answer this" reaches the
   *  screen without being retyped here. */
  note: string;
  /** Tokens refused for walking outside the workspace — reported, not dropped. */
  rejected: string[];
}

/** Which of the seven signature lines a machine actually holds, read from
 *  §7.2's own warning paragraph. `unenforced` is computed from `roles`, never
 *  lifted from the sentence's own count (ADR-0041 §SD6). */
export interface PipelineEnforcement {
  declared: boolean;
  roles: string[];
  mechanism: string;
  mechanism_exists: boolean;
  enforced: number;
  unenforced: number;
  total: number;
}

/** The two belt columns of the role panel. Both halves fail independently:
 *  a renamed stage column blanks `stations` and leaves `signatures` readable
 *  (ADR-0041 §SD7). */
export interface WorkspacePipeline {
  source: string;
  stations: {
    present: boolean;
    reason: string;
    stages: PipelineStation[];
    /** role slug → the stages it holds, `[]` when it holds none. */
    per_role: Record<string, string[]>;
  };
  signatures: {
    present: boolean;
    reason: string;
    rows: PipelineSignature[];
    /** Every folder under `projects/` — the close gate's denominator, which is
     *  deliberately wider than the board's own card count (§SD6). */
    projects: number;
    enforcement: PipelineEnforcement;
  };
}

/** How much of the tree one pattern from the register actually points at.
 *  `paths` is capped and `more` says how many were left off — the column is
 *  evidence that a place exists, not a file browser (ADR-0043 §SD3). */
export interface RegisterLevel {
  have: number;
  paths: string[];
  more: number;
  /** `project` level only: how many folders under `projects/` carry it. */
  projects?: number;
  /** `project` level only: the same matches split per project, so the picker
   *  can answer one project exactly. Not derivable in the browser — `paths` is
   *  a capped sample, and filtering a sample undercounts (ADR-0043 Amendment,
   *  S34). */
  by_project?: { project: string; have: number; paths: string[]; more: number }[];
}

/** One location named by a role's `บันทึกผลลงที่` cell, resolved at **both**
 *  levels — always. `docs/runbooks/` is real at the workspace root and under a
 *  project, so stopping at the first hit would delete half the answer. */
export interface RegisterFileTarget {
  kind: 'file';
  token: string;
  /** The token after `<n>`-style placeholders become `*`. Printed beside the
   *  raw token so the substitution is never invisible. */
  glob: string;
  levels: { workspace: RegisterLevel; project: RegisterLevel };
}

/** A `surface:<slug>` token (ADR-0043 Amendment (2) §A6) — a place this role
 *  records into that is not a file in the tree (a PR body, an external doc).
 *  Not glob'd, not counted: the board reads only files committed at HEAD, and
 *  a surface's real location is not one (§A8). */
export interface RegisterSurfaceTarget {
  kind: 'surface';
  token: string;
  slug: string;
}

export type RegisterTarget = RegisterFileTarget | RegisterSurfaceTarget;

/** The register's fourth column and what the tree says about it.
 *
 *  `kind: "not-files"` is a real answer, not a missing one (ADR-0043 §SD4):
 *  `senior-developer` and `developer` close in a commit body and `qa` in a PR
 *  thread, so an empty `targets` there means *the register does not ask for a
 *  file* — never *the file is not written yet*. `"mixed"` (Amendment (2) §A5)
 *  is a cell that names both a file and a surface — `tech-lead` and
 *  `product-owner` are exactly that. */
export interface RegisterRecords {
  raw: string;
  text: string;
  kind: 'files' | 'not-files' | 'mixed';
  targets: RegisterTarget[];
  /** Tokens refused for walking outside the workspace — reported, not dropped. */
  rejected: string[];
  /** Prose left in the cell after its path/surface tokens, verbatim from
   *  roles.md (§A7 — a token that is neither must survive here, not vanish). */
  note: string;
}

/** One row of `roles.md § แกนความเป็นเจ้าของ`, every column of it. */
export interface RegisterRole {
  role: string;
  office: string;
  disciplines: string[];
  /** The discipline cell as roles.md writes it — printed beside the parsed
   *  result so a wrong filter is visible rather than silent. */
  disciplines_raw: string;
  /** Text in that cell that is not discipline-shaped (`qa`'s cell points at
   *  `ways-of-working/` after an em-dash). Reported, never silently binned. */
  disciplines_dropped: string[];
  records: RegisterRecords;
}

/** The seven-row register the second screen prints (ADR-0043). Joined to
 *  `dispatch` in the browser, which is where the tier half already lives. */
export interface WorkspaceRegister {
  source: string;
  section: string;
  present: boolean;
  reason: string;
  roles: RegisterRole[];
  /** S19's column, declared unread rather than parsed on the way past
   *  (ADR-0043 §SD6). `readable` is false today and the screen says so. */
  heavy_when: {
    readable: boolean;
    column: string;
    slice: string;
    reason: string;
  };
}

export interface WorkspaceResponse {
  generated_at: string;
  repo: string;
  head: string;
  stale_by: string;
  slots: WorkspaceSlots;
  projects: WorkspaceProject[];
  totals: {
    projects_with_slices: number;
    slices: Record<string, number>;
  };
  gaps:
    | { present: false }
    | { present: true; total: number; closed: number; reduced: number; open: number };
  dispatch: WorkspaceDispatch;
  pipeline: WorkspacePipeline;
  register: WorkspaceRegister;
  /** Every row across every project whose `role` just changed (S40),
   *  flattened so the board can announce a handoff without anyone having to
   *  open a project and scan its columns to notice one. */
  handoffs: { project: string; id: string; title: string; from: string; to: string }[];
}


export async function fetchWorkspace(refresh = false): Promise<WorkspaceResponse> {
  const res = await fetch(`${BASE}/workspace${refresh ? '?refresh=1' : ''}`);
  if (!res.ok) throw new Error(`workspace ${res.status}`);
  return res.json();
}

// ── Daily calendar (slices.md S9) ──

/** One row of team-os/ways-of-working/rituals.md § เจ้าของของแต่ละจังหวะ —
 *  the second register the board may dispatch from (ADR-0036 §SD1).
 *  `dispatchable` is false when `role`, `client` or the office behind them did
 *  not resolve; the board then shows the row and *why*, and no button — there
 *  is no default for any of those fields (§SD3). */
export interface Ritual {
  key: string;
  name: string;
  role: string | null;
  client: string;
  office: string | null;
  /** `<client>/<office>/<role>/<key>` — four full segments, no `-` (§SD3). */
  assignment: string | null;
  /** Where the ritual is defined (runbook + step numbers), workspace-relative. */
  reads: string;
  dispatchable: boolean;
  missing: string[];
}

export interface ScheduleBlock {
  start: string; // "HH:MM"
  end: string; // "HH:MM"
  label: string;
  domain: string;
  minutes: number | null;
  /** The ritual this bar carries, or null when its label names no key. */
  ritual: Ritual | null;
  /** Keys that all matched this one label — the bar gets no button, because
   *  choosing between them would be a guess (ADR-0036 §SD2). */
  ritual_conflict: string[] | null;
}

export interface DaySchedule {
  date: string; // "YYYY-MM-DD"
  present: boolean;
  blocks: ScheduleBlock[];
  /** Rituals that declare a key but matched no bar this day (ADR-0036 §SD6).
   *  Read from the day's *plan* — it does not mean "not run yet". */
  unmapped: Ritual[];
}

export interface CalendarResponse {
  center: string;
  days: DaySchedule[];
  rituals: {
    present: boolean;
    reason: string;
    source: string;
    declared: number;
  };
}

export async function fetchCalendar(
  center?: string,
  before = 2,
  after = 2,
): Promise<CalendarResponse> {
  const qs = new URLSearchParams();
  if (center) qs.set('date', center);
  qs.set('before', String(before));
  qs.set('after', String(after));
  const res = await fetch(`${BASE}/calendar?${qs.toString()}`);
  if (!res.ok) throw new Error(`calendar ${res.status}`);
  return res.json();
}

// ── Role participation (ADR-0039) ──

/** One of the 7 roles of `roles.md § แกนความเป็นเจ้าของ` — always all seven,
 *  including the ones that shipped nothing: `0 / 0` is the answer S26 asks for,
 *  so it has to be a row rather than an absence (§SD2). */
export interface RoleActivityRow {
  /** Slug as it appears in the third slot of an `Assignment:` id. */
  role: string;
  office: string | null;
  /** `direct + legacy` — commits in the window, not pieces of work (§ เสีย). */
  commits: number;
  /** The id already named one of the 7 roles. */
  direct: number;
  /** The id named a `team/` discipline (every id before the 2026-09-05
   *  cutover does) and was resolved through the board's own resolver (§SD3). */
  legacy: number;
  /** Which discipline slugs the `legacy` count came from, and how many each. */
  legacy_slugs: Record<string, number>;
  /** Rows still open, per board column. `todo` leads; `owner` is printed
   *  separately because the 🖐️ mark beats the status glyph (§SD6). */
  open: Record<string, number>;
  open_total: number;
  /** No commits *and* no open rows — the gap G-25 went looking for. */
  silent: boolean;
}

export interface RoleActivityRepo {
  /** Workspace-relative, `.` for the workspace itself. */
  path: string;
  commits: number;
  /** The difference from `commits` is what no role was credited for (§SD5). */
  with_assignment: number;
}

export type RoleActivityResponse =
  | { present: false; reason: string; source: string; repo: string }
  | {
      present: true;
      generated_at: string;
      repo: string;
      source: string;
      window: { days: number; since: string; tz: string };
      /** The project the numbers were scoped to, or null for the whole workspace. */
      project: string | null;
      roles: RoleActivityRow[];
      repos: RoleActivityRepo[];
      unresolved: {
        count: number;
        samples: { sha: string; repo: string; assignment: string }[];
      };
      open_unassigned: Record<string, number>;
      totals: { commits: number; with_assignment: number };
    };

/** `project` scopes the count by path (ADR-0040 §SD5) — an unknown name is a
 *  400, never an empty panel that would read like "this role shipped nothing". */
export async function fetchRoleActivity(
  days: number,
  project?: string,
): Promise<RoleActivityResponse> {
  const qs = new URLSearchParams({ days: String(days) });
  if (project) qs.set('project', project);
  const res = await fetch(`${BASE}/roles/activity?${qs.toString()}`);
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.error || `roles/activity ${res.status}`);
  return data as RoleActivityResponse;
}
