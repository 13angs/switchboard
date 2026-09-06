import { useCallback, useEffect, useState } from 'react';
import { fetchCalendar, type CalendarResponse, type DaySchedule } from '../lib/api';

const DAY_MINUTES = 24 * 60;

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

/** Known domains get a stable color; anything else falls into `other` rather
 *  than a guessed mapping — the schedule's domain column is free-text Thai
 *  prose (workspace.py's own rule for the same reason). */
const KNOWN_DOMAINS = new Set(['work', 'personal', 'learning']);

function domainClass(domain: string): string {
  const key = domain.trim().toLowerCase();
  return KNOWN_DOMAINS.has(key) ? key : 'other';
}

/** Reads `meta/daily/<date>.md § ⏱️ ตารางเวลา` and draws one bar per day —
 *  slices.md S9. The point is showing which slot is already booked, not just
 *  empty, before a session gets dispatched on top of it. */
export function DayCalendar() {
  const [data, setData] = useState<CalendarResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchCalendar());
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
          แถบสี = ช่องที่จองแล้ว ไม่ใช่ช่องว่าง — เช็คก่อนสั่งงานทับ
        </span>
      </div>
      <div className="calendar-scale" aria-hidden="true">
        <span>00:00</span>
        <span>06:00</span>
        <span>12:00</span>
        <span>18:00</span>
        <span>24:00</span>
      </div>
      {data.days.map((day) => (
        <DayRow key={day.date} day={day} today={day.date === data.center} />
      ))}
    </section>
  );
}

function DayRow({ day, today }: { day: DaySchedule; today: boolean }) {
  return (
    <div className={`cal-row${today ? ' cal-today' : ''}`}>
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
          const domain = b.domain.trim();
          const hasDomain = domain !== '' && domain !== '—';
          return (
            <span
              key={`${b.start}-${i}`}
              className={`cal-block cal-${domainClass(b.domain)}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`${b.start}–${b.end} · ${b.label}${hasDomain ? ` · ${domain}` : ''}`}
            />
          );
        })}
      </div>
    </div>
  );
}
