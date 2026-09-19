import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { sendSessionMessage } from '../../lib/api';

export function ChatComposer({ sessionId, onSent }: { sessionId: string; onSent: () => void }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const message = text.trim();
    if (!message || pending.current) return;
    pending.current = true;
    setSending(true);
    setError('');
    try {
      await sendSessionMessage(sessionId, message);
      setText('');
      onSent();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Message could not be sent');
    } finally {
      pending.current = false;
      setSending(false);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  return (
    <form className="chat-composer" onSubmit={submit}>
      <textarea ref={inputRef} aria-label="Write a message" placeholder="Write a message…" value={text}
        onChange={(event) => setText(event.target.value)} onKeyDown={onKeyDown}
        disabled={sending} rows={2} />
      <button type="submit" className="primary" disabled={sending || !text.trim()}>
        {sending ? 'Sending…' : 'Send'}
      </button>
      {error && <div className="chat-send-error" role="alert">{error}</div>}
    </form>
  );
}
