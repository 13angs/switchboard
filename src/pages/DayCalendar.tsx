import { useCallback, useEffect, useState } from 'react';
import {
  fetchCalendar,
  type CalendarResponse,
  type DaySchedule,
  type Ritual,
  type ScheduleBlock,
  type WorkspaceDispatch,
} from '../lib/api';
import { composeRitualPrompt, dispatchLabel } from '../lib/dispatch-prompt';
import { DispatchDialog } from './DispatchDialog';

const DAY_MINUTES = 24 * 60;

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

/** Known domains get a stable color + a legend label; anything else falls
 *  into `other` rather than a guessed mapping — the schedule's domain column
 *  is free-text Thai prose (workspace.py's own rule for the same reason). */
const DOMAIN_LEGEND: Record<string, string> = {
  work: 'work',
  personal: 'personal',
  learning: 'learning',
  other: 'อื่น ๆ / ไม่ระบุ',
};

function domainClass(domain: string): string {
  const key = domain.trim().toLowerCase();
  return key in DOMAIN_LEGEND && key !== 'other' ? key : 'other';
}

/** A bar the operator has pressed, carried with the day it was pressed on —
 *  the prompt states the date and the slot, and the same key appears on three
 *  bars a day, so the block alone would not identify which one. */
interface PickedRitual {
  ritual: Ritual;
  date: string;
  start: string;
  end: string;
}

/** Reads `meta/daily/<date>.md § ⏱️ ตารางเวลา` and draws one strip per day —
 *  slices.md S9. The strip alone is a color-only glance (color needs a
 *  legend and a hover to mean anything, which doesn't work on a touch
 *  device) — so the day expands into a plain-text agenda list underneath,
 *  which is what actually answers "is this slot bookable, and by what".
 *
 *  Since ADR-0036 the agenda row is also where a bar is *pressed*: a row whose
 *  label names a ritual declared in `rituals.md` carries a dispatch button, on
 *  the row rather than on the colored strip for the same reason the agenda
 *  exists at all — the strip is 3px of color on a tablet. */
export function DayCalendar({ dispatch }: { dispatch: WorkspaceDispatch | null }) {
  const [data, setData] = useState<CalendarResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<PickedRitual | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchCalendar();
      setData(res);
      setExpanded((prev) => (prev.size === 0 ? new Set([res.center]) : prev));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load calendar');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const onFocus = () => load();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [load]);

  const toggle = (date: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(date)) next.delete(date);
      else next.add(date);
      return next;
    });
  };

  if (loading && !data) {
    return <div className="calendar-loading">กำลังอ่านตารางเวลา…</div>;
  }
  if (error) {
    return <div className="calendar-error">อ่านปฏิทินไม่ได้: {error}</div>;
  }
  if (!data) return null;

  return (
    <section className="calendar" aria-label="ปฏิทินภาระงานรายวัน">
      <div className="calendar-head">
        <h2>ปฏิทินภาระงานรายวัน</h2>
        <span className="calendar-hint">
          แถบสี = ช่องที่จองแล้ว ไม่ใช่ช่องว่าง — กดวันที่เพื่อดูว่าจองไว้ทำอะไร ก่อนสั่งงานทับ
        </span>
      </div>
      {!data.rituals.present && (
        <p className="cal-ritual-off">
          แถวจังหวะกดสั่งงานไม่ได้ — {data.rituals.reason || `อ่าน ${data.rituals.source} ไม่ได้`}
        </p>
      )}
      <div className="calendar-legend">
        {Object.entries(DOMAIN_LEGEND).map(([key, label]) => (
          <span className="legend-item" key={key}>
            <i className={`legend-dot cal-${key}`} />
            {label}
          </span>
        ))}
      </div>
      <div className="calendar-scale" aria-hidden="true">
        <span>00:00</span>
        <span>06:00</span>
        <span>12:00</span>
        <span>18:00</span>
        <span>24:00</span>
      </div>
      {data.days.map((day) => (
        <DayBlock
          key={day.date}
          day={day}
          today={day.date === data.center}
          open={expanded.has(day.date)}
          onToggle={() => toggle(day.date)}
          // No workspace payload means no role→tier map to pin a session with,
          // so the bar carries no button rather than one that does nothing.
          onDispatch={dispatch ? setPicked : null}
        />
      ))}

      {picked && dispatch && (
        <DispatchDialog
          action={dispatchLabel('act')}
          subject={picked.ritual.key}
          dispatch={dispatch}
          preferredRole={picked.ritual.role}
          fixedRole={
            picked.ritual.role
              ? {
                  role: picked.ritual.role,
                  why: `role มาจากแถวของจังหวะใน rituals.md — เปลี่ยนที่นี่ไม่ได้ เพราะ Assignment ของจังหวะนี้ประกาศ ${picked.ritual.role} ไว้แล้ว`,
                }
              : null
          }
          notice={
            <p className="dlg-ritual">
              จังหวะ <b>{picked.ritual.name}</b> ของ {picked.date} ·{' '}
              {picked.start}–{picked.end} · id{' '}
              <code>{picked.ritual.assignment}</code>{' '}
              <b>ไม่มีวันที่ต่อท้าย</b> — มันระบุจังหวะ ไม่ใช่รอบของวันนี้
            </p>
          }
          compose={(role) =>
            composeRitualPrompt(picked.ritual, role, {
              date: picked.date,
              start: picked.start,
              end: picked.end,
            })
          }
          onClose={() => setPicked(null)}
        />
      )}
    </section>
  );
}

