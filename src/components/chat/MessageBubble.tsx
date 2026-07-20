import { memo, useMemo, useState, type FC } from 'react';
import { renderMarkdown, formatTimestamp } from '../../lib/markdown';
import { messageToMarkdown, toolUseLabel, toolResultSummary } from '../../lib/export-markdown';
import type { RichMessage, RichContentBlock } from '../../lib/types';

interface MessageBubbleProps {
  message: RichMessage;
}

// Clipboard API needs a secure context; on plain-http (non-localhost) origins
// it is undefined — hide the copy affordance entirely (ADR-0007).
const canCopy = typeof navigator !== 'undefined' && Boolean(navigator.clipboard);

/** Hover-reveal copy button — copies the message as full markdown (ADR-0007). */
const CopyButton: FC<{ message: RichMessage }> = ({ message }) => {
  const [copied, setCopied] = useState(false);
  if (!canCopy) return null;
  return (
    <button
      className="bubble-copy-btn"
      title="Copy as markdown"
      aria-label="Copy message as markdown"
      onClick={() => {
        navigator.clipboard
          .writeText(messageToMarkdown(message))
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => {
            /* clipboard denied (focus loss / permissions) — no feedback, user retries */
          });
      }}
    >
      {copied ? '✓ Copied' : '⧉'}
    </button>
  );
};

function formatTokens(usage: { input_tokens?: number | null; output_tokens?: number | null } | undefined): string {
  if (!usage) return '';
  const parts: string[] = [];
  if (usage.input_tokens != null) parts.push(`↥${usage.input_tokens}`);
  if (usage.output_tokens != null) parts.push(`↧${usage.output_tokens}`);
  return parts.join(' ');
}

// Memoised: the transcript poll appends only new messages and preserves the
// object identity of existing ones (mergeTranscriptMessages), so shallow-comparing
// `message` skips re-rendering — and re-parsing markdown for — every unchanged
// bubble on each poll (ADR-0006 rich transcript; the dominant chat render cost).
const MessageBubbleImpl: FC<MessageBubbleProps> = ({ message }) => {
  const { role, ts, content, model, usage } = message;
  const isUser = role === 'user';

  if (isUser) {
    // User messages: simple text rendering (content is typically a single text block).
    const text = content?.find((b) => b.type === 'text')?.text || '';
    return (
      <div className="msg-row user">
        <div className="bubble">
          <CopyButton message={message} />
          <div className="bubble-text">{text}</div>
          {ts && <span className="bubble-ts">{formatTimestamp(ts)}</span>}
        </div>
      </div>
    );
  }

  // Assistant messages: rich block rendering + metadata.
  return (
    <div className="msg-row assistant">
      <div className="bubble">
        <CopyButton message={message} />
        {(content || []).map((block, i) => (
          <RichBlock key={i} block={block} />
        ))}
        <div className="bubble-meta">
          {ts && <span className="bubble-ts">{formatTimestamp(ts)}</span>}
          {model && <span className="bubble-model">{model}</span>}
          {usage && <span className="bubble-tokens">{formatTokens(usage)}</span>}
        </div>
      </div>
    </div>
  );
};

export const MessageBubble = memo(MessageBubbleImpl);

/** Render a single content block based on its type. */
const RichBlock: FC<{ block: RichContentBlock }> = ({ block }) => {
  const [expanded, setExpanded] = useState(false);
  // Parse markdown once per text block, not on every re-render (e.g. a sibling
  // block's expand toggle) — marked's lexer/parser is the hot path.
  const textHtml = useMemo(
    () => (block.type === 'text' ? renderMarkdown(block.text || '') : ''),
    [block]
  );

  switch (block.type) {
    case 'text':
      return (
        <div
          className="bubble-md"
          dangerouslySetInnerHTML={{ __html: textHtml }}
        />
      );

    case 'thinking':
      return (
        <div className="block-thinking">
          <button
            className="block-toggle"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            <span className="block-toggle-icon">{expanded ? '▾' : '▸'}</span>
            💭 Thinking{!expanded ? '…' : ''}
          </button>
          {expanded && (
            <div className="block-thinking-body">
              {renderMarkdown(block.thinking || '')}
            </div>
          )}
        </div>
      );

    case 'tool_use':
      return (
        <div className="block-tool-use">
          🔧 <code>{toolUseLabel(block)}</code>
        </div>
      );

    case 'tool_result':
      return (
        <div className="block-tool-result">
          <button
            className="block-toggle"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            <span className="block-toggle-icon">{expanded ? '▾' : '▸'}</span>
            📋 {toolResultSummary(block)}
          </button>
          {expanded && (
            <pre className="block-tool-result-body">
              {typeof block.content === 'string'
                ? block.content
                : JSON.stringify(block.content, null, 2)}
            </pre>
          )}
        </div>
      );

    default:
      // Defensive: unknown block types — render as JSON.
      return (
        <div className="block-unknown">
          <code>{JSON.stringify(block)}</code>
        </div>
      );
  }
};
