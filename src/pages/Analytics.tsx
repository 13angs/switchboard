import { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchAnalytics, type AnalyticsResponse } from '../lib/api';
import './Analytics.css';

const DAYS_OPTIONS = [1, 7, 30] as const;
const HARNESS_OPTIONS = ['claude', 'codex', 'agy'] as const;
const HARNESS_LABELS: Record<string, string> = {
  claude: 'Claude',
  codex: 'Codex',
  agy: 'Agy',
};

function readParams(): { days: number; harness: string } {
  const qs = new URLSearchParams(location.search);
  const rawDays = parseInt(qs.get('days') || '', 10);
  const days = DAYS_OPTIONS.includes(rawDays as typeof DAYS_OPTIONS[number]) ? rawDays : 7;
  const harness = qs.get('harness')?.toLowerCase() || 'claude';
  return {
    days,
    harness: HARNESS_OPTIONS.includes(harness as typeof HARNESS_OPTIONS[number])
      ? harness
      : 'claude',
  };
}

function writeParams(days: number, harness: string) {
  const url = new URL(location.href);
  url.searchParams.set('days', String(days));
  url.searchParams.set('harness', harness);
  history.replaceState(null, '', url.toString());
}

type SortField = 'path' | 'reads' | 'edits' | 'writes' | 'total_ops' | 'sessions';
type SortDir = 'asc' | 'desc';

function dirname(p: string): string {
  const parts = p.split('/');
  parts.pop();
  return parts.join('/') || '';
}
function filename(p: string): string {
  const parts = p.split('/');
  return parts[parts.length - 1] || p;
}

interface HarnessStats {
  sessions: number;
  operations: number;
  unique_files: number;
}