function DayBlock({
  day,
  today,
  open,
  onToggle,
  onDispatch,
}: {
  day: DaySchedule;
  today: boolean;
  open: boolean;
  onToggle: () => void;
  onDispatch: ((picked: PickedRitual) => void) | null;
}) {
  return (
    <div className={`cal-day${today ? ' cal-today' : ''}`}>
      <button
        type="button"
        className="cal-row"
        onClick={onToggle}
        aria-expanded={open}
        title="กดเพื่อดูรายการงานที่จองไว้วันนี้"
      >
        <span className="cal-date">{day.date}</span>
        <div
          className="cal-bar"
          role="img"
          aria-label={
            day.blocks.length > 0
              ? `${day.date}: ${day.blocks.map((b) => `${b.start}–${b.end} ${b.label}`).join(', ')}`
              : `${day.date}: ไม่มีช่องที่จองไว้`
          }
        >
          {!day.present && <span className="cal-empty">ไม่มีไฟล์วัน</span>}
          {day.present && day.blocks.length === 0 && (
            <span className="cal-empty">ไม่มีหัวข้อ § ⏱️ ตารางเวลา</span>
          )}
          {day.blocks.map((b, i) => {
            const startM = toMinutes(b.start);
            const endM = toMinutes(b.end);
            if (endM <= startM) return null; // overnight range — not drawable on a single-day bar
            const left = (startM / DAY_MINUTES) * 100;
            const width = Math.max(((endM - startM) / DAY_MINUTES) * 100, 0.3);
            return (
              <span
                key={`${b.start}-${i}`}
                className={`cal-block cal-${domainClass(b.domain)}${b.ritual ? ' cal-ritual' : ''}`}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            );
          })}
        </div>
        <span className="cal-caret">{open ? '▾' : '▸'}</span>
      </button>
      {open && <Agenda day={day} onDispatch={onDispatch} />}
    </div>
  );
}

function Agenda({
  day,
  onDispatch,
}: {
  day: DaySchedule;
  onDispatch: ((picked: PickedRitual) => void) | null;
}) {
  if (!day.present) {
    return <p className="cal-agenda-empty">ไม่มีไฟล์วันนี้ — จองได้เต็มวัน</p>;
  }
  if (day.blocks.length === 0) {
    return (
      <p className="cal-agenda-empty">
        มีไฟล์วันนี้ แต่ไม่มีหัวข้อ § ⏱️ ตารางเวลา — จองได้เต็มวันตามที่รู้จากบอร์ดนี้
      </p>
    );
  }
  return (
    <>
      <ul className="cal-agenda">
        {day.blocks.map((b, i) => (
          <AgendaRow
            key={`${b.start}-${i}`}
            block={b}
            onDispatch={
              onDispatch && b.ritual
                ? () =>
                    onDispatch({
                      ritual: b.ritual as Ritual,
                      date: day.date,
                      start: b.start,
                      end: b.end,
                    })
                : null
            }
          />
        ))}
      </ul>
      <Unmapped rituals={day.unmapped} />
    </>
  );
}

/** ADR-0036 §SD6 — a ritual that declares a key but matched nothing in this
 *  day's plan. Without this the failure is silent in exactly the shape
 *  risks.md S-08 describes: the button simply is not there, and nobody can
 *  tell that from a day the ritual was never scheduled on. Reads the plan
 *  only — it never claims the ritual was or was not actually run. */
function Unmapped({ rituals }: { rituals: Ritual[] }) {
  if (rituals.length === 0) return null;
  return (
    <p className="cal-unmapped">
      <b>จังหวะที่ประกาศคีย์ไว้ แต่ไม่มีแถวในแผนของวันนี้:</b>{' '}
      {rituals.map((r) => (
        <span className="cal-unmapped-key" key={r.key}>
          <code>{r.key}</code>
          {!r.dispatchable && (
            <em> (ทะเบียนยังขาด {r.missing.join(' · ')})</em>
          )}
        </span>
      ))}
      <span className="cal-unmapped-why">
        · อ่านจากแผนของวัน ไม่ได้แปลว่าเดินแล้วหรือยัง
      </span>
    </p>
  );
}

function AgendaRow({
  block,
  onDispatch,
}: {
  block: ScheduleBlock;
  onDispatch: (() => void) | null;
}) {
  const domain = block.domain.trim();
  const hasDomain = domain !== '' && domain !== '—';
  const conflict = block.ritual_conflict ?? [];
  return (
    <li className={`cal-agenda-row cal-${domainClass(block.domain)}`}>
      <span className="ar-time">
        {block.start}–{block.end}
      </span>
      <span className="ar-label">
        {block.label}
        {conflict.length > 1 && (
          // Two keys on one label: the board has no way to choose, and choosing
          // would be a guess (ADR-0036 §SD2). It says so instead of going quiet.
          <em className="ar-conflict">
            {' '}
            — 2 จังหวะชนกันบนแถวนี้ ({conflict.join(' · ')}) ⇒ ไม่มีปุ่ม
          </em>
        )}
        {block.ritual && !block.ritual.dispatchable && (
          <em className="ar-conflict">
            {' '}
            — จังหวะ <code>{block.ritual.key}</code> ยังขาด{' '}
            {block.ritual.missing.join(' · ')} ในทะเบียน ⇒ ไม่มีปุ่ม
          </em>
        )}
      </span>
      <span className="ar-meta">
        {hasDomain && <span className="ar-domain">{domain}</span>}
        {block.minutes != null && <span className="ar-minutes">{block.minutes} นาที</span>}
        {onDispatch && block.ritual?.dispatchable && (
          <button className="ar-dispatch" onClick={onDispatch}>
            {dispatchLabel('act')} · {block.ritual.role}
          </button>
        )}
      </span>
    </li>
  );
}
