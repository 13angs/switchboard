import { useState, useEffect, useCallback } from 'react';
import {
  fetchRoleActivity,
  type RoleActivityResponse,
  type RoleActivityRow,
  type WorkspaceResponse,
  type PipelineSignature,
} from '../lib/api';
import { rowsPerRole } from '../lib/project-gaps';

/** Windows the panel offers, in owner-timezone days (ADR-0039 §SD7). Fixed
 *  presets rather than a free field: the question is "has this role been quiet
 *  lately", and a date picker would invite precision the count does not have. */
const WINDOWS = [7, 14, 30, 90] as const;
const DEFAULT_WINDOW = 14;

/**
 * The one button S31 asks for: the belt as a table, seven roles by four columns.
 *
 * ① station · ② cards · ③ closing surface · ④ shipped/open. The first three are
 * a *chain*, not three metrics that happen to sit in a row — a station with no
 * card is work nobody can be handed, and a card with no closing surface is work
 * that runs to the end of the belt and finds nowhere to sign. ④ (ADR-0039, the
 * panel this one grew out of) says whether anyone has walked the chain lately;
 * it is a fourth column here rather than a second button.
 *
 * Two payloads meet on this screen and the join happens here, not on a server:
 * ① and ③ are functions of HEAD and ride `/workspace`, ④ is a function of a
 * *window* and rides `/roles/activity` — ADR-0039 §SD1 drew that line and
 * ADR-0041 §SD1 keeps it. ② is computed in the browser from the same call the
 * card badges use, so a badge and this table can never disagree.
 *
 * Everything is printed as *text*. No `title=`, no hover disclosure: the board
 * is read on a tablet, which is the lesson `S9` paid for once already
 * (ADR-0036 §SD5(ค)).
 */
