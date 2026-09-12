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
  composeFillGapsPrompt,
  composeRoleGapPrompt,
  promptShapeFor,
  dispatchLabel,
} from '../lib/dispatch-prompt';
import {
  projectGaps,
  projectRoleGaps,
  type ProjectGaps,
  type RoleGapRow,
} from '../lib/project-gaps';
import { ProjectGapsDialog } from './ProjectGaps';
import {
  readProjectParam,
  withProjectParam,
  resolveProjectSelection,
} from '../lib/project-filter';
import {
  readActingRoleParam,
  withActingRoleParam,
  resolveActingRole,
} from '../lib/acting-role';
import { DispatchDialog } from './DispatchDialog';
import { DayCalendar } from './DayCalendar';
import { RoleActivityDialog } from './RoleActivity';
import { RoleRegister } from './RoleRegister';
import { CardTransitions } from './CardTransitions';
import {
  columnOf,
  columnsFor,
  conflictLabel,
  defaultGrouping,
  type Grouping,
} from '../lib/belt';
import { useToast, ToastContainer } from '../components/shared/Toast';
import './Work.css';

/** The two groupings of the board screen (row-status.md § สายพาน).
 *
 *  Not a third screen — ADR-0043 §SD1 capped `/work` at two, and this is the
 *  same rows read along the other axis: `สถานะ` answers *is this row open*,
 *  `stage` answers *where is it on the belt*. Column lists live in
 *  `src/lib/belt.ts` so the choice is checkable offline. */
