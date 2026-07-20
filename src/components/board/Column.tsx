import type { FC } from 'react';
import type { SessionCard } from '../../lib/types';
import { Card } from './Card';

interface ColumnProps {
  name: string;
  cards: SessionCard[];
  expandedId: string | null;
  onToggleTranscript: (id: string) => void;
  onOpenChat: (card: SessionCard) => void;
  onOpenTerminal: (card: SessionCard) => void;
  onKill: (sessionId: string) => void;
  onDismiss: (sessionId: string) => void;
  onCopyId: (sessionId: string) => void;
  transcripts: Record<string, { role: string; text: string; ts: string }[] | null>;
  loadingTranscripts: Record<string, boolean>;
}

export const Column: FC<ColumnProps> = ({
  name,
  cards,
  expandedId,
  onToggleTranscript,
  onOpenChat,
  onOpenTerminal,
  onKill,
  onDismiss,
  onCopyId,
  transcripts,
  loadingTranscripts,
}) => {
  return (
    <div className={`col c-${name}`}>
      <div className="col-head">
        <span className="swatch" />
        <h2>{name}</h2>
        <span className="count">{cards.length || ''}</span>
      </div>
      <div className="stack">
        {cards.length > 0 ? (
          cards.map((c) => (
            <Card
              key={c.session_id}
              card={c}
              expanded={expandedId === c.session_id}
              onToggleTranscript={onToggleTranscript}
              onOpenChat={onOpenChat}
              onOpenTerminal={onOpenTerminal}
              onKill={onKill}
              onDismiss={onDismiss}
              onCopyId={onCopyId}
              transcript={transcripts[c.session_id] ?? null}
              loadingTranscript={loadingTranscripts[c.session_id] ?? false}
            />
          ))
        ) : (
          <div className="empty">
            No sessions here.<br />
            New work lands as you start it.
          </div>
        )}
      </div>
    </div>
  );
};
