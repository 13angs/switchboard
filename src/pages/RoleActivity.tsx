import { useState, useEffect, useCallback } from 'react';
import {
  fetchRoleActivity,
  type RoleActivityResponse,
  type RoleActivityRow,
} from '../lib/api';

/** Windows the panel offers, in owner-timezone days (ADR-0039 §SD7). Fixed
 *  presets rather than a free field: the question is "has this role been quiet
 *  lately", and a date picker would invite precision the count does not have. */
const WINDOWS = [7, 14, 30, 90] as const;
const DEFAULT_WINDOW = 14;

/**
 * The one button S26 asks for: all seven roles at once — how many commits each
 * shipped in the window, next to how many rows each still holds.
 *
 * Everything this panel prints is printed as *text*. No `title=`, no hover
 * disclosure: the board is read on a tablet, which is the lesson `S9` paid for
 * once already (ADR-0036 §SD5(ค)). That is why the provenance block at the
 * bottom is long — it is the honest reading of the numbers above it, and it has
 * nowhere else to live.
 */
export function RoleActivityDialog({ onClose }: { onClose: () => void }) {
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

  return (
    <div className="dlg-backdrop" onClick={onClose}>
      <div
        className="dlg ra"
        role="dialog"
        aria-modal="true"
        aria-label="การมีส่วนร่วมต่อ role"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dlg-head">
          <h3>การมีส่วนร่วมต่อ role</h3>
          <button className="dlg-x" onClick={onClose} aria-label="ปิด">
            ✕
          </button>
        </div>

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
            <div className="ra-table" role="table">
              <div className="ra-row ra-head" role="row">
                <span className="ra-role">role</span>
                <span className="ra-num">ทำไปแล้ว</span>
                <span className="ra-num">ค้างอยู่</span>
              </div>
              {data.roles.map((r) => (
                <RoleRow key={r.role} row={r} />
              ))}
            </div>
            <Provenance data={data} />
          </>
        )}
      </div>
    </div>
  );
}

function RoleRow({ row }: { row: RoleActivityRow }) {
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
      <span className="ra-num">
        <b>{row.commits}</b> ใบ
        {row.legacy > 0 && (
          <em>
            {row.direct} ตรง · {row.legacy} แปลง
          </em>
        )}
      </span>
      <span className="ra-num">
        <b>{todo}</b> ใบ
        {rest.length > 0 && (
          <em>{rest.map((x) => `+${x.n} ${label[x.k]}`).join(' · ')}</em>
        )}
      </span>
    </div>
  );
}

/** Why the numbers above read the way they do. Printed, not hidden behind a
 *  tooltip: without it "no role shipped much" and "most commits carried no id"
 *  look identical on screen, and they are not the same finding (§SD5). */
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
      <h4>อ่านตัวเลขนี้ยังไง</h4>
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
