import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { fetchRichTranscript } from '../../lib/api';
import type { RichMessage } from '../../lib/types';
import { StateBanner } from './StateBanner';
import { MessageList } from './MessageList';
import { ChatComposer } from './ChatComposer';
import { ApprovalPanel } from './ApprovalPanel';

type ChatState = 'loading' | 'error' | 'ready' | 'ended' | 'connecting' | null;
interface ExternalTranscript {
  messages: RichMessage[];
  state: ChatState;
  error: string;
  typing: boolean;
  refresh: () => void;
  content?: ReactNode;
  footer?: ReactNode;
}

/** Route-independent transcript and composer. Agent may supply its existing transcript state. */
export function SessionChat({ sessionId, active = false, external }: {
  sessionId: string | null;
  active?: boolean;
  external?: ExternalTranscript;
}) {
  const [messages, setMessages] = useState<RichMessage[]>([]);
  const [state, setState] = useState<ChatState>('loading');
  const [error, setError] = useState('');
  const requestRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!sessionId || external) return;
    const request = ++requestRef.current;
    try {
      const result = await fetchRichTranscript(sessionId);
      if (request !== requestRef.current) return;
      setMessages(result.messages ?? []);
      setState('ready');
      setError('');
    } catch (cause) {
      if (request !== requestRef.current) return;
      setState('error');
      setError(cause instanceof Error ? cause.message : 'Failed to load messages');
    }
  }, [sessionId, external]);

  useEffect(() => {
    if (external) return;
    requestRef.current++;
    setMessages([]);
    setState('loading');
    void refresh();
    const timer = window.setInterval(() => void refresh(), active ? 2000 : 5000);
    return () => { window.clearInterval(timer); requestRef.current++; };
  }, [sessionId, active, external, refresh]);

  const displayed = external?.messages ?? messages;
  const chatState = external?.state ?? state;
  const chatError = external?.error ?? error;
  const onSent = () => {
    if (external) external.refresh();
    else void refresh();
  };

  return (
    <div className="chat-body session-chat">
      <StateBanner state={chatState} errorMessage={chatError} />
      {external?.content ?? <MessageList messages={displayed} typing={external?.typing ?? false} />}
      {sessionId && (
        <ApprovalPanel
          key={`approval-${sessionId}`}
          sessionId={sessionId}
          active={active}
          onResolved={onSent}
        />
      )}
      {sessionId && <ChatComposer key={sessionId} sessionId={sessionId} onSent={onSent} />}
      {external?.footer}
    </div>
  );
}
