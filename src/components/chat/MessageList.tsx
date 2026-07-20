import { useEffect, useRef, useState, useCallback, type FC, type UIEvent } from 'react';
import { MessageBubble } from './MessageBubble';
import { turnOf } from '../../lib/turns';
import type { RichMessage } from '../../lib/types';

interface MessageListProps {
  messages: RichMessage[];
  typing: boolean;
}

export const MessageList: FC<MessageListProps> = ({ messages, typing }) => {
  const bottomRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const [newSinceScroll, setNewSinceScroll] = useState(false);
  const prevMessageCountRef = useRef(messages.length);

  const scrollToBottom = useCallback((smooth = true) => {
    bottomRef.current?.scrollIntoView(smooth ? { behavior: 'smooth' } : undefined);
    setUserScrolledUp(false);
  }, []);

  // Detect scroll position: if user scrolled up >50px from bottom, pause auto-scroll
  const handleScroll = useCallback((e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setUserScrolledUp(distFromBottom > 50);
  }, []);

  // Auto-scroll to bottom when new messages arrive, UNLESS user has scrolled up
  useEffect(() => {
    if (userScrolledUp) return;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, userScrolledUp]);

  // Auto-scroll when typing indicator appears (unless user scrolled up)
  useEffect(() => {
    if (userScrolledUp) return;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [typing, userScrolledUp]);

  // Track whether new messages arrived while the user was scrolled up —
  // decides the badge label (ADR-0007).
  useEffect(() => {
    const prevCount = prevMessageCountRef.current;
    prevMessageCountRef.current = messages.length;
    if (userScrolledUp && messages.length > prevCount) {
      setNewSinceScroll(true);
    }
  }, [messages.length, userScrolledUp]);

  useEffect(() => {
    if (!userScrolledUp) setNewSinceScroll(false);
  }, [userScrolledUp]);

  // Per-index turn numbers — shared with markdown export (ADR-0007). Also
  // fixes the "Turn ?" separator label: the old inline map had no entry at
  // user-message boundaries after turn 1.
  const turns = turnOf(messages);

  const getEmptyState = () => {
    if (typing) return null; // typing indicator takes precedence
    if (messages.length === 0) {
      return (
        <div className="chat-empty">
          No messages yet — switch to{' '}
          <span className="ro-empty-hint">Terminal</span> to start a conversation.
        </div>
      );
    }
    return null;
  };

  return (
    <div className="message-list" ref={listRef} onScroll={handleScroll}>
      {messages.map((m, i) => {
        const prevRole = i > 0 ? messages[i - 1].role : null;
        const isTurnBoundary = m.role === 'user' && prevRole !== null && prevRole !== 'user';
        const showTopLabel = i === 0 && m.role === 'user';

        return (
          <div key={i}>
            {(isTurnBoundary || showTopLabel) && (
              <div className="turn-sep">
                <span>Turn {turns[i]}</span>
              </div>
            )}
            <MessageBubble message={m} />
          </div>
        );
      })}

      {typing && (
        <div className="msg-row assistant">
          <div className="bubble typing-indicator">
            <span /><span /><span />
          </div>
        </div>
      )}

      {getEmptyState()}

      {userScrolledUp && (
        <button className="scroll-badge" onClick={() => scrollToBottom(true)}>
          {newSinceScroll ? '↓ New messages' : '↓ Jump to latest'}
        </button>
      )}

      <div ref={bottomRef} />
    </div>
  );
};
