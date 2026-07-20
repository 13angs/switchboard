/**
 * Extract file references from rich transcript messages.
 *
 * Scans tool_use blocks (Read, Write, Edit, NotebookEdit) and
 * returns a deduplicated list of file references ordered by
 * most-recent timestamp. Ready for future grouping modes.
 */
import type { RichMessage, RichContentBlock } from './types';

export interface FileRef {
  path: string;
  op: 'read' | 'edit' | 'write';
  ts: string;
  toolName: string;
}

const FILE_TOOLS: Record<string, FileRef['op']> = {
  Read: 'read',
  Write: 'write',
  Edit: 'edit',
  NotebookEdit: 'edit',
  // Codex / agy harnesses: generic shell-command tools that carry
  // a synthetic file_path when a file reference can be heuristically
  // extracted from the command string (codex_store._extract_file_paths_from_cmd).
  exec_command: 'edit',
  Bash: 'edit',
};

/** Extract all file references from transcript messages. */
export function extractFileRefs(messages: RichMessage[]): FileRef[] {
  const seen = new Map<string, FileRef>(); // key = path:op
  const refs: FileRef[] = [];

  for (const msg of messages) {
    for (const block of msg.content ?? []) {
      if (block.type !== 'tool_use') continue;
      const op = FILE_TOOLS[block.name ?? ''];
      if (!op) continue;
      const filePath = filePathFromInput(block);
      if (!filePath) continue;

      // Deduplicate: same path + same op → keep latest timestamp.
      const key = `${filePath}:${op}`;
      const existing = seen.get(key);
      if (existing) {
        if (msg.ts > existing.ts) {
          existing.ts = msg.ts;
        }
        continue;
      }

      const ref: FileRef = { path: filePath, op, ts: msg.ts, toolName: block.name ?? '?' };
      seen.set(key, ref);
      refs.push(ref);
    }
  }

  // Sort by most recent first.
  refs.sort((a, b) => b.ts.localeCompare(a.ts));
  return refs;
}

function filePathFromInput(block: RichContentBlock): string | null {
  const input = block.input;
  if (!input) return null;
  const fp = input['file_path'];
  if (typeof fp === 'string' && fp.length > 0) return fp;
  // NotebookEdit uses notebook_path.
  const np = input['notebook_path'];
  if (typeof np === 'string' && np.length > 0) return np;
  return null;
}

// ── Grouping helpers (future-flexible) ──

export interface FileGroup {
  key: string;
  label: string;
  icon: string;
  files: FileRef[];
}

export type GroupMode = 'operation' | 'directory' | 'time';

export function groupFileRefs(refs: FileRef[], mode: GroupMode): FileGroup[] {
  switch (mode) {
    case 'directory':
      return groupByDirectory(refs);
    case 'time':
      return groupByTime(refs);
    case 'operation':
    default:
      return groupByOperation(refs);
  }
}

const OP_ORDER: FileRef['op'][] = ['read', 'edit', 'write'];
const OP_ICON: Record<FileRef['op'], string> = { read: '📖', edit: '✏️', write: '📝' };
const OP_LABEL: Record<FileRef['op'], string> = { read: 'Read', edit: 'Edited', write: 'Written' };

function groupByOperation(refs: FileRef[]): FileGroup[] {
  const groups: Record<string, FileRef[]> = {};
  for (const r of refs) {
    if (!groups[r.op]) groups[r.op] = [];
    groups[r.op].push(r);
  }
  return OP_ORDER.filter((k) => groups[k]).map((k) => ({
    key: k,
    label: OP_LABEL[k],
    icon: OP_ICON[k],
    files: groups[k],
  }));
}

function groupByDirectory(refs: FileRef[]): FileGroup[] {
  const groups: Record<string, FileRef[]> = {};
  for (const r of refs) {
    const d = dirname(r.path);
    if (!groups[d]) groups[d] = [];
    groups[d].push(r);
  }
  return Object.entries(groups)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([dir, files]) => ({ key: dir, label: dir, icon: '📁', files }));
}

function groupByTime(refs: FileRef[]): FileGroup[] {
  const sorted = [...refs].sort((a, b) => b.ts.localeCompare(a.ts));
  const buckets: FileGroup[] = [];
  let bucket: FileGroup | null = null;
  let now = Date.now();
  for (const r of sorted) {
    const age = now - new Date(r.ts).getTime();
    let label: string;
    if (age < 5 * 60_000) label = 'Just now';
    else if (age < 15 * 60_000) label = '< 15 min ago';
    else if (age < 60 * 60_000) label = '< 1 hour ago';
    else label = 'Earlier';
    if (!bucket || bucket.label !== label) {
      bucket = { key: label, label, icon: '🕐', files: [] };
      buckets.push(bucket);
    }
    bucket.files.push(r);
  }
  return buckets;
}

function dirname(p: string): string {
  const parts = p.split('/');
  parts.pop();
  return parts.join('/') || '/';
}
