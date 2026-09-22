import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchSessionInteraction,
  sendSessionInteractionAction,
} from '../../lib/api';
import type { SessionInteraction } from '../../lib/types';

export function ApprovalPanel({
  sessionId,
  active = false,
  onResolved,
}: {
  sessionId: string;
  active?: boolean;
  onResolved?: () => void;
}) {
  const [interaction, setInteraction] = useState<SessionInteraction | null>(null);
  const [sending, setSending] = useState<string | null>(null);
  const [error, setError] = useState('');
  const requestRef = useRef(0);

  const refresh = useCallback(async () => {
    const request = ++requestRef.current;
    try {
      const result = await fetchSessionInteraction(sessionId);
      if (request !== requestRef.current) return;
      setInteraction(result.interaction);
      setError('');
    } catch (cause) {
      if (request !== requestRef.current) return;
      setError(cause instanceof Error ? cause.message : 'Could not check approval');
    }
  }, [sessionId]);

  useEffect(() => {
    requestRef.current++;
    setInteraction(null);
    setError('');
    void refresh();

    const timer = window.setInterval(() => void refresh(), active ? 1500 : 4000);
    const source = new EventSource('/events');
    const onApproval = (message: MessageEvent) => {
      try {
        const event = JSON.parse(message.data) as { session_id?: string | null };
        if (event.session_id === sessionId) void refresh();
      } catch {
        // Polling remains the fallback/source of truth.
      }
    };
    source.addEventListener('approval_required', onApproval);

    return () => {
      window.clearInterval(timer);
      source.removeEventListener('approval_required', onApproval);
      source.close();
      requestRef.current++;
    };
  }, [active, refresh, sessionId]);

  const submit = async (actionId: string) => {
    if (!interaction || sending) return;
    setSending(actionId);
    setError('');
    try {
      await sendSessionInteractionAction(sessionId, actionId, interaction.fingerprint);
      setInteraction(null);
      onResolved?.();
      void refresh();
    } catch (cause) {
      const err = cause as Error & { status?: number };
      if (err.status === 409) {
        await refresh();
        setError('Approval changed or was already answered.');
      } else {
        setError(err.message || 'Approval action failed');
      }
    } finally {
      setSending(null);
    }
  };

  if (!interaction && !error) return null;

  const terminalUrl = `/agent?view=terminal&session_id=${encodeURIComponent(sessionId)}`;

  return (
    <section className="approval-panel" aria-live="polite">
      {interaction && (
        <>
          <div className="approval-copy">
            <strong>Approval required</strong>
            <span>{interaction.prompt_summary || 'The agent is waiting for approval.'}</span>
            {interaction.actions.length === 0 && (
              <small>This prompt is not safe to answer from Chat yet. Use Terminal.</small>
            )}
          </div>
          <div className="approval-actions">
            {interaction.actions.map((action) => (
              <button
                key={action.id}
                type="button"
                className={action.intent === 'approve' ? 'primary' : ''}
                disabled={sending !== null}
                onClick={() => void submit(action.id)}
              >
                {sending === action.id ? 'Sending…' : action.label}
              </button>
            ))}
            <a
              className="approval-terminal"
              href={terminalUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open Terminal
            </a>
          </div>
        </>
      )}
      {error && <div className="approval-error" role="alert">{error}</div>}
    </section>
  );
}
