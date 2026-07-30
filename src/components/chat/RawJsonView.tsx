import { useEffect, useMemo, useState, type FC } from 'react';
import { serializeMessages } from '../../lib/raw-json';
import type { RichMessage } from '../../lib/types';

interface RawJsonViewProps {
  messages: RichMessage[];
  /** Optional notification hook — the clipboard write itself happens here, so
   *  the parent does not have to re-serialize the transcript to copy it
   *  (deviation from ADR-0015 §SD3's prop shape; see the PR body). */
  onCopy?: () => void;
}

export const RawJsonView: FC<RawJsonViewProps> = ({ messages, onCopy }) => {
  // Memoized: ADR-0015 § Consequences flags stringify cost on long transcripts,
  // so it must not re-run on the copied-flag render.
  const result = useMemo(() => serializeMessages(messages), [messages]);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);

  if (!result.ok) {
    return (
      <div className="raw-json-view">
        <div className="raw-json-error">
          Could not serialize this transcript: {result.error}
        </div>
      </div>
    );
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(result.text).then(
      () => {
        setCopied(true);
        onCopy?.();
      },
      () => {
        /* clipboard denied (insecure context / permission) — leave the label */
      }
    );
  };

  return (
    <div className="raw-json-view">
      <button
        className={`copy-json-btn${copied ? ' copied' : ''}`}
        onClick={handleCopy}
        title="Copy the raw transcript JSON"
      >
        {copied ? '✓ Copied!' : '📋 Copy'}
      </button>
      <pre className="raw-json-pre">{result.text}</pre>
    </div>
  );
};
