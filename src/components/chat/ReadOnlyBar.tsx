import type { FC } from 'react';
import { costStr } from '../../lib/format';

interface ReadOnlyBarProps {
  state: 'connecting' | 'loading' | 'ended' | 'error' | 'ready' | 'working' | null;
  sessionId: string | null;
  /** Session running cost — hidden when null (ADR-0007). */
  costUsd?: number | null;
  /** Download the transcript as markdown; button hidden when absent (ADR-0007). */
  onExport?: () => void;
  onSwitchToTerminal: () => void;
}

export const ReadOnlyBar: FC<ReadOnlyBarProps> = ({
  state,
  sessionId,
  costUsd,
  onExport,
  onSwitchToTerminal,
}) => {
  const getContent = () => {
    if (!sessionId) {
      return (
        <span className="ro-status-text">
          <span className="banner-spinner" />
          Connecting to session…
        </span>
      );
    }

    switch (state) {
      case 'connecting':
      case 'loading':
        return (
          <span className="ro-status-text">
            <span className="banner-spinner" />
            Loading transcript…
          </span>
        );
      case 'working':
        return (
          <span className="ro-status-text">
            <span className="banner-spinner" />
            Assistant is working…
          </span>
        );
      case 'ended':
        return (
          <span className="ro-status-text ro-ended">
            Session ended
          </span>
        );
      case 'error':
        return (
          <span className="ro-status-text ro-error">
            Connection lost — retrying…
          </span>
        );
      case 'ready':
      default:
        return (
          <>
            <span className="ro-status-text">📖 Read-only transcript</span>
            <span className="ro-sep">·</span>
            <button className="ro-switch-link" onClick={onSwitchToTerminal}>
              Switch to Terminal to interact
            </button>
          </>
        );
    }
  };

  const cost = costStr(costUsd); // '' when null/undefined → hidden

  return (
    <div className="readonly-bar">
      <span className="ro-main">{getContent()}</span>
      {(cost || onExport) && (
        <span className="ro-right">
          {cost && (
            <span className="ro-cost" title="Session cost (USD)">
              {cost}
            </span>
          )}
          {onExport && (
            <button
              className="ro-export-btn"
              onClick={onExport}
              title="Download transcript as markdown"
            >
              ⬇ Export .md
            </button>
          )}
        </span>
      )}
    </div>
  );
};
