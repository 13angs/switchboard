import type { FC } from 'react';
import type { RightDrawerProps } from '../../lib/terminal-types';
import { PRPill } from './PRPill';
import { costStr } from '../../lib/format';
import { mergePullRequests, sessionPullRequest } from '../../lib/prs';

function providerLabel(provider: string): string {
  if (provider === 'deepseek') return 'DeepSeek';
  if (provider === 'ollama') return 'Ollama';
  if (provider === 'openai') return 'OpenAI';
  if (provider === 'google') return 'Google';
  if (provider === 'claude') return 'Claude';
  return provider;
}

function providerClass(provider: string): string {
  if (provider === 'deepseek') return 'provider-deepseek';
  if (provider === 'ollama') return 'provider-ollama';
  if (provider === 'openai') return 'provider-codex';
  if (provider === 'google') return 'provider-google';
  return 'provider-claude';
}

export const RightDrawer: FC<RightDrawerProps> = ({
  open,
  session,
  pullRequests = [],
  view,
  onViewChange,
  hideViewToggle,
}) => {
  const prs = session
    ? mergePullRequests(
        sessionPullRequest(session.pr_number, session.pr_url, session.pr_state),
        mergePullRequests(session.pull_requests ?? [], pullRequests)
      )
    : [];

  return (
    <div className={`right-drawer${open ? ' open' : ''}`}>
      <div className="drawer-head">
        <span className="eyebrow">Session</span>
        {!hideViewToggle && onViewChange && (
          <div className="view-toggles">
            <button
              className={view === 'terminal' ? 'active' : ''}
              onClick={() => onViewChange('terminal')}
            >
              Terminal
            </button>
            <button
              className={view === 'files' ? 'active' : ''}
              onClick={() => onViewChange('files')}
            >
              Files
            </button>
          </div>
        )}
      </div>
      <div className="drawer-body">
        {!session && (
          <div style={{ color: 'var(--faint)', fontSize: 11 }}>
            No session selected
          </div>
        )}

        {session && (
          <>
            {/* Harness + Provider */}
            <div className="detail-group">
              <div className="dg-label">Harness / Provider</div>
              <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                {session.harness && <span className="chip harness">⎇ {session.harness}</span>}
                {session.provider && (
                  <span className={`chip ${providerClass(session.provider)}`}>
                    {providerLabel(session.provider)}
                  </span>
                )}
              </div>
            </div>

            {/* PR */}
            <div className="detail-group">
              <div className="dg-label">Pull Request</div>
              <div className="pr-list">
                {prs.length > 0 ? (
                  prs.map((pr) => (
                    <PRPill
                      key={pr.key}
                      number={pr.number}
                      url={pr.url}
                      state={pr.state ?? null}
                      label={pr.label}
                    />
                  ))
                ) : (
                  <PRPill number={null} url={null} state={null} />
                )}
              </div>
            </div>

            {/* Stats */}
            <div className="detail-group">
              <div className="dg-label">Stats</div>
              <div className="dg-row">
                <span className="k">Turns</span>
                <span className="v">{session.turn_count}</span>
              </div>
              <div className="dg-row">
                <span className="k">Cost</span>
                <span className="v">{costStr(session.total_cost_usd) || '—'}</span>
              </div>
              <div className="dg-row">
                <span className="k">Age</span>
                <span className="v">{session.age}</span>
              </div>
            </div>

            {/* Git */}
            <div className="detail-group">
              <div className="dg-label">Git</div>
              <div className="dg-row">
                <span className="k">Branch</span>
                <span className="v mono">{session.git_branch || '—'}</span>
              </div>
              <div className="dg-row">
                <span className="k">Worktree</span>
                <span className="v mono" style={{ fontSize: 11 }}>
                  {session.worktree_path || '—'}
                </span>
              </div>
            </div>

            {/* Session ID */}
            <div className="detail-group">
              <div className="dg-label">Session ID</div>
              <div className="v mono" style={{ fontSize: 11, userSelect: 'all' }}>
                {session.session_id}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
