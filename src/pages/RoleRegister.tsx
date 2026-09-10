import type { WorkspaceResponse, RegisterLevel } from '../lib/api';
import {
  roleRegister,
  assignmentQuery,
  pointsAtNothingIn,
  projectLevel,
  type RegisterRow,
} from '../lib/role-register';

/**
 * The second screen of `/work` (ADR-0043, S33): the register as one table.
 *
 * Not a panel behind a button, and not a third button. What sits in a dialog is
 * a *question you go and ask* — ADR-0041's belt ("is this role ready?"),
 * ADR-0042's gaps ("what does this project miss?"). This is a reference table
 * read constantly, and the whole reason S33 exists is that everything added to
 * `/work` so far was a button or an overlay, leaving the register nowhere to
 * land.
 *
 * Six columns, and they answer one sentence end to end: *press this card and
 * you get which brain · its result sleeps where · is there anything there yet*.
 *
 * Everything is printed as *text*. No `title=`, no hover disclosure: the board
 * is read on a tablet, the lesson `S9` paid for once already (ADR-0036 §SD5(ค)).
 */
export function RoleRegister({
  data,
  project,
}: {
  data: WorkspaceResponse;
  /** The shared picker's selection, `null` for *ทั้งหมด* (ADR-0043 Amendment,
   *  S34). It narrows column ⑤ only: ①–④ and ⑥ come from a register that is
   *  workspace-level by construction, and pretending otherwise would print a
   *  different set of roles per project. */
  project: string | null;
}) {
  const view = roleRegister(data);
  const register = data.register;

  return (
    <section className="rr" aria-label="ทะเบียน role">
      <div className="rr-lede">
        <h2>ทะเบียน role</h2>
        <p>
          <b>role นี้คือใคร</b> — office · discipline ที่เปิดอ่าน · ที่บันทึกผล ·
          tier ที่บอร์ดจะปักหมุดให้จริงตอนกด · อ่านสดจาก{' '}
          <code>{register.source}</code> ทุกครั้ง ไม่มีสำเนาในแอป
        </p>
        {/* Printed, never a `title=` — the picker changes one column and it
            must be obvious which, or a filtered screen reads as a different
            register. */}
        <p className="rr-scope">
          {project ? (
            <>
              กรอง <b>เฉพาะคอลัมน์ ⑤</b> ให้เหลือของใต้{' '}
              <code>projects/{project}/</code> — ①–④ และ ⑥ เป็นทะเบียนระดับ
              workspace <b>ไม่ถูกกรอง</b> (7 role เท่าเดิมทุกโปรเจกต์)
            </>
          ) : (
            <>
              กำลังดู <b>ทั้งหมด</b> — เลือกโปรเจกต์จาก dropdown ด้านบนเพื่อกรอง
              คอลัมน์ ⑤ ให้เหลือของใต้โปรเจกต์นั้น
            </>
          )}
        </p>
      </div>

      {view.empty && (
        <div className="rr-blocked">
          <p>อ่านทะเบียนไม่ได้ทั้งสองครึ่ง — ตารางนี้จึงว่าง ไม่ใช่ว่าไม่มี role</p>
          <p className="why">
            § {register.section}: {register.reason || 'ไม่ทราบสาเหตุ'}
          </p>
          <p className="why">
            § โมเดลต่อ role:{' '}
            {data.dispatch.present ? 'อ่านได้' : data.dispatch.reason}
          </p>
        </div>
      )}

      {!view.empty && (
        <div className="rr-table" role="table">
          <div className="rr-row rr-head" role="row">
            <span className="rr-cell">
              ① role
              <em>§ {register.section}</em>
            </span>
            <span className="rr-cell">
              ② office
              <em>ช่องที่ 2 ของ Assignment</em>
            </span>
            <span className="rr-cell">
              ③ discipline ที่เปิดใน <code>team/</code>
              <em>คลัง SOP — ไม่ใช่เจ้าของงาน</em>
            </span>
            <span className="rr-cell">
              ④ บันทึกผลลงที่
              <em>ข้อความตามที่ทะเบียนเขียน</em>
            </span>
            <span className="rr-cell">
              ⑤ ไฟล์จริงในเวิร์กสเปซ
              <em>
                {project ? `เฉพาะ ${project}` : 'ทุกโปรเจกต์'} · HEAD{' '}
                {data.head ? data.head.slice(0, 7) : 'ไม่ใช่ git'}
              </em>
            </span>
            <span className="rr-cell">
              ⑥ tier · effort
              <em>ปักหมุดตอนกดสั่งงาน</em>
            </span>
          </div>
          {view.rows.map((row) => (
            <Row
              key={row.slug}
              row={row}
              project={project}
              dispatchReason={dispatchReason(data)}
            />
          ))}
        </div>
      )}

      <Footnotes data={data} rows={view.rows} />
    </section>
  );
}

function dispatchReason(data: WorkspaceResponse): string {
  return data.dispatch.present ? '' : data.dispatch.reason;
}