export function AnalyticsPage() {
  const { days: initDays, harness: initHarness } = readParams();
  const [days, setDays] = useState<number>(initDays);
  const [harness, setHarness] = useState<string>(initHarness);
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sortField, setSortField] = useState<SortField>('total_ops');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const fetchData = useCallback(async (d: number, h: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAnalytics(d, h);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchData(days, harness);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Poll every 30s
  useEffect(() => {
    const timer = setInterval(() => {
      fetchData(days, harness);
    }, 30_000);
    return () => clearInterval(timer);
  }, [days, harness, fetchData]);

  const handleDaysChange = useCallback(
    (d: number) => {
      setDays(d);
      writeParams(d, harness);
      fetchData(d, harness);
    },
    [harness, fetchData]
  );

  const handleHarnessChange = useCallback(
    (h: string) => {
      setHarness(h);
      writeParams(days, h);
      fetchData(days, h);
    },
    [days, fetchData]
  );

  const handleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortField(field);
        setSortDir(field === 'path' ? 'asc' : 'desc');
      }
    },
    [sortField]
  );

  const sortedFiles = useMemo(() => {
    if (!data) return [];
    const files = [...data.top_files];
    files.sort((a, b) => {
      let cmp = 0;
      if (sortField === 'path') {
        cmp = a.path.localeCompare(b.path);
      } else {
        cmp = (a[sortField] || 0) - (b[sortField] || 0);
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return files;
  }, [data, sortField, sortDir]);

  const sortIndicator = (field: SortField) => {
    if (sortField !== field) return '';
    return sortDir === 'asc' ? ' ▲' : ' ▼';
  };

  const harnessOrder = ['claude', 'codex', 'agy'];
  const perHarnessList: Array<{ name: string; label: string; stats: HarnessStats }> = useMemo(() => {
    if (!data) return [];
    return harnessOrder
      .filter((h) => data.per_harness[h])
      .map((h) => ({ name: h, label: HARNESS_LABELS[h] || h, stats: data.per_harness[h] }));
  }, [data]);

  const isEmpty = data && data.summary.total_sessions === 0;

  return (
    <div id="page-analytics">
      {/* header */}
      <header className="analytics-header">
        <div className="brand">
          <span className="mark">Agent View</span>
          <span className="section">· Analytics</span>
        </div>
        <div className="repo mono">
          <span className="live" />
          <span>{data?.repo ?? '—'}</span>
        </div>
        <div className="spacer" />
        <div className="filter-bar">
          <div className="segmented">
            {DAYS_OPTIONS.map((d) => (
              <button
                key={d}
                className={d === days ? 'active' : ''}
                onClick={() => handleDaysChange(d)}
              >
                {d}d
              </button>
            ))}
          </div>
          <select
            value={harness}
            onChange={(e) => handleHarnessChange(e.target.value)}
          >
            {HARNESS_OPTIONS.map((h) => (
              <option key={h} value={h}>
                {HARNESS_LABELS[h]}
              </option>
            ))}
          </select>
        </div>
      </header>

      <main>
        {error && (
          <div className="card">
            <div className="card-body">
              <div className="empty">
                <div className="icon">⚠️</div>
                <p>Failed to load: {error}</p>
              </div>
            </div>
          </div>
        )}

        {loading && !data && !error && (
          <div className="card">
            <div className="card-body">
              <div className="empty">
                <p>Loading…</p>
              </div>
            </div>
          </div>
        )}

        {isEmpty && (
          <div className="card">
            <div className="card-body">
              <div className="empty">
                <div className="icon">📂</div>
                <p>No sessions in this time range.</p>
                <p className="hint">Try a wider time range or a different harness.</p>
              </div>
            </div>
          </div>
        )}

        {data && !isEmpty && (
          <>
            {/* summary tiles */}
            <div className="tiles">
              <div className="tile">
                <span className="label">Sessions</span>
                <span className="value tnum">{data.summary.total_sessions}</span>
              </div>
              <div className="tile">
                <span className="label">Operations</span>
                <span className="value tnum">{data.summary.total_operations}</span>
              </div>
              <div className="tile">
                <span className="label">Unique files</span>
                <span className="value tnum">{data.summary.unique_files}</span>
              </div>
            </div>

            {/* per-harness breakdown */}
            <div className="card">
              <div className="card-header">
                <span className="title">Per-Harness Breakdown</span>
                <span className="subtitle">— selected harness highlighted</span>
              </div>
              <div className="card-body" style={{ overflowX: 'auto' }}>
                <table className="harness-table">
                  <thead>
                    <tr>
                      <th>Harness</th>
                      <th>Sessions</th>
                      <th>Operations</th>
                      <th>Unique files</th>
                    </tr>
                  </thead>
                  <tbody>
                    {perHarnessList.map((h) => (
                      <tr
                        key={h.name}
                        className={h.name === harness ? 'harness-active' : ''}
                      >
                        <td>{h.name === harness ? `▶ ${h.label}` : h.label}</td>
                        <td className="tnum">{h.stats.sessions}</td>
                        <td className="tnum">{h.stats.operations}</td>
                        <td className="tnum">{h.stats.unique_files}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* top files table */}
            <div className="card">
              <div className="card-header">
                <span className="title">Top Files</span>
                <span className="subtitle">
                  — {sortedFiles.length} of {data.summary.unique_files} files · sorted by{' '}
                  {sortField.replace('_', ' ')}
                </span>
              </div>
              <div className="card-body" style={{ overflowX: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th
                        className={`sortable${sortField === 'path' ? ` sort-${sortDir}` : ''}`}
                        onClick={() => handleSort('path')}
                        style={{ minWidth: 200 }}
                      >
                        File{sortIndicator('path')}
                      </th>
                      <th
                        className={`sortable${sortField === 'reads' ? ` sort-${sortDir}` : ''}`}
                        onClick={() => handleSort('reads')}
                        style={{ width: 55, textAlign: 'center' }}
                      >
                        Rd{sortIndicator('reads')}
                      </th>
                      <th
                        className={`sortable${sortField === 'edits' ? ` sort-${sortDir}` : ''}`}
                        onClick={() => handleSort('edits')}
                        style={{ width: 55, textAlign: 'center' }}
                      >
                        Ed{sortIndicator('edits')}
                      </th>
                      <th
                        className={`sortable${sortField === 'writes' ? ` sort-${sortDir}` : ''}`}
                        onClick={() => handleSort('writes')}
                        style={{ width: 55, textAlign: 'center' }}
                      >
                        Wr{sortIndicator('writes')}
                      </th>
                      <th
                        className={`sortable${sortField === 'total_ops' ? ` sort-${sortDir}` : ''}`}
                        onClick={() => handleSort('total_ops')}
                        style={{ width: 70, textAlign: 'right' }}
                      >
                        Total{sortIndicator('total_ops')}
                      </th>
                      <th
                        className={`sortable${sortField === 'sessions' ? ` sort-${sortDir}` : ''}`}
                        onClick={() => handleSort('sessions')}
                        style={{ width: 70, textAlign: 'right' }}
                      >
                        Sessions{sortIndicator('sessions')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedFiles.map((f) => (
                      <tr key={f.path}>
                        <td>
                          <span className="path-cell">
                            {dirname(f.path) ? (
                              <span className="dirname">{dirname(f.path)}/</span>
                            ) : null}
                            <span className="filename">{filename(f.path)}</span>
                          </span>
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          {f.reads > 0 ? (
                            <span className="op-chip rd">{f.reads}</span>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          {f.edits > 0 ? (
                            <span className="op-chip ed">{f.edits}</span>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          {f.writes > 0 ? (
                            <span className="op-chip wr">{f.writes}</span>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td className="tnum op-num" style={{ textAlign: 'right' }}>
                          {f.total_ops}
                        </td>
                        <td className="tnum" style={{ textAlign: 'right', color: 'var(--muted)' }}>
                          {f.sessions}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
