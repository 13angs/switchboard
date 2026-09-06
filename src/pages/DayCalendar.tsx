import { useCallback, useEffect, useState } from 'react';
import { fetchCalendar, type CalendarResponse, type DaySchedule, type ScheduleBlock } from '../lib/api';

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

/** Reads `meta/daily/<date>.md § ⏱️ ตารางเวลา` and draws one strip per day —
 *  slices.md S9. The strip alone is a color-only glance (color needs a
 *  legend and a hover to mean anything, which doesn't work on a touch
 *  device) — so the day expands into a plain-text agenda list underneath,
 *  which is what actually answers "is this slot bookable, and by what". */
export function DayCalendar() {
  const [data, setData] = useState<CalendarResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

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
        />
      ))}
    </section>
  );
}

function DayBlock({
  day,
  today,
  open,
  onToggle,
}: {
  day: DaySchedule;
  today: boolean;
  open: boolean;
  onToggle: () => void;
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
                className={`cal-block cal-${domainClass(b.domain)}`}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            );
          })}
        </div>
        <span className="cal-caret">{open ? '▾' : '▸'}</span>
      </button>
      {open && <Agenda day={day} />}
    </div>
  );
}

function Agenda({ day }: { day: DaySchedule }) {
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
    <ul className="cal-agenda">
      {day.blocks.map((b, i) => (
        <AgendaRow key={`${b.start}-${i}`} block={b} />
      ))}
    </ul>
  );
}

function AgendaRow({ block }: { block: ScheduleBlock }) {
  const domain = block.domain.trim();
  const hasDomain = domain !== '' && domain !== '—';
  return (
    <li className={`cal-agenda-row cal-${domainClass(block.domain)}`}>
      <span className="ar-time">
        {block.start}–{block.end}
      </span>
      <span className="ar-label">{block.label}</span>
      <span className="ar-meta">
        {hasDomain && <span className="ar-domain">{domain}</span>}
        {block.minutes != null && <span className="ar-minutes">{block.minutes} นาที</span>}
      </span>
    </li>
  );
}