function Row({
  row,
  project,
  dispatchReason,
}: {
  row: RegisterRow;
  project: string | null;
  dispatchReason: string;
}) {
  const own = row.ownership;
  const records = own?.records ?? null;
  return (
    <div className="rr-row" role="row">
      {/* ① */}
      <span className="rr-cell rr-role" data-label="① role">
        <b>{row.name}</b>
        <code>{row.slug}</code>
        {row.side === 'dispatch' && (
          <em className="warn">§ แกนความเป็นเจ้าของ ไม่มีแถวนี้</em>
        )}
        {row.side === 'ownership' && (
          <em className="warn">§ โมเดลต่อ role ไม่มีแถวนี้</em>
        )}
      </span>

      {/* ② */}
      <span className="rr-cell" data-label="② office">
        {own === null ? (
          <span className="rr-dark">อ่านไม่ได้</span>
        ) : own.office ? (
          <code>{own.office}</code>
        ) : (
          <em className="warn">เว้นไว้ในทะเบียน</em>
        )}
      </span>

      {/* ③ */}
      <span className="rr-cell" data-label="③ discipline">
        {own === null ? (
          <span className="rr-dark">อ่านไม่ได้</span>
        ) : own.disciplines.length > 0 ? (
          own.disciplines.map((d) => <code key={d}>{d}</code>)
        ) : (
          <span className="rr-half">— ไม่ถือ discipline leaf</span>
        )}
        {own && own.disciplines_dropped.length > 0 && (
          <em>
            เซลล์นี้มีข้อความที่ไม่ใช่ discipline อีก{' '}
            {own.disciplines_dropped.length} ชิ้น — ไม่นับเข้าตาราง ·{' '}
            <span className="rr-raw">{own.disciplines_raw}</span>
          </em>
        )}
      </span>

      {/* ④ — the cell verbatim, so ⑤ can be read against its own source. */}
      <span className="rr-cell" data-label="④ บันทึกผลลงที่">
        {records === null ? (
          <span className="rr-dark">อ่านไม่ได้</span>
        ) : (
          <span className="rr-raw">{records.text || '—'}</span>
        )}
      </span>

      {/* ⑤ */}
      <span className="rr-cell" data-label="⑤ ไฟล์จริง">
        <Files row={row} project={project} />
      </span>

      {/* ⑥ */}
      <span className="rr-cell rr-tier" data-label="⑥ tier · effort">
        {row.tier === null ? (
          <span className="rr-dark">
            {dispatchReason || 'อ่าน § โมเดลต่อ role ไม่ได้'}
          </span>
        ) : (
          <>
            <b className={`t-${row.tier.tier}`}>{row.tier.tier}</b>
            <em>{row.tier.effort ? `effort ${row.tier.effort}` : 'ไม่ส่ง effort'}</em>
            <code>{row.tier.model}</code>
          </>
        )}
      </span>
    </div>
  );
}

/**
 * Column ⑤, and the one distinction the owner made a rule of (ADR-0043 §SD4).
 *
 * An empty cell here would mean *the pattern could hold a file and nobody has
 * written one*. Three of the seven roles do not record into a file at all —
 * `senior-developer`/`developer` close in a commit body, `qa` in a PR thread —
 * and printing those as blank sends a reader off to create files roles.md
 * never asked for. So they print what they are, with the query that finds the
 * record where it actually lives.
 */
function Files({ row, project }: { row: RegisterRow; project: string | null }) {
  const records = row.ownership?.records ?? null;
  if (records === null) return <span className="rr-dark">อ่านไม่ได้</span>;

  if (records.kind === 'not-files') {
    return (
      <>
        <span className="rr-half">◐</span> <b>ไม่ใช่ไฟล์ — อยู่ใน git log / PR</b>
        <code className="rr-cmd">{assignmentQuery(row.slug)}</code>
        <em>
          ทะเบียน<b>ไม่ได้สั่ง</b>ให้ role นี้เขียนไฟล์ — คนละเรื่องกับช่องที่ยังว่าง
        </em>
      </>
    );
  }

  return (
    <>
      {records.targets.map((t) => (
        <span className="rr-target" key={t.token}>
          <code>{t.token}</code>
          {t.glob !== t.token && (
            <em>
              glob ที่ใช้จริง <code>{t.glob}</code>
            </em>
          )}
          <span className="rr-levels">
            <Level
              label={
                project
                  ? `ใต้ projects/${project}/`
                  : 'ใต้ projects/ (ทุกโปรเจกต์)'
              }
              {...projectLevel(t, project)}
            />
            {/* Kept on screen even with a project picked, and kept second: a
                workspace-level target (`meta/adr-*.md`) has nothing under a
                project to measure, and printing it as this project's zero is
                exactly the ✗-that-should-be-◐ ADR-0042 §SD3 refused. */}
            <Level
              label="ระดับ workspace"
              level={t.levels.workspace}
              scoped={false}
              muted={Boolean(project)}
            />
          </span>
        </span>
      ))}
      {pointsAtNothingIn(row, project) && (
        <em className="warn">
          {project ? (
            <>
              ทะเบียนสั่งให้เขียนไฟล์ แต่ <code>{project}</code> ยังไม่มีสักใบ —
              ช่องนี้ว่าง<b>เฉพาะโปรเจกต์นี้</b>
            </>
          ) : (
            <>ทะเบียนสั่งให้เขียนไฟล์ แต่ยังไม่มีใครเขียนสักใบ — ช่องนี้ว่างจริง</>
          )}
        </em>
      )}
      {records.rejected.length > 0 && (
        <em className="warn">
          เป้าที่ปฏิเสธ (ออกนอก workspace):{' '}
          {records.rejected.map((t) => (
            <code key={t}>{t}</code>
          ))}
        </em>
      )}
      {records.note && <em>{records.note}</em>}
    </>
  );
}

