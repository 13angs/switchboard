/** Markdown → HTML renderer using marked.
 *  Ported from chat.js _mdToHtml(). */
import { marked } from 'marked';

/** Parse markdown text → HTML string. */
export function renderMarkdown(text: string): string {
  try {
    // marked.parse returns string | Promise<string>. In sync mode with
    // marked v15+, it's always sync for string input → returns string.
    const html = marked.parse(text, { breaks: true, gfm: true }) as string;
    return html;
  } catch {
    return escapeHtml(text).replace(/\n/g, '<br>');
  }
}

export function escapeHtml(s: string): string {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function formatTimestamp(raw: string | null | undefined): string {
  if (!raw) return '';
  try {
    const d = new Date(raw);
    if (isNaN(d.getTime())) return '';
    return [d.getHours(), d.getMinutes(), d.getSeconds()]
      .map((n) => String(n).padStart(2, '0'))
      .join(':');
  } catch {
    return '';
  }
}
