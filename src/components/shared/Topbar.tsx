import type { FC } from 'react';
import type { TopbarProps } from '../../lib/terminal-types';
import { StatusGlyph } from './StatusGlyph';

export const Topbar: FC<TopbarProps> = ({
  session,
  sessionAlert,
  leftOpen,
  rightOpen,
  agentView,
  onAgentViewChange,
  showChatToggle = true,
  onToggleLeft,
  onToggleRight,
  onChat,
  onKill,
  onClose,
}) => {
  const status = session?.status ?? 'connecting';
  const title = session?.title ?? 'Terminal';

  return (
    <div className="topbar">
      {/* Left drawer toggle */}
      <button
        className={`drawer-toggle${leftOpen ? ' on' : ''}`}
        onClick={onToggleLeft}
        title="Toggle session list"
      >
        ☰
      </button>

      {/* Breadcrumb */}
      <div className="breadcrumb">
        <span className="bc-repo mono">{session ? 'my-projects' : ''}</span>
        <span className="bc-slash">/</span>
        <span className="bc-session">{title}</span>
      </div>

      <span className="topbar-spacer" />

      {sessionAlert && (
        <span className={`session-alert ${sessionAlert.kind}`}>
          {sessionAlert.label}
        </span>
      )}

      {/* Status glyph */}
      <StatusGlyph status={status} />

      {agentView && onAgentViewChange && (
        <div className="view-switch" role="tablist" aria-label="Agent view">
          <button
            type="button"
            role="tab"
            aria-selected={agentView === 'terminal'}
            className={agentView === 'terminal' ? 'active' : ''}
            onClick={() => onAgentViewChange('terminal')}
            title="Terminal view"
          >
            Terminal
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={agentView === 'chat'}
            className={agentView === 'chat' ? 'active' : ''}
            onClick={() => onAgentViewChange('chat')}
            title="Chat view"
          >
            Chat
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={agentView === 'files'}
            className={agentView === 'files' ? 'active' : ''}
            onClick={() => onAgentViewChange('files')}
            title="Files navigator"
          >
            Files
          </button>
        </div>
      )}

      {showChatToggle && !agentView && onChat && (
        <button
          className="icon"
          onClick={onChat}
          title="Open chat"
        >
          💬
        </button>
      )}

      {/* Right drawer toggle */}
      <button
        className={`drawer-toggle${rightOpen ? ' on' : ''}`}
        onClick={onToggleRight}
        title="Toggle details"
      >
        ⚙
      </button>

      {/* Kill */}
      <button
        className="icon danger"
        onClick={onKill}
        title="Terminate session"
      >
        ⏻
      </button>

      {/* Close (detach) */}
      <button className="icon" onClick={onClose} title="Detach (close tab)">
        ✕
      </button>
    </div>
  );
};
