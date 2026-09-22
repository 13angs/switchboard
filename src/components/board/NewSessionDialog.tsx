import { useState, type FC } from 'react';
import type { Launcher, SessionStartModelOption } from '../../lib/types';

interface NewSessionDialogProps {
  onClose: () => void;
  onStart: (
    harness: string,
    provider: string,
    label: string,
    model?: string,
    effort?: string
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

function defaultSelection(launcher: Launcher | undefined): { model: string; effort: string } {
  const caps = launcher?.session_start;
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
  const initial = defaultSelection(firstLauncher);
  const [harness, setHarness] = useState(firstLauncher?.harness ?? '');
  const currentLauncher =
    launchers.find((launcher) => launcher.harness === harness) ?? firstLauncher;
  const [provider, setProvider] = useState(firstLauncher?.providers[0] ?? '');
  const [model, setModel] = useState(initial.model);
  const [effort, setEffort] = useState(initial.effort);
  const [label, setLabel] = useState('');
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const caps = currentLauncher?.session_start;
  const selectedModel = caps?.models.find((candidate) => candidate.id === model) ?? null;
  const requiresEffort = Boolean(selectedModel?.supports_effort && caps?.efforts.length);
  const capabilityError = caps?.error ?? (!caps ? 'Launch capabilities are unavailable.' : null);
  const launchBlocked =
    !currentLauncher ||
    !caps ||
    !caps.available ||
    (caps.pinning && !selectedModel) ||
    (requiresEffort && !effort);

  const selectHarness = (next: Launcher) => {
    const nextSelection = defaultSelection(next);
    setHarness(next.harness);
    setProvider(next.providers[0] ?? '');
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
        caps?.pinning && selectedModel?.supports_effort ? effort || undefined : undefined
      );
    } catch (err) {
      setStartError(err instanceof Error ? err.message : 'Unable to start session');
      setStarting(false);
    }
  };

  return (
    <div className="overlay" onClick={starting ? undefined : onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h4>New session</h4>

        <div className="field">
          <label>Harness</label>
          <div className="opts">
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
            <div className="opts">
              {currentLauncher.providers.map((candidate) => (
                <div
                  key={candidate}
                  className={`opt${provider === candidate ? ' sel' : ''}`}
                  onClick={() => {
                    if (!starting) {
                      setProvider(candidate);
                      setStartError(null);
                    }
                  }}
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
            <div className="opts">
              {caps.models.map((option) => (
                <div
                  key={option.id}
                  className={`opt${model === option.id ? ' sel' : ''}`}
                  onClick={() => !starting && selectModel(option)}
                >
                  <span className="opt-radio" />
                  <b>{tierLabel(option)}</b>
                  <span className="sub">{option.id}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {selectedModel?.supports_effort && caps?.efforts.length ? (
          <div className="field">
            <label>Effort</label>
            <div className="opts">
              {caps.efforts.map((candidate) => (
                <div
                  key={candidate}
                  className={`opt${effort === candidate ? ' sel' : ''}`}
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

        <div className="field">
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

        {(capabilityError || startError) && (
          <div className="session-start-error" role="alert">
            {startError || capabilityError}
          </div>
        )}

        <div className="modal-actions">
          <button onClick={onClose} disabled={starting}>Cancel</button>
          <button
            className="primary"
            onClick={start}
            disabled={launchBlocked || starting}
          >
            {starting ? 'Starting…' : 'Start session'}
          </button>
        </div>
      </div>
    </div>
  );
};
