import type { FC } from 'react';
import type { SessionCard } from '../../lib/types';
import { Column } from './Column';

const ACTIVITY_COLS = ['Working', 'Awaiting', 'Idle', 'Blocked'] as const;

interface BoardGridProps {
  columns: Record<string, SessionCard[]>;
  archived: SessionCard[];
  view: 'active' | 'archive';
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

export const BoardGrid: FC<BoardGridProps> = ({
  columns,
  archived,
  view,
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
  if (view === 'archive') {
    return (
      <div className="board" style={{ gridTemplateColumns: 'minmax(260px,340px)' }}>
        <Column
          name="Archive"
          cards={archived}
          expandedId={expandedId}
          onToggleTranscript={onToggleTranscript}
          onOpenChat={onOpenChat}
          onOpenTerminal={onOpenTerminal}
          onKill={onKill}
          onDismiss={onDismiss}
          onCopyId={onCopyId}
          transcripts={transcripts}
          loadingTranscripts={loadingTranscripts}
        />
      </div>
    );
  }

  return (
    <div className="board">
      {ACTIVITY_COLS.map((name) => (
        <Column
          key={name}
          name={name}
          cards={columns[name] ?? []}
          expandedId={expandedId}
          onToggleTranscript={onToggleTranscript}
          onOpenChat={onOpenChat}
          onOpenTerminal={onOpenTerminal}
          onKill={onKill}
          onDismiss={onDismiss}
          onCopyId={onCopyId}
          transcripts={transcripts}
          loadingTranscripts={loadingTranscripts}
        />
      ))}
    </div>
  );
};