/** One level of one target. Split out because the picker made the two levels
 *  behave differently: the project half narrows, the workspace half cannot —
 *  and the screen has to keep saying which is which. */
function Level({
  label,
  level,
  scoped,
  muted = false,
}: {
  label: string;
  /** `null` when a project is picked but the payload predates the per-project
   *  split — *the board cannot tell*, which is not the same as zero. */
  level: RegisterLevel | null;
  scoped: boolean;
  muted?: boolean;
}) {
  if (level === null) {
    return (
      <span className="rr-level">
        {label} <span className="rr-dark">payload รุ่นเก่า — แยกรายโปรเจกต์ไม่ได้</span>
      </span>
    );
  }
  return (
    <span className={`rr-level${muted ? ' muted' : ''}`}>
      {label} <b className={level.have === 0 ? 'warn' : ''}>{level.have}</b> ที่
      {!scoped && level.projects ? <i> · {level.projects} โปรเจกต์</i> : null}
      {level.paths.length > 0 && (
        <span className="rr-paths">
          {level.paths.map((p) => (
            <code key={p}>{p}</code>
          ))}
          {level.more > 0 && <i>+{level.more}</i>}
        </span>
      )}
    </span>
  );
}

/** What this table is standing on, and the one column it deliberately does not
 *  carry. Printed under the table rather than hidden: a screen that quietly
 *  omits an escalation rule reads as a screen that says there is none. */
function Footnotes({
  data,
  rows,
}: {
  data: WorkspaceResponse;
  rows: RegisterRow[];
}) {
  const register = data.register;
  const heavy = register.heavy_when;
  const drifted = rows.filter((r) => r.side !== 'both');
  return (
    <div className="rr-notes">
      <h4>อ่านตารางนี้ยังไง</h4>
      <ul>
        <li>
          ที่มา <code>{register.source}</code> § {register.section} (①–⑤) และ
          § โมเดลต่อ role (⑥) · อ่านจาก <code>HEAD</code>{' '}
          <code>{data.head ? data.head.slice(0, 7) : 'ไม่ใช่ git'}</code> ⇒{' '}
          <b>ตารางขยับเองเมื่อทะเบียนขยับ</b>
        </li>
        <li className="warn">
          คอลัมน์ <b>{heavy.column}</b>{' '}
          {heavy.readable ? 'อ่านได้แล้ว' : <b>ยังอ่านไม่ได้ และไม่อยู่ในตารางนี้</b>} —{' '}
          {heavy.reason} ⇒ เป็นงานของแถว <code>{heavy.slice}</code> · บอร์ด
          <b>ไม่เดาแทน</b>: ⑥ คือ tier ที่จะได้จริงตอนกด ไม่ใช่ tier ที่ควรได้
        </li>
        <li>
          ⑤ นับ <b>การมีอยู่ของพาธ</b> ไม่ได้นับความจริงของเนื้อใน — เป็นเพดานล่าง
          เหมือนคอลัมน์เดียวกันของแผง <i>สายพานต่อ role</i>
        </li>
        <li>
          ③ กับ ④ พิมพ์ <b>เซลล์ดิบ</b> ไว้ข้าง ๆ ผลที่อ่านได้เสมอ — วันที่ทะเบียน
          เปลี่ยนถ้อยคำแล้วตัวอ่านหลุด คนอ่านเห็นว่าหลุดตรงไหน (บรรเทา ไม่ได้กัน ·{' '}
          <code>risks.md</code> <code>S-01</code>)
        </li>
        {drifted.length > 0 && (
          <li className="warn">
            <b>{drifted.length}</b> แถวมีอยู่ในทะเบียนครึ่งเดียว (
            {drifted.map((r, i) => (
              <span key={r.slug}>
                {i > 0 && ' · '}
                <code>{r.slug}</code>
              </span>
            ))}
            ) — สองตารางของ <code>roles.md</code> หลุดจากกัน
          </li>
        )}
      </ul>
    </div>
  );
}
