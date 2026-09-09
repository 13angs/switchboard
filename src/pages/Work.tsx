import { useState, useEffect, useCallback } from 'react';
import {
  fetchWorkspace,
  type WorkspaceResponse,
  type WorkspaceProject,
  type WorkspaceSlice,
  type WorkspaceDispatch,
} from '../lib/api';
import {
  composePrompt,
  composeGrillPrompt,
  promptShapeFor,
  dispatchLabel,
} from '../lib/dispatch-prompt';
import {
  readProjectParam,
  withProjectParam,
  resolveProjectSelection,
} from '../lib/project-filter';
import { DispatchDialog } from './DispatchDialog';
import { DayCalendar } from './DayCalendar';
import { RoleActivityDialog } from './RoleActivity';
import { useToast, ToastContainer } from '../components/shared/Toast';
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

/** Columns whose cards can be handed to a role.
 *
 *  `owner` joined the set at ADR-0036 §SD5, which read the 🖐️ mark one step
 *  narrower than ADR-0030 §SD5 did: the mark holds back the *action*, not the
 *  *thinking*, so a card in it can open a session that prepares the decision.
 *  What differs is the prompt, not the button's existence — `promptShapeFor()`
 *  decides which, and the button wears the matching word.
 *
 *  `done` stays closed: a finished piece has nothing to prepare, and reopening
 *  one means editing the file, not pressing a button on a board that only reads. */
const DISPATCHABLE = new Set(['running', 'next', 'todo', 'owner']);

export function WorkPage() {
  const [data, setData] = useState<WorkspaceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<{
    project: WorkspaceProject;
    slice: WorkspaceSlice;
  } | null>(null);
  const [grilling, setGrilling] = useState<WorkspaceProject | null>(null);
  // ADR-0039 — the participation panel reads git log, so it is opened by a
  // press and never on load: nothing about it belongs in the board's own fetch.
  const [measuring, setMeasuring] = useState(false);
  // The board shows one project at a time, and which one lives in the URL so a
  // tab can be pinned to it (slices.md S20 §ก–§ข). Read once: nothing else
  // rewrites the query string, and the picker below keeps both in step.
  const [project, setProject] = useState<string | null>(() =>
    readProjectParam(window.location.search),
  );
  // ADR-0038 §SD1 — a "stay" dispatch closes its dialog and toasts here
  // instead of navigating away.
  const { toasts, toast } = useToast();
  const onDispatched = useCallback(() => toast('สั่งแล้ว'), [toast]);

  const pickProject = useCallback((name: string | null) => {
    setProject(name);
    const { pathname, search, hash } = window.location;
    window.history.replaceState(
      null,
      '',
      `${pathname}${withProjectParam(search, name)}${hash}`,
    );
  }, []);

  const selection = resolveProjectSelection(data?.projects ?? [], project);

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
          className="participation"
          onClick={() => setMeasuring(true)}
        >
          การมีส่วนร่วมต่อ role
        </button>
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

      {/* Above the picker on purpose: the calendar is the day, not a project
          (slices.md S20 §ง). Filtering it would hide blocks the owner still
          owes time to just because they belong to another piece of work. */}
      <DayCalendar dispatch={data?.dispatch ?? null} onDispatched={onDispatched} />

      {data && data.projects.length > 0 && (
        <ProjectPicker
          projects={data.projects}
          value={selection.value}
          unknown={selection.unknown}
          onPick={pickProject}
        />
      )}

      {data &&
        selection.shown.map((p) => (
          <ProjectBoard
            key={p.name}
            project={p}
            dispatch={data.dispatch}
            onDispatch={(slice) => setPicked({ project: p, slice })}
            onGrill={() => setGrilling(p)}
          />
        ))}

      {picked && data && (
        <SliceDialog
          {...picked}
          dispatch={data.dispatch}
          onClose={() => setPicked(null)}
          onDispatched={onDispatched}
        />
      )}
      {grilling && data && (
        <GrillDialog
          project={grilling}
          dispatch={data.dispatch}
          onClose={() => setGrilling(null)}
        />
      )}
      {measuring && <RoleActivityDialog onClose={() => setMeasuring(false)} />}
      <ToastContainer toasts={toasts} />
    </div>
  );
}

