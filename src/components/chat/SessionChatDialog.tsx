import { useEffect, useRef } from 'react';
import type { SessionCard } from '../../lib/types';
import { SessionChat } from './SessionChat';
import '../../pages/Chat.css';

export function SessionChatDialog({ card, onClose }: { card: SessionCard; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        const focusable = document.querySelectorAll<HTMLElement>('.session-chat-dialog button:not(:disabled), .session-chat-dialog textarea:not(:disabled)');
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKey);
      previous?.focus();
    };
  }, [onClose]);

  return (
    <div className="session-chat-dialog" role="dialog" aria-modal="true" aria-label={`Chat with ${card.title || card.session_id}`}>
      <div className="session-chat-header">
        <div><strong>{card.title || 'Untitled session'}</strong><small>{card.harness} · {card.activity} · {card.session_id}</small></div>
        <button ref={closeRef} aria-label="Close chat" onClick={onClose}>×</button>
      </div>
      <SessionChat sessionId={card.session_id} active={card.activity === 'Working' || card.activity === 'Awaiting'} />
    </div>
  );
}
