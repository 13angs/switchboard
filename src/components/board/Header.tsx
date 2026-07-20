import type { FC } from 'react';

interface HeaderProps {
  repo: string;
  view: 'active' | 'archive';
  onViewChange: (v: 'active' | 'archive') => void;
  onNewSession: () => void;
  unreadApprovals: number;
}

export const Header: FC<HeaderProps> = ({
  repo,
  view,
  onViewChange,
  onNewSession,
  unreadApprovals,
}) => {
  return (
    <header>
      <div className="brand">
        <span className="brand-mark">Agent View</span>
        <span className="brand-ver">v3</span>
      </div>
      <div className="repo">
        <span className="live-dot" />
        {repo || '—'}
      </div>
      <span className="spacer" />
      <div className="seg">
        <button
          className={view === 'active' ? 'active' : ''}
          onClick={() => onViewChange('active')}
        >
          Active
        </button>
        <button
          className={view === 'archive' ? 'active' : ''}
          onClick={() => onViewChange('archive')}
        >
          Archive
        </button>
      </div>
      <div className="notify-indicator" aria-live="polite" title="Browser tab alerts">
        Tab alerts
        {unreadApprovals > 0 && <span className="notify-badge">{unreadApprovals}</span>}
      </div>
      <button className="primary" onClick={onNewSession}>
        ＋ New session
      </button>
    </header>
  );
};
