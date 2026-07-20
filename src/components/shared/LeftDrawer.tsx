import type { FC } from 'react';
import type { LeftDrawerProps, SessionInfo } from '../../lib/terminal-types';

const SessionRow: FC<{
  s: SessionInfo;
  active: boolean;
  onClick: () => void;
}> = ({ s, active, onClick }) => {
  const providerLabel = s.provider === 'deepseek' ? 'DeepSeek'
    : s.provider === 'ollama' ? 'Ollama'
    : s.provider === 'claude' ? 'Claude'
    : s.provider === 'openai' ? 'OpenAI'
    : s.provider === 'google' ? 'Google'
    : s.provider ?? null;
  const providerClass = s.provider === 'deepseek' ? 'provider-deepseek'
    : s.provider === 'ollama' ? 'provider-ollama'
    : s.provider === 'openai' ? 'provider-codex'
    : s.provider === 'google' ? 'provider-google'
    : 'provider-claude';

  return (
    <div
      className={`sess-row s-${s.activity}${active ? ' active' : ''}`}
      onClick={onClick}
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onClick(); }}
    >
      <div className="sess-title">{s.title || s.session_id}</div>
      <div className="sess-meta">
        <span className="sess-age tnum">{s.age}</span>
        {providerLabel && (
          <span className={`chip ${providerClass}`}>{providerLabel}</span>
        )}
      </div>
    </div>
  );
};

export const LeftDrawer: FC<LeftDrawerProps> = ({
  open,
  sessions,
  activeSessionId,
  view,
  onViewChange,
  onSelectSession,
}) => {
  const activeSessions = sessions.filter((s) => !s.dismissed && !s.auto_archived);
  const archiveSessions = sessions.filter((s) => s.dismissed || s.auto_archived);
  const visibleSessions = view === 'archive' ? archiveSessions : activeSessions;

  return (
    <div className={`left-drawer${open ? ' open' : ''}`}>
      <div className="drawer-head">
        <span className="eyebrow">Sessions</span>
        <div className="drawer-segment" role="tablist" aria-label="Session scope">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'active'}
            className={view === 'active' ? 'active' : ''}
            onClick={() => onViewChange('active')}
          >
            Active
            <span className="tnum">{activeSessions.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'archive'}
            className={view === 'archive' ? 'active' : ''}
            onClick={() => onViewChange('archive')}
          >
            Archive
            <span className="tnum">{archiveSessions.length}</span>
          </button>
        </div>
      </div>
      <div className="drawer-body">
        {visibleSessions.map((s) => (
          <SessionRow
            key={s.session_id}
            s={s}
            active={s.session_id === activeSessionId}
            onClick={() => {
              if (s.session_id === activeSessionId) return;
              onSelectSession(s.session_id);
            }}
          />
        ))}
        {visibleSessions.length === 0 && (
          <div className="drawer-empty">
            {view === 'archive' ? 'No archived sessions' : 'No active sessions'}
          </div>
        )}
      </div>
    </div>
  );
};
