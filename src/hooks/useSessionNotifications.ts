import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { NotificationEvent } from '../lib/types';
import type { SessionAlert } from '../lib/terminal-types';

function alertForEvent(event: NotificationEvent): SessionAlert {
  if (event.type === 'input_ready') {
    return { kind: 'ready', label: 'Ready' };
  }
  return { kind: 'approval', label: 'Needs approval' };
}

function titlePrefix(alert: SessionAlert): string {
  return alert.kind === 'ready' ? '[Ready]' : '[Needs approval]';
}

export function useSessionNotifications(
  sessionId: string | null,
  titleShort: string,
  onEvent?: (event: NotificationEvent) => void
): SessionAlert | null {
  const [sessionAlert, setSessionAlert] = useState<SessionAlert | null>(null);
  const seenRef = useRef<Set<string>>(new Set());
  const pendingBySessionRef = useRef<Map<string, NotificationEvent>>(new Map());
  const baseTitle = `${titleShort} - Agent View`;

  const clearAlert = useCallback(() => {
    setSessionAlert(null);
  }, []);

  const applyEvent = useCallback(
    (event: NotificationEvent) => {
      onEvent?.(event);
      setSessionAlert(alertForEvent(event));
    },
    [onEvent]
  );

  useEffect(() => {
    setSessionAlert(null);
    seenRef.current.clear();
    if (!sessionId) return;
    const pending = pendingBySessionRef.current.get(sessionId);
    if (!pending) return;
    pendingBySessionRef.current.delete(sessionId);
    applyEvent(pending);
  }, [applyEvent, sessionId]);

  useEffect(() => {
    const source = new EventSource('/events');
    const handleEvent = (message: MessageEvent) => {
      try {
        const event = JSON.parse(message.data) as NotificationEvent;
        if (!event.fingerprint || seenRef.current.has(event.fingerprint)) return;
        if (!sessionId && event.session_id) {
          pendingBySessionRef.current.set(event.session_id, event);
          seenRef.current.add(event.fingerprint);
          return;
        }
        if (event.session_id !== sessionId) return;
        seenRef.current.add(event.fingerprint);
        applyEvent(event);
      } catch {
        // Ignore malformed events; the active harness tab should keep running.
      }
    };

    source.addEventListener('approval_required', handleEvent);
    source.addEventListener('input_ready', handleEvent);
    source.onerror = () => {
      // EventSource retries by itself.
    };
    return () => {
      source.removeEventListener('approval_required', handleEvent);
      source.removeEventListener('input_ready', handleEvent);
      source.close();
    };
  }, [applyEvent, sessionId]);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    document.title = sessionAlert ? `${titlePrefix(sessionAlert)} ${baseTitle}` : baseTitle;
    return () => {
      document.title = baseTitle;
    };
  }, [baseTitle, sessionAlert]);

  useEffect(() => {
    if (!sessionAlert || typeof window === 'undefined') return;
    const clearWhenVisible = () => {
      if (document.visibilityState === 'visible') {
        clearAlert();
      }
    };
    window.addEventListener('focus', clearWhenVisible);
    window.addEventListener('pointerdown', clearWhenVisible);
    window.addEventListener('keydown', clearWhenVisible);
    return () => {
      window.removeEventListener('focus', clearWhenVisible);
      window.removeEventListener('pointerdown', clearWhenVisible);
      window.removeEventListener('keydown', clearWhenVisible);
    };
  }, [clearAlert, sessionAlert]);

  return useMemo(() => sessionAlert, [sessionAlert]);
}
