/**
 * Raw transcript serialization for the JSON view (ADR-0015 §SD3).
 *
 * Split out of the component so the pretty-print contract and the
 * circular-reference guard are checkable without a DOM — see
 * `raw-json.check.ts`.
 */
import type { RichMessage } from './types';

export type SerializeResult =
  | { ok: true; text: string }
  | { ok: false; error: string };

/**
 * Pretty-print the transcript exactly as fetched — 2-space indent, no
 * transformation (§SD3 / forge B3: the raw view's value is that it hides
 * nothing the bubbles drop).
 *
 * `JSON.stringify` throws on a circular reference; ADR-0015 § Risk requires
 * that to surface as an error banner rather than take the chat page down.
 */
export function serializeMessages(messages: RichMessage[]): SerializeResult {
  try {
    return { ok: true, text: JSON.stringify(messages, null, 2) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