/**
 * One control, one line, whatever the project count (slices.md S20 §ก).
 *
 * A tab strip taps better, but its cost grows with exactly the number this
 * slice exists because it is growing — and the header already carries a tab
 * strip for Sessions/Work/Analytics, so a second one directly beneath it would
 * read as navigation rather than a filter. A native `<select>` stays one line
 * at 5 projects and at 31, and on a tablet it opens the platform's own
 * full-screen list, whose rows are bigger than any chip row built here.
 *
 * *ทั้งหมด* stays, and stays the default (§ค): with no `?project=` the board
 * must not decide for the reader which project matters, and a first-project
 * default would hide work behind a choice nobody made.
 */
function ProjectPicker({
  projects,
  value,
  unknown,
  onPick,
}: {
  projects: WorkspaceProject[];
  value: string;
  unknown: string | null;
  onPick: (name: string | null) => void;
}) {
  return (
    <div className="project-picker">
      <div className="picker-row">
        <label htmlFor="project-pick">โปรเจกต์</label>
        <select
          id="project-pick"
          value={value}
          onChange={(e) => onPick(e.target.value || null)}
        >
          <option value="">ทั้งหมด · {projects.length} โปรเจกต์</option>
          {projects.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name} · {p.slices.length} แถว
            </option>
          ))}
        </select>
      </div>
      {/* Printed, never a `title=` — hover does not exist on the tablet this
          board is read on (ADR-0036 §SD5(ค), the lesson S9 paid for). */}
      <p className="picker-scope">
        กรอง<b>เฉพาะการ์ดของ <code>slices.md</code></b> — ปฏิทินด้านบนเป็นของข้ามโปรเจกต์
        และไม่ถูกกรอง
      </p>
      {unknown && (
        <p className="picker-unknown">
          ไม่มีโปรเจกต์ <code>{unknown}</code> บนบอร์ดนี้ — แสดงทั้งหมดแทน ·
          บอร์ดอ่านเฉพาะโปรเจกต์ที่มี <code>slices.md</code>
        </p>
      )}
    </div>
  );
}

/** The slice half of the dispatch surface: it decides the shape from the card's
 *  column, then hands the dialog a composer. The dialog itself knows nothing
 *  about slices — the calendar's rituals reach it the same way (ADR-0036 §SD4). */
function SliceDialog({
  project,
  slice,
  dispatch,
  onClose,
  onDispatched,
}: {
  project: WorkspaceProject;
  slice: WorkspaceSlice;
  dispatch: WorkspaceDispatch;
  onClose: () => void;
  onDispatched?: () => void;
}) {
  const shape = promptShapeFor(slice.column);
  return (
    <DispatchDialog
      action={dispatchLabel(shape)}
      subject={slice.id !== '—' ? slice.id : project.name}
      dispatch={dispatch}
      preferredRole={slice.role ?? project.default_role}
      onDispatched={onDispatched}
      notice={
        shape === 'prepare' ? (
          <p className="dlg-prepare">
            แถวนี้อยู่คอลัมน์ <b>คนเคาะ</b> — session
            นี้เตรียมตัวเลือกกับข้อเสนอมาให้แล้วหยุด ·{' '}
            <b>ไม่ merge · ไม่ลบ branch · ไม่เคาะแทนคุณ</b>
          </p>
        ) : null
      }
      compose={(role) => composePrompt(project, slice, role, shape)}
      onClose={onClose}
    />
  );
}

/**
 * The grill half of the dispatch surface (ADR-0037). Unlike `SliceDialog`, it
 * hands the dialog a tier/effort picker instead of a role — grill runs before
 * any row exists to resolve a role from (§SD1), so there is nothing for
 * `dispatch.roles` to look `forge` up in.
 */
function GrillDialog({
  project,
  dispatch,
  onClose,
}: {
  project: WorkspaceProject;
  dispatch: WorkspaceDispatch;
  onClose: () => void;
}) {
  return (
    <DispatchDialog
      action="grill slices"
      subject={project.name}
      dispatch={dispatch}
      preferredRole={null}
      tierPicker={
        dispatch.present
          ? {
              role: 'forge',
              tiers: dispatch.tiers,
              defaultTier: 'heavy',
              defaultEffort: 'high',
            }
          : null
      }
      openMode="new-tab"
      notice={
        <p className="dlg-grill">
          session นี้ <b>ไม่ implement เอง</b> — คุยจนตกผลึกแล้วเปิด PR ที่แก้เฉพาะ{' '}
          <code>slices.md</code> ของ {project.name} เท่านั้น
        </p>
      }
      compose={(role) => composeGrillPrompt(project, role)}
      onClose={onClose}
    />
  );
}

