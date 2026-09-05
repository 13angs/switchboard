import { useState, useEffect, useCallback } from 'react';
import {
  fetchWorkspace,
  type WorkspaceResponse,
  type WorkspaceProject,
} from '../lib/api';
import './Work.css';

/** Board columns, in reading order. Mirrors control_plane/workspace.py COLUMN_ORDER. */
const COLUMNS = [
  { key: 'done', label: 'เสร็จแล้ว' },
  { key: 'running', label: 'กำลังทำ' },
  { key: 'next', label: 'ถัดไป' },
  { key: 'todo', label: 'รอคิว' },
  { key: 'owner', label: 'คนเคาะ' },
] as const;

/** `off` is not a column: days with no work are context, not a queue. */
const HIDDEN_COLUMN = 'off';

export function WorkPage() {
  const [data, setData] = useState<WorkspaceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchWorkspace(refresh));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workspace');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Re-read on tab focus rather than polling: the tree only changes on merge.
    const onFocus = () => load();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [load]);

  return (
    <div className="work">
      <header className="work-header">
        <div className="brand">
          <span className="mark">Switchboard</span>
          <span className="section">· Work</span>
        </div>
        {data && <span className="repo">{data.repo}</span>}
        <nav className="tabs">
          <a href="/">Sessions</a>
          <a className="on" href="/work">
            Work
          </a>
          <a href="/analytics">Analytics</a>
        </nav>
      </header>

      <div className="work-subbar">
        {data && (
          <span className="provenance" title={data.stale_by}>
            HEAD <code>{data.head ? data.head.slice(0, 7) : 'ไม่ใช่ git'}</code>
            <span className="lag">
              · อ่านจากไฟล์ที่ merge แล้ว — ช้ากว่างานจริง 1 PR
            </span>
          </span>
        )}
        <button
          className="refresh"
          onClick={() => load(true)}
          disabled={loading}
        >
          {loading ? 'กำลังอ่าน…' : 'รีเฟรช'}
        </button>
      </div>

      {error && <div className="work-error">อ่านไม่ได้: {error}</div>}

      {data && data.gaps.present && (
        <section className="gap-strip" aria-label="ช่องว่างของโครงเอกสาร">
          <span className="gap-title">ช่องว่างที่ประกาศไว้</span>
          <span className="gap-n total">{data.gaps.total}</span>
          <span className="gap-split">
            <b className="closed">{data.gaps.closed}</b> ปิด ·
            <b className="reduced"> {data.gaps.reduced}</b> ลดแล้ว ·
            <b className="open"> {data.gaps.open}</b> เปิด
          </span>
        </section>
      )}

      {data && data.projects.length === 0 && !loading && (
        <div className="work-empty">
          <p>
            ยังไม่มีโปรเจกต์ไหนมี <code>slices.md</code>
          </p>
          <p className="hint">
            บอร์ดนี้อ่านชิ้นงานจาก <code>projects/&lt;name&gt;/slices.md</code>{' '}
            — ไฟล์ไหนยังไม่มี โปรเจกต์นั้นจะไม่ขึ้นที่นี่
          </p>
        </div>
      )}

      {data?.projects.map((p) => (
        <ProjectBoard key={p.name} project={p} />
      ))}
    </div>
  );
}

function ProjectBoard({ project }: { project: WorkspaceProject }) {
  const missing = (['scope', 'risks', 'hld'] as const).filter(
    (k) => !project.has[k],
  );
  return (
    <section className="project">
      <div className="project-head">
        <h2>{project.name}</h2>
        {missing.length > 0 && (
          <span
            className="missing"
            title="ไฟล์ที่โครง team-os บังคับแต่โปรเจกต์นี้ยังไม่มี"
          >
            ยังไม่มี: {missing.join(' · ')}
          </span>
        )}
      </div>
      <div className="cols">
        {COLUMNS.map((col) => {
          const items = project.slices.filter((s) => s.column === col.key);
          return (
            <div className="col" key={col.key}>
              <div className={`col-head c-${col.key}`}>
                <span className="dot" />
                {col.label}
                <span className="count">{items.length}</span>
              </div>
              {items.map((s, i) => (
                <article className={`card c-${col.key}`} key={`${s.id}-${i}`}>
                  <span className="id">
                    {s.id !== '—' ? s.id : ''} {s.day && <em>· {s.day}</em>}
                  </span>
                  <span className="title">{s.title}</span>
                  {s.note && <span className="note">{s.note}</span>}
                </article>
              ))}
              {items.length === 0 && <div className="col-empty">—</div>}
            </div>
          );
        })}
      </div>
      {project.slices.some((s) => s.column === HIDDEN_COLUMN) && (
        <p className="off-note">
          {project.slices.filter((s) => s.column === HIDDEN_COLUMN).length}{' '}
          แถวเป็นวันที่ไม่มีงาน — ไม่แสดงเป็นคอลัมน์
        </p>
      )}
    </section>
  );
}
