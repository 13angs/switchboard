import { useState, type FC } from 'react';
import type { Launcher } from '../../lib/types';

interface NewSessionDialogProps {
  onClose: () => void;
  onStart: (harness: string, provider: string, label: string) => void;
  launchers: Launcher[];
}

const PROVIDER_LABELS: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
  ollama: 'Ollama',
  openai: 'OpenAI',
  google: 'Google',
};

const HARNESS_LABELS: Record<string, { label: string; sub: string }> = {
  claude: { label: 'Claude', sub: 'Claude Code CLI' },
  codex: { label: 'Codex', sub: 'Codex CLI' },
  agy: { label: 'Antigravity', sub: 'Antigravity CLI (agy)' },
};

export const NewSessionDialog: FC<NewSessionDialogProps> = ({
  onClose,
  onStart,
  launchers,
}) => {
  const safeLaunchers = launchers.length
    ? launchers
    : [{ harness: 'claude', providers: ['claude'] }];
  const [harness, setHarness] = useState(safeLaunchers[0].harness);
  const currentLauncher =
    safeLaunchers.find((l) => l.harness === harness) ?? safeLaunchers[0];
  const [provider, setProvider] = useState(currentLauncher.providers[0] ?? 'claude');
  const [label, setLabel] = useState('');

  const selectHarness = (next: Launcher) => {
    setHarness(next.harness);
    setProvider(next.providers[0] ?? '');
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h4>New session</h4>

        <div className="field">
          <label>Harness</label>
          <div className="opts">
            {safeLaunchers.map((l) => {
              const meta = HARNESS_LABELS[l.harness] ?? {
                label: l.harness,
                sub: `${l.harness} CLI`,
              };
              return (
                <div
                  key={l.harness}
                  className={`opt harness${harness === l.harness ? ' sel' : ''}`}
                  onClick={() => selectHarness(l)}
                >
                  <span className="opt-radio" />
                  <span className="opt-copy">
                    <b>{meta.label}</b>
                    <span>{meta.sub}</span>
                  </span>
                  <span className="sub">
                    {l.providers.length} provider{l.providers.length > 1 ? 's' : ''}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="field">
          <label>Provider</label>
          <div className="opts">
            {currentLauncher.providers.map((p) => (
              <div
                key={p}
                className={`opt${provider === p ? ' sel' : ''}`}
                onClick={() => setProvider(p)}
              >
                <span className="opt-radio" />
                {PROVIDER_LABELS[p] || p}
                <span className="sub">
                  {p === 'deepseek' || p === 'ollama' ? 'configured' : 'default'}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="field">
          <label>
            Label{' '}
            <span className="label-optional">(optional)</span>
          </label>
          <input
            className="tf"
            type="text"
            placeholder="short name for the card"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            autoComplete="off"
          />
        </div>

        <div className="modal-actions">
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            onClick={() => onStart(harness, provider, label.trim())}
          >
            Start session
          </button>
        </div>
      </div>
    </div>
  );
};
