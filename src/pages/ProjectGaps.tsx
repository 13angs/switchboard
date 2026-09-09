import { useState, useEffect } from 'react';
import {
  fetchRoleActivity,
  type RoleActivityResponse,
  type WorkspaceProject,
  type WorkspaceResponse,
} from '../lib/api';
import { projectGaps, type ProjectGaps } from '../lib/project-gaps';

/** Window the scoped commit counts are read over. One value, not a picker: this
 *  panel is about *what is missing*, and a window control here would invite
 *  reading it as the participation panel it deliberately is not (ADR-0039 is
 *  still one button away, on the board's own bar). */
const WINDOW_DAYS = 30;

/**
 * What one project is still missing, measured against what team-os says a
 * project should have — and one button that hands the list to a grill
 * (ADR-0040).
 *
 * The gap analysis itself is synchronous and local: it comes out of the
 * `/workspace` payload the board already holds, through `projectGaps()`, which
 * is the same function the card's badge counts with. Only the commit column is
 * fetched, because a browser cannot read `git log`.
 */
export function ProjectGapsDialog({
  project,
  data,
  onClose,
  onFill,
}: {
  project: WorkspaceProject;
  data: WorkspaceResponse;
  onClose: () => void;
  onFill: (gaps: ProjectGaps) => void;
}) {
  const gaps = projectGaps(project, data);
  const [activity, setActivity] = useState<RoleActivityResponse | null>(null);
  const [activityError, setActivityError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchRoleActivity(WINDOW_DAYS, project.name)
      .then((r) => live && setActivity(r))
      .catch((e) => live && setActivityError(e instanceof Error ? e.message : 'อ่านไม่ได้'));
    return () => {
      live = false;
    };
  }, [project.name]);

  const commits: Record<string, number> = {};
  if (activity && activity.present) {
    for (const row of activity.roles) commits[row.role] = row.commits;
  }

  return (
    <div className="dlg-backdrop" onClick={onClose}>
      <div
        className="dlg gaps"
        role="dialog"
        aria-modal="true"
        aria-label={`ช่องที่ขาดของ ${project.name}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dlg-head">
          <h3>
            ช่องที่ขาด — <code>{project.name}</code>
          </h3>
          <button className="dlg-x" onClick={onClose} aria-label="ปิด">
            ✕
          </button>
        </div>

        <Slots project={project} data={data} gaps={gaps} />
        <Roles
          project={project}
          gaps={gaps}
          commits={commits}
          loading={!activity && !activityError}
          error={activityError}
          window={activity && activity.present ? activity.window.since : null}
        />

        <div className="dlg-foot">
          <p className="gaps-why">
            ปุ่มนี้<b>ไม่เติมแถวให้เอง</b> — เปิด session <code>forge</code> ในแท็บใหม่
            พร้อมรายการข้างบน · ช่องที่ควรว่างต่อไปให้เขียนไว้ว่าทำไม ไม่ใช่แปลงเป็นแถวทุกช่อง
          </p>
          <button className="gaps-fill" onClick={() => onFill(gaps)}>
            ส่งช่องที่ขาดเข้ากริล
          </button>
        </div>
      </div>
    </div>
  );
}

/** Axis one: the files team-os declares a project should carry. */
function Slots({
  project,
  data,
  gaps,
}: {
  project: WorkspaceProject;
  data: WorkspaceResponse;
  gaps: ProjectGaps;
}) {
  const slotWhere = new Map(data.slots.slots.map((s) => [s.key, s.where]));
  return (
    <section className="gaps-axis">
      <h4>
        เอกสาร/ระยะ <em>· team-os/projects/README.md</em>
      </h4>
      <ul className="gaps-list">
        {Object.entries(project.has).map(([key, present]) => (
          <li key={key} className={present ? 'ok' : 'missing'}>
            <span className="mark">{present ? '✅' : '⬜'}</span>
            <code>{key}</code>
            <em>{slotWhere.get(key) ?? `${key}.md`}</em>
          </li>
        ))}
      </ul>
      {gaps.missingSlots.length === 0 && (
        <p className="gaps-none">ครบทุกช่องที่ประกาศไว้</p>
      )}
      {data.slots.source === 'fallback' && (
        <p className="gaps-warn">
          ⚠️ อ่านรายการช่องจาก <code>team-os/projects/README.md</code> ไม่ได้ —
          กำลังใช้สามช่องเดิมแทน ({data.slots.reason}) · รายการนี้จึงอาจไม่ครบ
        </p>
      )}
      {data.slots.unmapped.length > 0 && (
        <p className="gaps-note">
          ช่องที่ต้นแบบประกาศแต่บอร์ดไม่ตรวจ (ไม่ได้ชี้ไปที่ไฟล์ของโปรเจกต์):{' '}
          {data.slots.unmapped.join(' · ')}
        </p>
      )}
    </section>
  );
}

/** Axis two: the 7 roles, and which of them this project has never had a row for. */
function Roles({
  project,
  gaps,
  commits,
  loading,
  error,
  window: since,
}: {
  project: WorkspaceProject;
  gaps: ProjectGaps;
  commits: Record<string, number>;
  loading: boolean;
  error: string | null;
  window: string | null;
}) {
  if (gaps.rolesUnknown) {
    return (
      <section className="gaps-axis">
        <h4>role</h4>
        <p className="gaps-warn">
          ⚠️ อ่านแผนที่ role → model ไม่ได้ ⇒ บอกไม่ได้ว่า role ไหนไม่มีแถว —
          แกนนี้เงียบโดยตั้งใจ ดีกว่ารายงานว่า &laquo;ไม่ขาด&raquo;
        </p>
      </section>
    );
  }
  const rows = Object.keys(gaps.rowsPerRole)
    .concat(gaps.rolesWithoutRows)
    .filter((r, i, a) => a.indexOf(r) === i);
  return (
    <section className="gaps-axis">
      <h4>
        role <em>· team-os/people/roles.md · นับทุกคอลัมน์ รวม ✅</em>
      </h4>
      <ul className="gaps-list">
        {rows.map((role) => {
          const n = gaps.rowsPerRole[role] ?? 0;
          return (
            <li key={role} className={n > 0 ? 'ok' : 'missing'}>
              <span className="mark">{n > 0 ? '✅' : '⬜'}</span>
              <code>{role}</code>
              <em>
                {n > 0 ? `${n} แถว` : 'ไม่มีแถวเลย'}
                {!loading && !error && ` · ${commits[roleSlug(role)] ?? 0} commit`}
              </em>
            </li>
          );
        })}
      </ul>
      <p className="gaps-note">
        {loading && 'กำลังอ่าน git log…'}
        {error && `อ่าน commit ของโปรเจกต์นี้ไม่ได้: ${error}`}
        {!loading && !error && (
          <>
            commit นับจาก <code>projects/{project.name}/</code> และ repo โค้ดของโปรเจกต์นี้
            ตั้งแต่ <code>{since}</code> — <code>Assignment:</code> ไม่มีช่องโปรเจกต์
            จึงนับจาก path · commit ที่แตะสองโปรเจกต์ถูกนับให้ทั้งคู่
          </>
        )}
      </p>
    </section>
  );
}

/** `Tech Lead` → `tech-lead`. The board shows the display name from
 *  `§ โมเดลต่อ role`; `/roles/activity` keys on the slug that appears in an
 *  `Assignment:` id. Mirrors `workspace._slug()`. */
function roleSlug(role: string): string {
  return role.trim().toLowerCase().replace(/[\s_]+/g, '-');
}
