/**
 * Transcript → markdown serialization (ADR-0007).
 *
 * Shared by the per-message copy button and the full-transcript export —
 * one serializer so the two can never disagree. Pure functions, no DOM.
 */
import type { RichMessage, RichContentBlock } from './types';
import { costStr } from './format';
import { turnOf } from './turns';

/** Short summary of a tool_use block for inline display. (Also used by MessageBubble.) */
export function toolUseLabel(block: RichContentBlock): string {
  const name = block.name || '?';
  const input = block.input;
  if (!input || Object.keys(input).length === 0) return name;
  // Show the first key=value pair as a hint.
  const firstKey = Object.keys(input)[0];
  const val = input[firstKey];
  const valStr = typeof val === 'string' ? (val.length > 60 ? val.slice(0, 60) + '…' : val) : JSON.stringify(val);
  return `${name}(${firstKey}=${valStr})`;
}

/** Summary line for a tool_result block. (Also used by MessageBubble.) */
export function toolResultSummary(block: RichContentBlock): string {
  const content = block.content;
  if (typeof content === 'string') {
    const lines = content.split('\n').length;
    const chars = content.length;
    return `${chars} chars · ${lines} lines`;
  }
  if (Array.isArray(content)) {
    return `${content.length} blocks`;
  }
  return 'result';
}

/** Fence content with enough backticks to survive embedded fences. */
function fence(text: string, lang = ''): string {
  const longest = (text.match(/`+/g) ?? []).reduce((n, s) => Math.max(n, s.length), 0);
  const f = '`'.repeat(Math.max(3, longest + 1));
  return `${f}${lang}\n${text}\n${f}`;
}

function blockToMarkdown(block: RichContentBlock): string {
  switch (block.type) {
    case 'text':
      return block.text || '';
    case 'thinking':
      return (block.thinking || '')
        .split('\n')
        .map((line, i) => (i === 0 ? `> 💭 ${line}` : `> ${line}`))
        .join('\n');
    case 'tool_use':
      // Inline code — strip backticks from the label so it cannot break out.
      return `🔧 \`${toolUseLabel(block).replace(/`/g, "'")}\``;
    case 'tool_result': {
      const body =
        typeof block.content === 'string' ? block.content : JSON.stringify(block.content ?? '', null, 2);
      return `<details><summary>📋 ${toolResultSummary(block)}</summary>\n\n${fence(body)}\n\n</details>`;
    }
    default:
      // Unknown block types → fenced JSON stub, never dropped (ADR-0007).
      return fence(JSON.stringify(block, null, 2), 'json');
  }
}

function timeOf(ts: string): string {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleTimeString([], { hour12: false });
}

/** One message → markdown: role header + all blocks (full markdown — ADR-0007). */
export function messageToMarkdown(m: RichMessage): string {
  const who = m.role === 'user' ? 'User' : m.role === 'assistant' ? 'Assistant' : m.role;
  const header = `**${who}**${m.ts ? ` (${timeOf(m.ts)})` : ''}`;
  const body = (m.content ?? []).map(blockToMarkdown).filter(Boolean).join('\n\n');
  return body ? `${header}\n${body}` : header;
}

export interface TranscriptMeta {
  title: string;
  harness: string | null;
  provider: string | null;
  totalCostUsd: number | null;
}

/** Full transcript → one markdown document grouped under `## Turn N` headings. */
export function messagesToMarkdown(messages: RichMessage[], meta: TranscriptMeta): string {
  const model = [...messages].reverse().find((m) => m.model)?.model;
  const infoLine = [
    `**Harness:** ${meta.harness ?? '—'}`,
    `**Provider:** ${meta.provider ?? '—'}`,
    model ? `**Model:** ${model}` : '',
    meta.totalCostUsd != null ? `**Cost:** ${costStr(meta.totalCostUsd)}` : '',
  ]
    .filter(Boolean)
    .join(' | ');

  const turns = turnOf(messages);
  const parts: string[] = [`# Session: ${meta.title}\n${infoLine}`];
  messages.forEach((m, i) => {
    if (i === 0 || turns[i] !== turns[i - 1]) {
      parts.push(`---\n\n## Turn ${turns[i]}`);
    }
    parts.push(messageToMarkdown(m));
  });
  return parts.join('\n\n') + '\n';
}
