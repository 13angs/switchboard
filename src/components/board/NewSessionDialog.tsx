import { useState, type FC } from 'react';
import type { Launcher, SessionStartModelOption } from '../../lib/types';

interface NewSessionDialogProps {
  onClose: () => void;
  onStart: (
    harness: string,
    provider: string,
    label: string,
    model?: string,
    effort?: string,
    prompt?: string
  ) => Promise<void>;
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

function capabilities(
  launcher: Launcher | undefined,
  provider: string
) {
  return launcher?.session_start?.[provider];
}

function defaultSelection(
  launcher: Launcher | undefined,
  provider: string
): { model: string; effort: string } {
  const caps = capabilities(launcher, provider);
  if (!caps) return { model: '', effort: '' };
  const model = caps.defaults.model ?? '';
  const selected = caps.models.find((candidate) => candidate.id === model);
  return {
    model,
    effort: selected?.supports_effort ? caps.defaults.effort ?? '' : '',
  };
}

function tierLabel(option: SessionStartModelOption): string {
  return option.tier.charAt(0).toUpperCase() + option.tier.slice(1);
}

export const NewSessionDialog: FC<NewSessionDialogProps> = ({
  onClose,
  onStart,
  launchers,
}) => {
  const firstLauncher = launchers[0];
  const firstProvider = firstLauncher?.providers[0] ?? '';
  const initial = defaultSelection(firstLauncher, firstProvider);
  const [harness, setHarness] = useState(firstLauncher?.harness ?? '');
  const currentLauncher =
    launchers.find((launcher) => launcher.harness === harness) ?? firstLauncher;
  const [provider, setProvider] = useState(firstProvider);
  const [model, setModel] = useState(initial.model);
  const [effort, setEffort] = useState(initial.effort);
  const [label, setLabel] = useState('');
  const [startMode, setStartMode] = useState<'empty' | 'message'>('empty');
  const [message, setMessage] = useState('');
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const caps = capabilities(currentLauncher, provider);
  const selectedModel = caps?.models.find((candidate) => candidate.id === model) ?? null;
  const requiresEffort = Boolean(selectedModel?.supports_effort && caps?.efforts.length);
  const capabilityError = caps?.error ?? (!caps ? 'Launch capabilities are unavailable.' : null);
  const launchBlocked =
    !currentLauncher ||
    !caps ||
    !caps.available ||
    (caps.pinning && !selectedModel) ||
    (requiresEffort && !effort) ||
    (startMode === 'message' && !message.trim());

  const selectHarness = (next: Launcher) => {
    const nextProvider = next.providers[0] ?? '';
    const nextSelection = defaultSelection(next, nextProvider);
    setHarness(next.harness);
    setProvider(nextProvider);
    setModel(nextSelection.model);
    setEffort(nextSelection.effort);
    setStartError(null);
  };

  const selectProvider = (nextProvider: string) => {
    const nextSelection = defaultSelection(currentLauncher, nextProvider);
    setProvider(nextProvider);
    setModel(nextSelection.model);
    setEffort(nextSelection.effort);
    setStartError(null);
  };

  const selectModel = (next: SessionStartModelOption) => {
    setModel(next.id);
    if (!next.supports_effort) {
      setEffort('');
    } else {
      const nextEffort =
        caps?.defaults.effort && caps.efforts.includes(caps.defaults.effort)
          ? caps.defaults.effort
          : caps?.efforts[0] ?? '';
      setEffort(nextEffort);
    }
    setStartError(null);
  };

  const start = async () => {
    if (launchBlocked || starting || !currentLauncher) return;
    setStarting(true);
    setStartError(null);
    try {
      await onStart(
        currentLauncher.harness,
        provider,
        label.trim(),
        caps?.pinning ? model || undefined : undefined,
        caps?.pinning && selectedModel?.supports_effort ? effort || undefined : undefined,
        startMode === 'message' ? message.trim() : undefined
      );
    } catch (err) {
      setStartError(err instanceof Error ? err.message : 'Unable to start session');
      setStarting(false);
    }
  };

  return (
    <div className="overlay new-session-overlay" onClick={starting ? undefined : onClose}>
      <div
        className="modal new-session-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-session-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="new-session-head">
          <div>
            <h4 id="new-session-title">New session</h4>
            <p>Choose whether to open an empty room or start with a message.</p>
          </div>
          <button
            className="icon new-session-close"
            type="button"
            aria-label="Close new session dialog"
            onClick={onClose}
            disabled={starting}
          >
            ×
          </button>
        </div>

        <div className="new-session-body">
          <div className="field new-session-mode-field">
            <label>Start with</label>
            <div className="new-session-mode-grid" role="group" aria-label="Session start mode">
              <button
                type="button"
                className={`new-session-mode${startMode === 'empty' ? ' sel' : ''}`}
                aria-pressed={startMode === 'empty'}
                onClick={() => {
                  if (!starting) {
                    setStartMode('empty');
                    setStartError(null);
                  }
                }}
                disabled={starting}
              >
                <span className="new-session-mode-title">Empty session</span>
                <span className="new-session-mode-copy">Open the room without sending anything.</span>
              </button>
              <button
                type="button"
                className={`new-session-mode${startMode === 'message' ? ' sel' : ''}`}
                aria-pressed={startMode === 'message'}
                onClick={() => {
                  if (!starting) {
                    setStartMode('message');
                    setStartError(null);
                  }
                }}
                disabled={starting}
              >
                <span className="new-session-mode-title">Send a message</span>
                <span className="new-session-mode-copy">Start the room and run the first message now.</span>
              </button>
            </div>
          </div>

          <div className="field">
            <label>Harness</label>
            <div className="opts new-session-option-grid harness-options">
              {launchers.map((launcher) => {
                const meta = HARNESS_LABELS[launcher.harness] ?? {
                  label: launcher.harness,
                  sub: `${launcher.harness} CLI`,
                };
                return (
                  <div
                    key={launcher.harness}
                    className={`opt harness${harness === launcher.harness ? ' sel' : ''}`}
                    onClick={() => !starting && selectHarness(launcher)}
                  >
                    <span className="opt-radio" />
                    <span className="opt-copy">
                      <b>{meta.label}</b>
                      <span>{meta.sub}</span>
                    </span>
                    <span className="sub">
                      {launcher.providers.length} provider{launcher.providers.length > 1 ? 's' : ''}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {currentLauncher && (
            <div className="field">
              <label>Provider</label>
              <div className="opts new-session-option-grid provider-options">
                {currentLauncher.providers.map((candidate) => (
                  <div
                    key={candidate}
                    className={`opt${provider === candidate ? ' sel' : ''}`}
                    onClick={() => !starting && selectProvider(candidate)}
                  >
                    <span className="opt-radio" />
                    {PROVIDER_LABELS[candidate] || candidate}
                    <span className="sub">
                      {candidate === 'deepseek' || candidate === 'ollama' ? 'configured' : 'default'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {caps?.models.length ? (
            <div className="field">
              <label>Model</label>
              <div className="opts new-session-option-grid model-options">
                {caps.models.map((option) => (
                  <div
                    key={option.id}
                    className={`opt model-option${model === option.id ? ' sel' : ''}`}
                    onClick={() => !starting && selectModel(option)}
                  >
                    <span className="opt-radio" />
                    <span className="opt-copy">
                      <b>{tierLabel(option)}</b>
                      <span>{option.id}</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {selectedModel?.supports_effort && caps?.efforts.length ? (
            <div className="field">
              <label>Effort</label>
              <div className="opts effort-options">
                {caps.efforts.map((candidate) => (
                  <div
                    key={candidate}
                    className={`opt effort-option${effort === candidate ? ' sel' : ''}`}
                    onClick={() => {
                      if (!starting) {
                        setEffort(candidate);
                        setStartError(null);
                      }
                    }}
                  >
                    <span className="opt-radio" />
                    {candidate}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="field new-session-label-field">
            <label>
              Label <span className="label-optional">(optional)</span>
            </label>
            <input
              className="tf"
              type="text"
              placeholder="short name for the card"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              autoComplete="off"
              disabled={starting}
            />
          </div>

          {startMode === 'message' && (
            <div className="field new-session-message-field">
              <label htmlFor="new-session-message">First message</label>
              <textarea
                id="new-session-message"
                className="new-session-message"
                placeholder="Tell the agent what to do…"
                value={message}
                onChange={(e) => {
                  setMessage(e.target.value);
                  setStartError(null);
                }}
                rows={4}
                disabled={starting}
              />
              <span className="new-session-message-hint">
                This message is submitted as the agent&apos;s first turn.
              </span>
            </div>
          )}

          {(capabilityError || startError) && (
            <div className="session-start-error" role="alert">
              {startError || capabilityError}
            </div>
          )}
        </div>

        <div className="modal-actions new-session-actions">
          <button onClick={onClose} disabled={starting}>Cancel</button>
          <button
            className="primary"
            onClick={start}
            disabled={launchBlocked || starting}
          >
            {starting
              ? 'Starting…'
              : startMode === 'message'
                ? 'Start & send'
                : 'Start session'}
          </button>
        </div>
      </div>
    </div>
  );
};
