import type { FC } from 'react';
import { costDisplay } from '../../lib/format';

interface ReadOnlyBarProps {
  state: 'connecting' | 'loading' | 'ended' | 'error' | 'ready' | 'working' | null;
  sessionId: string | null;
  /** Session running cost — hidden when there is no usage data (ADR-0007).
   *  Renders `unpriced` when usage exists but no model had a rate (ADR-0022). */
  costUsd?: number | null;
  costPartial?: boolean;
  unpricedModels?: string[] | null;
  /** Download the transcript as markdown; button hidden when absent (ADR-0007). */
  onExport?: () => void;
  /** Toggle rendered bubbles ↔ raw JSON; button hidden when absent (ADR-0015 §SD1). */
  onToggleJson?: () => void;
  showJson?: boolean;
  onSwitchToTerminal: () => void;
}

export const ReadOnlyBar: FC<ReadOnlyBarProps> = ({
  state,
  sessionId,
  costUsd,
  costPartial,
  unpricedModels,
  onExport,
  onToggleJson,
  showJson,
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

  const cost = costDisplay(costUsd, costPartial, unpricedModels); // null → hidden

  return (
    <div className="readonly-bar">
      <span className="ro-main">{getContent()}</span>
      {(cost || onExport || onToggleJson) && (
        <span className="ro-right">
          {cost && (
            <span
              className={`ro-cost${cost.unpriced ? ' cost-unpriced' : ''}`}
              title={cost.title}
            >
              {cost.text}
            </span>
          )}
          {onToggleJson && (
            <button
              className={`ro-json-btn${showJson ? ' active' : ''}`}
              onClick={onToggleJson}
              aria-pressed={Boolean(showJson)}
              title={showJson ? 'Back to rendered transcript' : 'Show the raw transcript JSON'}
            >
              {'{} JSON'}
            </button>
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
