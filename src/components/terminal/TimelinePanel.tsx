import { useEffect, useState, type FC } from 'react';
import { fetchTimeline } from '../../lib/api';
import {
  TIMELINE_FILTERS,
  allUnsupported,
  durationDisplay,
  matchesFilter,
  noTimingNote,
  toolIcon,
  type FilterKey,
  type TimelineEntry,
} from '../../lib/timeline';

interface TimelinePanelProps {
  sessionId: string | null;
  visible: boolean;
}

type LoadState = 'idle' | 'loading' | 'ready' | 'error';

export const TimelinePanel: FC<TimelinePanelProps> = ({ sessionId, visible }) => {
  const [entries, setEntries] = useState<TimelineEntry[]>([]);
  const [harness, setHarness] = useState('');
  const [filter, setFilter] = useState<FilterKey>('all');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [state, setState] = useState<LoadState>('idle');

  // Fetch on session change only — the timeline is derived from a transcript
  // the page is already polling, so a timer here would re-read the same jsonl
  // twice for no new information (ADR-0017 § Rejected: poll-based timeline).
  useEffect(() => {
    if (!sessionId) {
      setEntries([]);
      setHarness('');
      setState('idle');
      return;
    }
    let active = true;
    setState('loading');
    setExpanded(new Set());
    fetchTimeline(sessionId)
      .then((data) => {
        if (!active) return;
        setEntries(data.entries ?? []);
        setHarness(data.harness ?? '');
        setState('ready');
      })
      .catch(() => {
        if (!active) return;
        setEntries([]);
        setState('error');
      });
    return () => {
      active = false;
    };
  }, [sessionId]);

  if (!visible) return null;

  // One honest sentence beats a column of identical dashes (ADR-0025 §SD3).
  const hideDuration = allUnsupported(entries);

  const toggle = (i: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  return (
    <div className="timeline-panel">
      <div className="timeline-filters">
        {TIMELINE_FILTERS.map((f) => (
          <button
            key={f.key}
            className={`chip filter${filter === f.key ? ' active' : ''}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {hideDuration && <div className="timeline-note">{noTimingNote(harness)}</div>}

      {state === 'loading' && <div className="timeline-empty">Loading…</div>}
      {state === 'error' && <div className="timeline-empty">Could not load the timeline</div>}
      {state === 'ready' && entries.length === 0 && (
        <div className="timeline-empty">No tool calls in this session</div>
      )}

      <div className="timeline-rows">
        {entries.map((e, i) => {
          const duration = durationDisplay(e);
          const isOpen = expanded.has(i);
          return (
            // Filtered rows are hidden, not unmounted, so expand state survives
            // a chip change (ADR-0017 §SD3).
            <div
              key={i}
              className={`timeline-row cat-${e.category}`}
              style={matchesFilter(e, filter) ? undefined : { display: 'none' }}
            >
              <button className="timeline-head" onClick={() => toggle(i)}>
                <span className="tl-icon">{toolIcon(e.category)}</span>
                <span className="tl-tool mono">{e.tool}</span>
                <span className="tl-args mono" title={e.args_summary}>
                  {e.args_summary}
                </span>
                {!hideDuration && (
                  <span
                    className={`tl-duration${
                      e.duration_state === 'measured' ? '' : ' tl-duration-absent'
                    }`}
                    title={duration.title}
                  >
                    {duration.text}
                  </span>
                )}
                <span className="tl-expand">{isOpen ? '▲' : '▼'}</span>
              </button>

              {isOpen && (
                <div className="timeline-detail">
                  <div className="tl-detail-label">Arguments</div>
                  <pre className="mono">{JSON.stringify(e.args, null, 2)}</pre>
                  <div className="tl-detail-label">Result</div>
                  <pre className="mono">{e.result_summary ?? '—'}</pre>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
