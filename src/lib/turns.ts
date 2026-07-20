/** Turn grouping shared by the chat viewer and markdown export (ADR-0007). */

/**
 * Per-index turn number: a new turn starts at each user message that follows
 * a non-user message (or opens the transcript). Leading assistant messages
 * (rare) belong to turn 0.
 */
export function turnOf(messages: { role: string }[]): number[] {
  let turn = 0;
  let lastRole: string | null = null;
  return messages.map((m) => {
    if (m.role === 'user' && lastRole !== 'user') turn++;
    lastRole = m.role;
    return turn;
  });
}
