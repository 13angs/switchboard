import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { NotificationEvent } from '../lib/types';

type ToastFn = (message: string) => void;

function eventLabel(event: NotificationEvent): string {
  if (event.title) return event.title;
  if (event.session_id) return event.session_id.slice(0, 12);
  return `${event.harness}/${event.provider}`;
}

function notificationCopy(event: NotificationEvent): string {
  if (event.type === 'input_ready') {
    return 'Session looks ready';
  }
  return 'Needs approval';
}

export function useNotifications(toast: ToastFn) {
  const [events, setEvents] = useState<NotificationEvent[]>([]);
  const seenRef = useRef<Set<string>>(new Set());
  const titleRef = useRef(typeof document !== 'undefined' ? document.title : 'Agent View');

  const unreadCount = events.length;

  const markSessionRead = useCallback((sessionId: string | null) => {
    if (!sessionId) {
      setEvents([]);
      return;
    }
    setEvents((prev) => prev.filter((event) => event.session_id !== sessionId));
  }, []);

  useEffect(() => {
    const source = new EventSource('/events');

    const handleEvent = (message: MessageEvent) => {
      try {
        const event = JSON.parse(message.data) as NotificationEvent;
        if (!event.fingerprint || seenRef.current.has(event.fingerprint)) return;
        seenRef.current.add(event.fingerprint);
        setEvents((prev) => [...prev, event]);

        const label = eventLabel(event);
        const copy = notificationCopy(event);
        toast(`${copy} - ${label}`);
      } catch {
        // Ignore malformed event payloads; the board should keep running.
      }
    };

    source.addEventListener('approval_required', handleEvent);
    source.addEventListener('input_ready', handleEvent);
    source.onerror = () => {
      // EventSource retries by itself. Keep the board usable if /events is down.
    };
    return () => {
      source.removeEventListener('approval_required', handleEvent);
      source.removeEventListener('input_ready', handleEvent);
      source.close();
    };
  }, [toast]);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    document.title = unreadCount > 0
      ? `(${unreadCount}) ${titleRef.current}`
      : titleRef.current;
    return () => {
      document.title = titleRef.current;
    };
  }, [unreadCount]);

  return useMemo(
    () => ({
      unreadCount,
      markSessionRead,
    }),
    [markSessionRead, unreadCount]
  );
}