export function RoleActivityDialog({
  workspace,
  onClose,
}: {
  workspace: WorkspaceResponse;
  onClose: () => void;
}) {
  const [days, setDays] = useState<number>(DEFAULT_WINDOW);
  const [data, setData] = useState<RoleActivityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (window: number) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchRoleActivity(window));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'อ่านไม่ได้');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(days);
  }, [days, load]);

  const pipeline = workspace.pipeline;
  const cards = rowsPerRole(workspace.projects);
  const signatures = new Map(
    pipeline.signatures.rows.map((r) => [r.role, r] as const),
  );

  return (
    <div className="dlg-backdrop" onClick={onClose}>
      <div
        className="dlg ra"
        role="dialog"
        aria-modal="true"
        aria-label="สายพานต่อ role"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dlg-head">
          <h3>สายพานต่อ role</h3>
          <button className="dlg-x" onClick={onClose} aria-label="ปิด">
            ✕
          </button>
        </div>

        <p className="ra-lede">
          role หนึ่ง <b>รับงานแล้วปิดงานได้จริงหรือยัง</b> — สามพื้นผิวของสายพาน
          คู่กับจำนวนใบที่เดินจริงในช่วงที่เลือก
        </p>

        <div className="ra-windows">
          {WINDOWS.map((w) => (
            <button
              key={w}
              className={`ra-win${w === days ? ' on' : ''}`}
              onClick={() => setDays(w)}
              disabled={loading}
            >
              {w} วัน
            </button>
          ))}
          {data && data.present && (
            <span className="ra-since">
              ตั้งแต่ <code>{data.window.since}</code> · เวลาเจ้าของ{' '}
              <code>{data.window.tz}</code>
            </span>
          )}
        </div>

        {loading && <p className="ra-loading">กำลังอ่าน <code>git log</code>…</p>}
        {error && <p className="dlg-error">อ่านไม่ได้: {error}</p>}

        {data && !data.present && (
          <div className="dlg-blocked">
            <p>{data.reason}</p>
            <p className="why">
              รายชื่อ role มาจาก <code>{data.source}</code> เท่านั้น — บอร์ดไม่เดา
              รายชื่อจากค่าที่เจอใน <code>git log</code> เพราะ role ที่ยังไม่เคยลงมือ
              จะหายไปพร้อมกับช่องโหว่ที่กำลังหาอยู่
            </p>
          </div>
        )}

        {data && data.present && !loading && (
          <>
            <div className="ra-table ra-belt" role="table">
              <div className="ra-row ra-head" role="row">
                <span className="ra-role">role</span>
                <span className="ra-cell">
                  ① สถานีบนสายพาน
                  <em>{pipeline.source} § Scope</em>
                </span>
                <span className="ra-cell">
                  ② การ์ดบนบอร์ด
                  <em>projects/*/slices.md</em>
                </span>
                <span className="ra-cell">
                  ③ พื้นผิวตอนปิด
                  <em>{pipeline.source} § 7.2</em>
                </span>
                <span className="ra-cell">
                  ④ ทำไปแล้ว / ค้าง
                  <em>{data.source} · git log</em>
                </span>
              </div>
              {data.roles.map((r) => (
                <RoleRow
                  key={r.role}
                  row={r}
                  stations={
                    pipeline.stations.present
                      ? (pipeline.stations.per_role[r.role] ?? [])
                      : null
                  }
                  cards={cards[r.role] ?? 0}
                  signature={
                    pipeline.signatures.present
                      ? (signatures.get(r.role) ?? null)
                      : null
                  }
                  enforced={pipeline.signatures.enforcement.roles.includes(r.role)}
                  mechanism={pipeline.signatures.enforcement.mechanism}
                />
              ))}
            </div>
            <Belt pipeline={pipeline} head={workspace.head} />
            <Provenance data={data} />
          </>
        )}
      </div>
    </div>
  );
}

function RoleRow({
  row,
  stations,
  cards,
  signature,
  enforced,
  mechanism,
}: {
  row: RoleActivityRow;
  /** `null` when the stage table could not be read — a blank column, not a
   *  blank panel (ADR-0041 §SD7). `[]` means the role holds no station. */
  stations: string[] | null;
  cards: number;
  signature: PipelineSignature | null;
  enforced: boolean;
  mechanism: string;
}) {
  const todo = row.open.todo ?? 0;
  const rest = (['running', 'next', 'owner'] as const)
    .map((k) => ({ k, n: row.open[k] ?? 0 }))
    .filter((x) => x.n > 0);
  const label: Record<string, string> = {
    running: 'กำลังทำ',
    next: 'ถัดไป',
    owner: 'รอคนเคาะ',
  };
  return (
    <div className={`ra-row${row.silent ? ' silent' : ''}`} role="row">
      <span className="ra-role">
        <b>{row.role}</b>
        {row.office && <em>· {row.office}</em>}
        {row.silent && <span className="ra-gap">ช่องโหว่ — 0 / 0</span>}
      </span>

      {/* ① */}
      <span className="ra-cell" data-label="① สถานีบนสายพาน">
        {stations === null ? (
          <span className="ra-dark">อ่านตารางสถานีไม่ได้</span>
        ) : stations.length > 0 ? (
          <>
            <span className="ra-yes">✓</span>{' '}
            {stations.map((s) => (
              <code key={s}>{s}</code>
            ))}
          </>
        ) : (
          <>
            <span className="ra-half">◐</span> ไม่มีสถานีของตัวเอง
            {/* Printed, not hidden: ◐ is not ✗, and the reason is two columns
                to the right on the same screen (ADR-0041 §SD4). */}
            <em>ยืนบนสายพานผ่านลายเซ็นของ § 7.2 ไม่ใช่ผ่าน discipline ที่ถือ</em>
          </>
        )}
      </span>

      {/* ② */}
      <span className="ra-cell" data-label="② การ์ดบนบอร์ด">
        {cards > 0 ? (
          <>
            <span className="ra-yes">✓</span> <b>{cards}</b> แถว
          </>
        ) : (
          <>
            <span className="ra-no">✗</span> ไม่มีแถวเลย
          </>
        )}
      </span>

      {/* ③ */}
      <span className="ra-cell" data-label="③ พื้นผิวตอนปิด">
        {signature === null ? (
          <span className="ra-dark">อ่านตารางลายเซ็นไม่ได้</span>
        ) : signature.targets.length > 0 ? (
          <>
            {signature.targets.map((t) => (
              <span className="ra-target" key={t.token}>
                <code>{t.token}</code>{' '}
                <b className={t.have === 0 ? 'warn' : ''}>
                  {t.have}
                  {t.total !== null ? `/${t.total} โปรเจกต์` : ' ไฟล์'}
                </b>
              </span>
            ))}
            {signature.note && <em>{signature.note}</em>}
          </>
        ) : (
          <>
            <span className="ra-half">◐</span> ไม่ใช่ไฟล์ —{' '}
            {enforced ? (
              <>
                <b>มีเครื่องบังคับ</b> <code>{mechanism}</code>
              </>
            ) : (
              <b className="warn">ข้อความล้วน</b>
            )}
            {signature.note && <em>{signature.note}</em>}
          </>
        )}
        {signature && signature.rejected.length > 0 && (
          <em className="warn">
            เป้าที่ปฏิเสธ (ออกนอก workspace):{' '}
            {signature.rejected.map((t) => (
              <code key={t}>{t}</code>
            ))}
          </em>
        )}
      </span>

      {/* ④ — ADR-0039, unchanged in meaning: commits, not pieces of work. */}
      <span className="ra-cell ra-num" data-label="④ ทำไปแล้ว / ค้าง">
        <b>{row.commits}</b> ใบ
        {row.legacy > 0 && (
          <em>
            {row.direct} ตรง · {row.legacy} แปลง
          </em>
        )}
        <span className="ra-open">
          ค้าง <b>{todo}</b>
          {rest.length > 0 && (
            <em>{rest.map((x) => `+${x.n} ${label[x.k]}`).join(' · ')}</em>
          )}
        </span>
      </span>
    </div>
  );
}

/** The belt itself, and what the two SOP-fed columns are standing on.
 *
 *  Printed under the table rather than folded into it because a station nobody
 *  holds has no role row to appear in — and `close` is exactly that station
 *  (ADR-0041 §SD4). Dropping it here would delete the newest part of the belt
 *  from the one screen that claims to draw the belt. */
function Belt({
  pipeline,
  head,
}: {
  pipeline: WorkspaceResponse['pipeline'];
  head: string;
}) {
  const enforcement = pipeline.signatures.enforcement;
  return (
    <div className="ra-belt-note">
      <h4>สายพานที่อ่านมา</h4>
      {pipeline.stations.present ? (
        <p className="ra-stages">
          {pipeline.stations.stages.map((s, i) => (
            <span key={s.stage}>
              {i > 0 && ' → '}
              <code className={s.roles.length === 0 ? 'unowned' : ''}>
                {s.stage}
              </code>
              {s.roles.length === 0 && <i> (ด่าน — ไม่มี role ถือ)</i>}
            </span>
          ))}
        </p>
      ) : (
        <p className="warn">{pipeline.stations.reason}</p>
      )}
      <ul>
        <li>
          ที่มา <code>{pipeline.source}</code> · อ่านจาก <code>HEAD</code>{' '}
          <code>{head ? head.slice(0, 7) : 'ไม่ใช่ git'}</code> ⇒{' '}
          <b>ตารางนี้ขยับเองเมื่อ SOP ขยับ</b> ไม่ต้องมีใครมาวาดใหม่
        </li>
        <li>
          ③ นับ <b>ทุกโฟลเดอร์ใน <code>projects/</code> ({pipeline.signatures.projects})
          </b>{' '}
          ไม่ใช่แค่โปรเจกต์ที่มีการ์ดบนบอร์ด — ด่านปิดรอบเป็นของทุกโปรเจกต์ (§ 7.1)
        </li>
        {enforcement.declared ? (
          <li>
            <b className={enforcement.unenforced > 0 ? 'warn' : ''}>
              {enforcement.unenforced} ใน {enforcement.total}
            </b>{' '}
            บรรทัดของ § 7.2 <b>ไม่มีเครื่องบังคับ</b> — ที่มีคือ{' '}
            {enforcement.roles.map((r, i) => (
              <span key={r}>
                {i > 0 && ' · '}
                <code>{r}</code>
              </span>
            ))}{' '}
            ผ่าน <code>{enforcement.mechanism}</code>
            {!enforcement.mechanism_exists && (
              <b className="warn"> — ประกาศไว้แต่หาไฟล์ไม่เจอในทรี</b>
            )}
          </li>
        ) : (
          <li className="warn">
            § 7.2 ไม่ได้ประกาศว่าบรรทัดไหนมีเครื่องบังคับ — คอลัมน์ ③
            จึงไม่บอกว่าใครบังคับ และบอร์ดไม่เดาแทน
          </li>
        )}
        <li>
          ③ นับ <b>การมีอยู่ของไฟล์</b> ไม่ได้นับความจริงของเนื้อใน · § 7.3
          ยังให้ใบชื่ออื่นตอบแทนได้ ⇒ เลขนี้เป็น <b>เพดานล่าง</b> ของความพร้อม
        </li>
      </ul>
    </div>
  );
}

/** Why the numbers in column ④ read the way they do. Printed, not hidden behind
 *  a tooltip: without it "no role shipped much" and "most commits carried no id"
 *  look identical on screen, and they are not the same finding (ADR-0039 §SD5). */
function Provenance({
  data,
}: {
  data: Extract<RoleActivityResponse, { present: true }>;
}) {
  const uncounted = data.totals.commits - data.totals.with_assignment;
  const legacy = data.roles.reduce((n, r) => n + r.legacy, 0);
  const slugs = new Set<string>();
  data.roles.forEach((r) => Object.keys(r.legacy_slugs).forEach((s) => slugs.add(s)));
  const unassigned = Object.values(data.open_unassigned).reduce((a, b) => a + b, 0);

  return (
    <div className="ra-prov">
      <h4>อ่านคอลัมน์ ④ ยังไง</h4>
      <ul>
        <li>
          นับ <b>commit</b> ไม่ใช่นับงาน — PR ที่ squash เหลือใบเดียว ส่วนใบที่ merge
          ทั้งสายให้เลขเท่าจำนวน commit ในสายนั้น
        </li>
        <li>
          <b>{data.totals.commits}</b> commit ในช่วงนี้ · มี <code>Assignment:</code>{' '}
          <b>{data.totals.with_assignment}</b> ใบ ⇒{' '}
          <b className={uncounted > 0 ? 'warn' : ''}>{uncounted}</b>{' '}
          ใบไม่ถูกนับให้ role ไหนเลย
        </li>
        <li>
          repo ที่อ่าน:{' '}
          {data.repos.map((r, i) => (
            <span key={r.path}>
              {i > 0 && ' · '}
              <code>{r.path}</code> {r.with_assignment}/{r.commits}
            </span>
          ))}
          {data.repos.length === 0 && '— ไม่มี (ไม่ใช่ git checkout)'}
        </li>
        {legacy > 0 && (
          <li>
            <b>{legacy}</b> ใบเป็น id รูปเก่าที่เขียน discipline ไว้ในช่องที่ 3 (
            {[...slugs].sort().map((s, i) => (
              <span key={s}>
                {i > 0 && ' · '}
                <code>{s}</code>
              </span>
            ))}
            ) — <b>แปลงเป็น role ด้วยตารางของวันนี้</b> ไม่ใช่สิ่งที่ commit เขียนไว้เอง
          </li>
        )}
        {data.unresolved.count > 0 && (
          <li className="warn">
            <b>{data.unresolved.count}</b> ใบมี <code>Assignment:</code> ที่อ่านไม่ออก —
            เช่น{' '}
            {data.unresolved.samples.map((s, i) => (
              <span key={s.sha + i}>
                {i > 0 && ' · '}
                <code>{s.sha}</code> <code>{s.assignment}</code>
              </span>
            ))}
          </li>
        )}
        {unassigned > 0 && (
          <li>
            <b>{unassigned}</b> แถวที่ยังไม่จบ resolve role ไม่ได้ — ไม่ถูกโยนเข้า role ไหน
          </li>
        )}
        <li>
          <code>.githooks/commit-msg</code> ครอบเฉพาะ repo ของ workspace — ตัวเลขจาก repo
          โค้ดของโปรเจกต์ดีเท่าที่คนเขียน commit เขียนไว้
        </li>
      </ul>
    </div>
  );
}