const GROUPINGS = [
  { key: 'belt', label: 'สายพาน' },
  { key: 'status', label: 'สถานะแถว' },
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

/** The two screens of `/work` (ADR-0043 §SD1). `board` is not merely the first
 *  entry — it is the only state the page ever starts in, and there is nothing
 *  anywhere that can change that: no `?view=`, no `localStorage`. A session
 *  must never open a screen it did not ask for, which is why this is the one
 *  piece of `/work` state deliberately *not* pinned in the URL the way
 *  `?project=` is (S20 wanted a pinnable tab; a pinned screen is a different
 *  thing entirely). */
const VIEWS = [
  { key: 'board', label: 'บอร์ด' },
  { key: 'register', label: 'ทะเบียน role' },
] as const;

type View = (typeof VIEWS)[number]['key'];

export function WorkPage() {
  const [data, setData] = useState<WorkspaceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<{
    project: WorkspaceProject;
    slice: WorkspaceSlice;
  } | null>(null);
  const [grilling, setGrilling] = useState<WorkspaceProject | null>(null);
  // ADR-0040 — two steps on purpose: the gap panel shows what is missing, and
  // only a second press hands that list to a grill. Nothing dispatches from a
  // glance at a badge.
  const [gapsFor, setGapsFor] = useState<WorkspaceProject | null>(null);
  const [filling, setFilling] = useState<{
    project: WorkspaceProject;
    gaps: ProjectGaps;
  } | null>(null);
  // ADR-0042 §SD4 — one row of the gap panel, handed to the role that owns it.
  // Separate state from `filling` because they dispatch different roles at
  // different tiers: this one is a role out of roles.md, that one is `forge`.
  const [closing, setClosing] = useState<{
    project: WorkspaceProject;
    row: RoleGapRow;
  } | null>(null);
  // ADR-0039 · ADR-0041 — the belt panel reads git log for its fourth column,
  // so it is opened by a press and never on load: nothing about it belongs in
  // the board's own fetch.
  const [measuring, setMeasuring] = useState(false);
  // `null` until the payload arrives: the grouping a project opens in is a
  // fact of the file (does it declare stations at all), not a preference, so
  // it cannot be decided before the file is read. Once the operator picks, the
  // pick wins for the rest of the visit and is deliberately not stored — same
  // reasoning ADR-0043 §SD1 gave for the screen itself.
  const [grouping, setGrouping] = useState<Grouping | null>(null);
  // ADR-0043 §SD1 — always `board`, never restored from anywhere.
  const [view, setView] = useState<View>('board');
  // The board shows one project at a time, and which one lives in the URL so a
  // tab can be pinned to it (slices.md S20 §ก–§ข). Read once: nothing else
  // rewrites the query string, and the picker below keeps both in step.
  const [project, setProject] = useState<string | null>(() =>
    readProjectParam(window.location.search),
  );
  // Which role the operator is sitting as right now (slices.md S43a) — read
  // from `?actingRole=` the same way `project` reads `?project=`, and for the
  // same reason: each tab of a multi-tab board can stand on its own seat.
  const [actingRole, setActingRole] = useState<string | null>(() =>
    readActingRoleParam(window.location.search),
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

  const pickActingRole = useCallback((role: string | null) => {
    setActingRole(role);
    const { pathname, search, hash } = window.location;
    window.history.replaceState(
      null,
      '',
      `${pathname}${withActingRoleParam(search, role)}${hash}`,
    );
  }, []);

  const selection = resolveProjectSelection(data?.projects ?? [], project);
  const dispatchRoles = data?.dispatch.present ? data.dispatch.roles : [];
  // Resolved against the live table every render: a role dropped from
  // `roles.md` since the tab was bookmarked must not leave every transition
  // button silently answering for a seat nobody holds any more.
  const effectiveActingRole = resolveActingRole(dispatchRoles, actingRole);
  // The belt is offered from what the *shown* registers declare, not from the
  // workspace as a whole: picking a project that has no stations must not
  // leave the operator staring at nine empty columns.
  const beltAvailable = defaultGrouping(selection.shown) === 'belt';
  const activeGrouping: Grouping =
    grouping ?? (beltAvailable ? 'belt' : 'status');

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

  // S43b/§SD6 — the write lands on the card's own branch, not on `main`, so
  // reloading `/workspace` will not show the new `stage` until that branch is
  // merged (the same one-PR lag the board already prints for every other
  // write path). The toast is the confirmation; the board catching up is a
  // separate, already-known lag, not a bug this slice owns.
  const onMoved = useCallback(
    (branch: string, commit: string) => {
      toast(`ส่งต่อแล้ว · ${branch} ${commit}`);
      load();
    },
    [toast, load],
  );

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
        {/* A screen picker, not a third action button: the two beside it
            (*สายพานต่อ role* · *รีเฟรช*) do something, this one decides what
            you are looking at. ADR-0043 §SD1 — and the subbar itself is
            identical on both screens, so ADR-0041's panel is neither moved,
            hidden, nor duplicated. */}
        <div className="work-views" role="tablist" aria-label="จอของ /work">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              role="tab"
              aria-selected={view === v.key}
              className={`work-view${view === v.key ? ' on' : ''}`}
              onClick={() => setView(v.key)}
            >
              {v.label}
            </button>
          ))}
        </div>
        {/* The second axis, on the same screen (row-status.md § สายพาน).
            Only offered where the board has a belt to read: a register with no
            station declared would answer with nine empty columns. */}
        {view === 'board' && data && beltAvailable && (
          <div
            className="work-views"
            role="tablist"
            aria-label="แกนที่บอร์ดจัดกลุ่มด้วย"
          >
            {GROUPINGS.map((g) => (
              <button
                key={g.key}
                role="tab"
                aria-selected={activeGrouping === g.key}
                className={`work-view${activeGrouping === g.key ? ' on' : ''}`}
                onClick={() => setGrouping(g.key)}
              >
                {g.label}
              </button>
            ))}
          </div>
        )}
        {/* S43a — who is at the keyboard, separate from any row's own
            `role` cell (row-status.md § ตารางการส่งต่อ 🔑). Only offered
            where there is a belt to send a card along; the acting role has
            nothing to do on the status grouping. */}
        {view === 'board' && data && beltAvailable && (
          <label className="acting-role">
            สวมบทบาท
            <select
              value={effectiveActingRole ?? ''}
              onChange={(e) => pickActingRole(e.target.value || null)}
            >
              <option value="">— ยังไม่เลือก —</option>
              {dispatchRoles.map((r) => (
                <option key={r.role} value={r.role}>
                  {r.role}
                </option>
              ))}
            </select>
          </label>
        )}
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
          สายพานต่อ role
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

      {/* S40 — a row's `role` cell changing value is a handoff between
          stations; without this, the only way to notice is opening every
          project and reading every card's role by eye. Computed fresh from
          git history on every read (no ack, no storage) so a second real
          edit to the same row surfaces again on its own. */}
      {view === 'board' && data && data.handoffs.length > 0 && (
        <section className="handoff-strip" aria-label="แถวที่เพิ่งเปลี่ยนมือ">
          <span className="handoff-title">ส่งไม้แล้ว</span>
          <ul>
            {data.handoffs.map((h) => (
              <li key={`${h.project}-${h.id}`}>
                <b>{h.project}</b> {h.id} · {h.title} — {h.from} → {h.to}
              </li>
            ))}
          </ul>
        </section>
      )}

      {view === 'board' && data && data.gaps.present && (
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

      {/* One picker, above the split, because it is the same question on both
          screens (ADR-0043 Amendment, S34). It rides `?project=` — which the
          view deliberately does not (§SD1): a pinned tab should keep the
          *project* and still open on the board. */}
      {data && data.projects.length > 0 && (
        <ProjectPicker
          projects={data.projects}
          value={selection.value}
          unknown={selection.unknown}
          view={view}
          onPick={pickProject}
        />
      )}

      {data && view === 'register' && (
        <RoleRegister data={data} project={selection.picked} />
      )}

      {view === 'board' && data && data.projects.length === 0 && !loading && (
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
      {view === 'board' && (
        <DayCalendar dispatch={data?.dispatch ?? null} onDispatched={onDispatched} />
      )}

      {view === 'board' &&
        data &&
        selection.shown.map((p) => (
          <ProjectBoard
            key={p.name}
            project={p}
            data={data}
            grouping={beltAvailable ? activeGrouping : 'status'}
            actingRole={effectiveActingRole}
            onMoved={onMoved}
            onDispatch={(slice) => setPicked({ project: p, slice })}
            onGrill={() => setGrilling(p)}
            onGaps={() => setGapsFor(p)}
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
      {gapsFor && data && (
        <ProjectGapsDialog
          project={gapsFor}
          data={data}
          onClose={() => setGapsFor(null)}
          onFill={(gaps) => {
            setFilling({ project: gapsFor, gaps });
            setGapsFor(null);
          }}
          onDispatchRole={(row) => {
            setClosing({ project: gapsFor, row });
            setGapsFor(null);
          }}
        />
      )}
      {closing && data && (
        <RoleGapDialog
          project={closing.project}
          row={closing.row}
          dispatch={data.dispatch}
          onClose={() => setClosing(null)}
          onDispatched={onDispatched}
        />
      )}
      {filling && data && (
        <FillGapsDialog
          project={filling.project}
          gaps={filling.gaps}
          dispatch={data.dispatch}
          onClose={() => setFilling(null)}
        />
      )}
      {/* ADR-0041 §SD1 — the panel joins two payloads and does it here: the
          belt columns ride /workspace (HEAD), the shipped/open column rides
          /roles/activity (a window). Handing it the board's own data is what
          keeps ② from becoming a second parser of slices.md. */}
      {measuring && data && (
        <RoleActivityDialog workspace={data} onClose={() => setMeasuring(false)} />
      )}
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
  view,
  onPick,
}: {
  projects: WorkspaceProject[];
  value: string;
  unknown: string | null;
  /** Which screen is reading it — the picker is shared, what it filters is not
   *  (ADR-0043 Amendment, S34), and the line below has to say which. */
  view: 'board' | 'register';
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
        {view === 'board' ? (
          <>
            กรอง<b>เฉพาะการ์ดของ <code>slices.md</code></b> — ปฏิทินด้านบนเป็นของ
            ข้ามโปรเจกต์ และไม่ถูกกรอง
          </>
        ) : (
          <>
            กรอง<b>เฉพาะคอลัมน์ ⑤ ของทะเบียน</b> — 7 role · office · discipline ·
            ที่บันทึกผล · tier เป็นทะเบียนระดับ workspace และไม่ถูกกรอง
          </>
        )}
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

/**
 * The fill-gaps half of the dispatch surface (ADR-0040 §SD1).
 *
 * Deliberately a near-copy of `GrillDialog` rather than a shared abstraction:
 * they are the *same* shape (ADR-0037's mechanics, verbatim) with different
 * prompt content, and the day they need to diverge — a different role, a
 * different tab rule — a shared wrapper would be the thing standing in the way.
 * What must not diverge is the prompt spine, and that lives in one module.
 */
function FillGapsDialog({
  project,
  gaps,
  dispatch,
  onClose,
}: {
  project: WorkspaceProject;
  gaps: ProjectGaps;
  dispatch: WorkspaceDispatch;
  onClose: () => void;
}) {
  return (
    <DispatchDialog
      action="เติมช่องที่ขาด"
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
          session นี้ <b>ไม่ implement เอง</b> และ <b>ไม่แปลงช่องว่างเป็นแถวทุกช่อง</b> —
          กริลก่อน แล้วเปิด PR ที่แก้เฉพาะ <code>slices.md</code> ของ {project.name}
        </p>
      }
      compose={(role) => composeFillGapsPrompt(project, gaps, role)}
      onClose={onClose}
    />
  );
}

/**
 * One row of the gap panel, handed to the role that owns it (ADR-0042 §SD4).
 *
 * The opposite end of the dialog's range from `GrillDialog`: grill has no row
 * to resolve a role from and so picks a tier by hand (ADR-0037 §SD2), while
 * this row *is* a role — `sop-pipeline-handoff.md § 7.2` wrote its name — so
 * the tier comes off the roles.md table like every slice dispatch, and the
 * picker is locked to that one role. Swapping it would run the session under an
 * `Assignment:` id naming somebody else, which is the reason `fixedRole` exists
 * (ADR-0036 §SD3).
 *
 * `openMode` stays the default: this is a role doing its own work in the
 * background, not the live conversation grill is (ADR-0038 §SD1).
 */
function RoleGapDialog({
  project,
  row,
  dispatch,
  onClose,
  onDispatched,
}: {
  project: WorkspaceProject;
  row: RoleGapRow;
  dispatch: WorkspaceDispatch;
  onClose: () => void;
  onDispatched?: () => void;
}) {
  const files = row.surfaces.filter(
    (s) => s.level === 'project' && s.present === false,
  );
  return (
    <DispatchDialog
      action="ปิดช่องที่ขาด"
      subject={`${row.slug} · ${project.name}`}
      dispatch={dispatch}
      preferredRole={row.role}
      fixedRole={{
        role: row.role,
        why: `แถวนี้เขียนชื่อเจ้าของไว้แล้ว — ${row.slug} เป็น role ที่ § 7.2 ให้เซ็นบรรทัดนี้ ⇒ สลับ role ไม่ได้`,
      }}
      onDispatched={onDispatched}
      notice={
        <p className="dlg-grill">
          {files.length > 0 ? (
            <>
              session นี้อาจ<b>สร้างไฟล์ใหม่</b> ({files.map((s) => s.token).join(' · ')}) —
              แต่<b>ห้ามสร้างไฟล์เปล่า</b>: ถ้ามีใบอื่นทำหน้าที่นั้นอยู่แล้ว (§ 7.3)
              หรือยังไม่ถึงเวลา ให้เขียนไว้ว่าทำไม แล้วเปิดเป็นแถวแทน
            </>
          ) : (
            <>
              session นี้เปิด<b>แถวใน <code>slices.md</code></b> ของ {project.name} —
              หรือเขียนไว้ว่าทำไมโปรเจกต์นี้ไม่มีงานของ role นี้จริง ๆ
            </>
          )}
        </p>
      }
      compose={(role) => composeRoleGapPrompt(project, row, role)}
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
  data,
  grouping,
  actingRole,
  onMoved,
  onDispatch,
  onGrill,
  onGaps,
}: {
  project: WorkspaceProject;
  data: WorkspaceResponse;
  grouping: Grouping;
  /** S43a — the role picked at the board header. `null` before anyone has
   *  picked one. */
  actingRole: string | null;
  onMoved: (branch: string, commit: string) => void;
  onDispatch: (slice: WorkspaceSlice) => void;
  onGrill: () => void;
  onGaps: () => void;
}) {
  const dispatch = data.dispatch;
  const [reading, setReading] = useState<WorkspaceSlice | null>(null);
  // Same function the gap dialog uses, so the badge and the panel can never
  // report two different numbers (ADR-0040 §SD2, now counted by role —
  // ADR-0042 §SD1: a file that used to be counted twice is counted once).
  const gaps = projectRoleGaps(project, data);
  const missing = projectGaps(project, data).missingSlots;
  return (
    <section className="project">
      <div className="project-head">
        <h2>{project.name}</h2>
        <button
          className="gaps"
          onClick={onGaps}
          title="โปรเจกต์นี้ขาดอะไรจาก workflow ที่ team-os ประกาศไว้ — และส่งช่องที่ขาดเข้ากริล"
        >
          เติมช่องที่ขาด
          {gaps.count > 0 && <span className="n">{gaps.count}</span>}
        </button>
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
        {columnsFor(grouping).map((col) => {
          // `off` is context, not a queue — hidden in either grouping, and it
          // has no belt station to fall into either.
          const items = project.slices.filter(
            (s) =>
              s.column !== HIDDEN_COLUMN &&
              columnOf(s, grouping, project.slices) === col.key,
          );
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
                const blockers = s.blocked_by ?? [];
                // S38: a row waiting on a sibling row cannot start yet, no
                // matter which column its own glyph put it in — the blocker
                // wins over DISPATCHABLE the same way the owner mark wins
                // over the status glyph in `_column_for()`.
                // Both read `s.column`, never the column the card is sitting
                // in: whether a session can be opened is a fact of the row's
                // own status, and in the belt grouping `col.key` is a station.
                const shape = promptShapeFor(s.column);
                const dispatchable =
                  DISPATCHABLE.has(s.column) && blockers.length === 0;
                const conflict = conflictLabel(s);
                return (
                  <article className={`card c-${s.column}`} key={`${s.id}-${i}`}>
                    <span className="id">
                      {s.id !== '—' ? s.id : ''} {s.day && <em>· {s.day}</em>}
                    </span>
                    <span className="title">{s.title}</span>
                    {s.note && <span className="note">{s.note}</span>}
                    {blockers.length > 0 && (
                      <span className="blocked-by">
                        รอ: {blockers.map((b) => `${b.id} ${b.title}`).join(' · ')}
                      </span>
                    )}
                    {/* The other half of S38/S39: a row with nothing holding it
                        back reads identically to one whose blockers merely were
                        not declared. `blocked-by` already tells you when a row
                        cannot start; this tells you when one can — so several
                        of them can be picked up in parallel without opening the
                        file to check. Only on the two columns where starting is
                        the next thing that happens: a `running` row is already
                        being worked, and an `owner` row waits on a person, not
                        on a sibling row. */}
                    {blockers.length === 0 &&
                      (s.column === 'todo' || s.column === 'next') && (
                        <span className="ready-now">⚡ พร้อมหยิบ</span>
                      )}
                    {/* Assembled at display time from the rows that name this
                        one as their parent — the file still holds one criterion
                        per row (row-status.md § สายพาน). */}
                    {s.criteria && (
                      <span className="criteria">
                        เกณฑ์ {s.criteria.done}/{s.criteria.total}
                      </span>
                    )}
                    {/* The two axes disagreeing. Printed, never corrected: the
                        board reads files, it does not edit them
                        (meta/adr-slices-stage-axis-2026-09.md §SD3). */}
                    {conflict && <span className="axis-conflict">⚠ {conflict}</span>}
                    {/* S43a–S43c — a card's own row (not a `part-of`
                        criterion, which carries no stage of its own) on the
                        belt grouping offers whatever `evaluate()` says out of
                        its current station, for whichever role is sworn in
                        at the header. */}
                    {grouping === 'belt' && !s.part_of && s.stage && (
                      <CardTransitions
                        project={project.name}
                        sliceId={s.id}
                        stage={s.stage}
                        actingRole={actingRole}
                        onMoved={onMoved}
                      />
                    )}
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
