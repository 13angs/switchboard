import type { FC } from 'react';
import type { SessionStatus } from '../../lib/terminal-types';

interface StatusGlyphProps {
  status: SessionStatus;
}

export const StatusGlyph: FC<StatusGlyphProps> = ({ status }) => {
  if (status === 'connecting') {
    return (
      <span className="status-glyph">
        <span className="sg-spinner" />
      </span>
    );
  }
  if (status === 'ended') {
    return (
      <span className="status-glyph">
        <span className="sg-dot" style={{ background: 'var(--blocked)' }} />
      </span>
    );
  }
  if (status === 'waiting') {
    return (
      <span className="status-glyph">
        <span className="sg-dot" style={{ background: 'var(--working)', animation: 'pulse 1.8s ease-in-out infinite' }} />
      </span>
    );
  }
  // connected
  return (
    <span className="status-glyph">
      <span className="sg-dot" style={{ background: 'var(--ok)' }} />
    </span>
  );
};
