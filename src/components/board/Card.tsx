import { memo, type FC } from 'react';
import type { SessionCard } from '../../lib/types';
import { ago, costStr, sidShort } from '../../lib/format';

interface CardProps {
  card: SessionCard;
  expanded: boolean;
  onToggleTranscript: (sessionId: string) => void;
  onOpenChat: (card: SessionCard) => void;
  onOpenTerminal: (card: SessionCard) => void;
  onKill: (sessionId: string) => void;
  onDismiss: (sessionId: string) => void;
  onCopyId: (sessionId: string) => void;
  selected: boolean;
  onToggleSelected: (sessionId: string) => void;
  transcript: { role: string; text: string; ts: string }[] | null;
  loadingTranscript: boolean;
}

const PROVIDER_LABELS: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
  ollama: 'Ollama',
  openai: 'OpenAI',
  google: 'Google',
};

function providerClass(provider: string): string {
  if (provider === 'deepseek') return 'provider-deepseek';
  if (provider === 'ollama') return 'provider-ollama';
  if (provider === 'openai') return 'provider-codex';
  if (provider === 'google') return 'provider-google';
  return 'provider-claude';
}

const CardImpl: FC<CardProps> = ({
  card,
  expanded,
  onToggleTranscript,
  onOpenChat,
  onOpenTerminal,
  onKill,
  onDismiss,
  onCopyId,
  selected,
  onToggleSelected,
  transcript,
  loadingTranscript,
}) => {
  const sid = sidShort(card.session_id);
  const age = ago(card.last_ts);
  const cost = costStr(card.total_cost_usd);
  const isMerged = !!card.merged_pr;

  return (
    <div
      className={`card s-${card.activity}${expanded ? ' expanded' : ''}${selected ? ' selected' : ''}`}
      tabIndex={0}
      onClick={() => onOpenTerminal(card)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onOpenTerminal(card);
      }}
    >
      <label
        className="card-select"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
        title="Select session"
      >
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelected(card.session_id)}
          aria-label={`Select ${card.title || card.session_id}`}
        />
      </label>
      <div className="card-top">
        <div className="card-title">{card.title || card.session_id || 'Untitled'}</div>
        {/* Health dot — Branch C, ADR-0016: 🟢 hidden, 🟡🟡🔴 shown */}
        {card.health && card.health.status !== 'healthy' && (
          <span
            className={`health-dot h-${card.health.status}`}
            title={`Health: ${card.health.status}\nStale: ${card.health.stale} (${card.health.stale_hrs.toFixed(1)}h)\nLoop: ${card.health.loop} (max ${card.health.loop_count})\nError: ${card.health.error} (${card.health.error_count}/${card.health.error_total})`}
          />
        )}
        <span className={`state-badge st-${card.activity}`}>
          <span className="state-dot" />
          {card.activity}
        </span>
      </div>
      <div className="card-sid mono">{sid}</div>
      <div className="card-meta">
        <span className="meta-field">
          <span className="meta-key">age</span>
          <span className="meta-val tnum">{age}</span>
        </span>
        <span className="meta-field">
          <span className="meta-key">turns</span>
          <span className="meta-val tnum">{card.turn_count}</span>
        </span>
        {cost && (
          <span className="meta-field">
            <span className="meta-key">cost</span>
            <span className="meta-val tnum">{cost}</span>
          </span>
        )}
      </div>

      {/* Chips row */}
      {(card.harness || card.provider || card.pr_number || card.git_branch || card.merged_pr) && (
        <div className="card-chips">
          {card.harness && <span className="chip harness">⎇ {card.harness}</span>}
          {card.provider && (
            <span className={`chip ${providerClass(card.provider)}`}>
              {PROVIDER_LABELS[card.provider] || card.provider}
            </span>
          )}
          {card.pr_number && (
            <a
              className="chip pr"
              href={card.pr_url || '#'}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              PR&nbsp;#{card.pr_number}
            </a>
          )}
          {card.git_branch && <span className="chip">{card.git_branch}</span>}
          {card.merged_pr && <span className="chip merged">✓ merged · {card.merged_pr}</span>}
        </div>
      )}

      {/* Note (blocked reason / read-only) */}
      {card.note_kind === 'blocked' && card.note_text && (
        <div className="card-note blocked-note">
          <b>{card.note_text}</b>
          {card.note_code && <> — <span className="mono">{card.note_code}</span></>}
          {card.note_tail}
        </div>
      )}
      {card.note_kind === 'metadata' && card.note_text && (
        <div className="card-note">
          <b>{card.note_text}</b>
          {card.note_tail}
        </div>
      )}
      {card.note_kind && !['blocked', 'metadata'].includes(card.note_kind) && (
        <div className="card-note"><b>Read-only</b> — {card.note_kind}</div>
      )}

      {/* Transcript drawer */}
      {expanded && (
        <div className="card-drawer">
          {loadingTranscript ? (
            <div className="msg" style={{ color: 'var(--muted)' }}>loading…</div>
          ) : transcript && transcript.length > 0 ? (
            transcript.slice(-10).map((m, i) => (
              <div key={i} className="msg">
                <span className={`role ${m.role === 'user' ? 'user' : ''}`}>{m.role}</span>{' '}
                <span className="body">{m.text.slice(0, 200)}</span>
              </div>
            ))
          ) : transcript && transcript.length === 0 ? (
            <div className="msg">empty</div>
          ) : null}
        </div>
      )}

      {/* Actions */}
      {!isMerged && (
        <div className="card-actions" onClick={(e) => e.stopPropagation()}>
          <button className="icon" onClick={() => onOpenChat(card)} title="Chat">
            Chat
          </button>
          <button className="icon" onClick={() => onOpenTerminal(card)} title="Terminal">
            Terminal
          </button>
          <button
            className={`icon${expanded ? ' on' : ''}`}
            onClick={() => onToggleTranscript(card.session_id)}
          >
            Transcript{expanded ? ' ▾' : ''}
          </button>
          <span className="actions-spacer" />
          <button
            className="icon"
            title="Copy session ID"
            onClick={() => onCopyId(card.session_id)}
          >
            ⧉
          </button>
          <button
            className="icon danger"
            onClick={() => onKill(card.session_id)}
            title="Terminate this session"
          >
            Kill
          </button>
          <button
            className="icon danger"
            onClick={() => onDismiss(card.session_id)}
            title="Dismiss (hide from board)"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
};

// Memoised: useBoardState keeps a card's object identity stable across polls
// when its fields are unchanged, and Board passes stable useCallback handlers,
// so unchanged cards skip re-rendering on every 5s poll.
export const Card = memo(CardImpl);