/** A title or note past this many characters gets clamped by CSS, so the
 *  [อ่านเต็ม] button only needs to know the character count — never the
 *  rendered DOM height (slices.md S21 §(ง)). */
const READ_MORE_THRESHOLD = 100;

function ProjectBoard({
  project,
  dispatch,
  onDispatch,
  onGrill,
}: {
  project: WorkspaceProject;
  dispatch: WorkspaceDispatch;
  onDispatch: (slice: WorkspaceSlice) => void;
  onGrill: () => void;
}) {
  const [reading, setReading] = useState<WorkspaceSlice | null>(null);
  const missing = (['scope', 'risks', 'hld'] as const).filter(
    (k) => !project.has[k],
  );
  return (
    <section className="project">
      <div className="project-head">
        <h2>{project.name}</h2>
        <button
          className="grill"
          onClick={onGrill}
          title="เปิด session role forge (grill-me) ในแท็บใหม่ — ตกผลึกเป็นแถวใหม่ใน slices.md ของโปรเจกต์นี้"
        >
          grill slices
        </button>
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
          const shape = promptShapeFor(col.key);
          return (
            <div className="col" key={col.key}>
              <div className={`col-head c-${col.key}`}>
                <span className="dot" />
                {col.label}
                <span className="count">{items.length}</span>
              </div>
              {items.map((s, i) => {
                const long =
                  s.title.length >= READ_MORE_THRESHOLD ||
                  (s.note?.length ?? 0) >= READ_MORE_THRESHOLD;
                const dispatchable = DISPATCHABLE.has(col.key);
                return (
                  <article className={`card c-${col.key}`} key={`${s.id}-${i}`}>
                    <span className="id">
                      {s.id !== '—' ? s.id : ''} {s.day && <em>· {s.day}</em>}
                    </span>
                    <span className="title">{s.title}</span>
                    {s.note && <span className="note">{s.note}</span>}
                    {(long || dispatchable) && (
                      <div className="card-foot">
                        {long && (
                          <button
                            className="read-more"
                            onClick={() => setReading(s)}
                          >
                            อ่านเต็ม
                          </button>
                        )}
                        {dispatchable && (
                          <>
                            <button
                              className={`dispatch s-${shape}`}
                              onClick={() => onDispatch(s)}
                              title={
                                !dispatch.present
                                  ? 'อ่านแผนที่ role → model ไม่ได้ — กดเพื่อดูเหตุผล'
                                  : shape === 'prepare'
                                    ? 'เลือก role แล้วเปิด session ที่เตรียมเรื่องให้คุณเคาะ'
                                    : 'เลือก role แล้วเปิด session ที่ปักหมุด tier ไว้'
                              }
                            >
                              {dispatchLabel(shape)}
                            </button>
                            {/* Printed, never a `title=`: the owner reads this
                                board on a tablet and hover does not exist
                                there — the lesson S9 paid for once already
                                (ADR-0036 §SD5(ค)). */}
                            {shape === 'prepare' && (
                              <span className="dispatch-why">
                                ปุ่มนี้ไม่ลงมือแทนคุณ — ได้ตัวเลือกกับข้อเสนอ
                                แล้วคุณเคาะ
                              </span>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
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
      {reading && <ReadDialog slice={reading} onClose={() => setReading(null)} />}
    </section>
  );
}

/** Read-only expansion of one card's clamped text (slices.md S21 §(ข)) — the
 *  same `.dlg-backdrop`/`.dlg` frame `DispatchDialog` uses, but with no role
 *  picker and nothing to send: closing is the only action it offers. */
function ReadDialog({
  slice,
  onClose,
}: {
  slice: WorkspaceSlice;
  onClose: () => void;
}) {
  return (
    <div className="dlg-backdrop" onClick={onClose}>
      <div
        className="dlg"
        role="dialog"
        aria-modal="true"
        aria-label={slice.title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dlg-head">
          <h3>{slice.id !== '—' ? slice.id : slice.title}</h3>
          <button className="dlg-x" onClick={onClose} aria-label="ปิด">
            ✕
          </button>
        </div>
        <p className="dlg-read-title">{slice.title}</p>
        {slice.note && <p className="dlg-read-note">{slice.note}</p>}
      </div>
    </div>
  );
}
