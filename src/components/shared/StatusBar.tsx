import type { FC } from 'react';
import type { StatusBarProps, SessionStatus } from '../../lib/terminal-types';

const STATUS_LABEL: Record<SessionStatus, string> = {
  connecting: 'connecting…',
  connected: 'connected',
  ended: 'session ended',
  waiting: 'waiting…',
  reconnecting: 'reconnecting… (input is not being sent)',
};

export const StatusBar: FC<StatusBarProps> = ({ status, sessionId }) => {
  const dotCls = status === 'connected' ? 'live'
    : status === 'ended' ? 'ended'
    : 'waiting';

  return (
    <div className="status-bar">
      <span className={`sb-dot ${dotCls}`} />
      <span className="sb-label">{STATUS_LABEL[status]}</span>
      {sessionId && (
        <>
          <span className="topbar-spacer" />
          <span className="mono" style={{ fontSize: 10, color: 'var(--faint)' }}>
            {sessionId.slice(0, 12)}
          </span>
        </>
      )}
    </div>
  );
};
