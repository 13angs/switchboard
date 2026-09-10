import { useState, useEffect } from 'react';
import {
  fetchRoleActivity,
  type RoleActivityResponse,
  type WorkspaceProject,
  type WorkspaceResponse,
} from '../lib/api';
import {
  projectGaps,
  projectRoleGaps,
  type ProjectGaps,
  type RoleGapRow,
  type RoleSurface,
} from '../lib/project-gaps';

/** Window the scoped commit counts are read over. One value, not a picker: this
 *  panel is about *what is missing*, and a window control here would invite
 *  reading it as the participation panel it deliberately is not (ADR-0039 is
 *  still one button away, on the board's own bar). */
const WINDOW_DAYS = 30;

/**
 * What one project is still missing — **by role** (ADR-0042).
 *
 * ADR-0040 opened this window with two lists side by side: the files team-os
 * declares a project should carry, and the roles with no row here. They never
 * met, so `rollout.md` appeared once as a missing file and again, two sections
 * down, as the surface `devops` has nowhere to sign. §SD1 collapses them onto
 * one key — the role — by joining the two registers on the *file location*
 * they both name, so every file appears exactly once, under whoever signs it.
 *
 * What the join cannot claim, it does not: a slot no §7.2 line mentions gets
 * its own section rather than the nearest-looking role (§SD2), and a target the
 * SOP measures across the whole workspace is ◐ here rather than ✓ or ✗ (§SD3).
 *
 * Every row with a gap carries a button that opens a session **of that role**
 * at its own tier — one press, one role, one project (§SD4).
 */
export function ProjectGapsDialog({
  project,
  data,
  onClose,
  onFill,
  onDispatchRole,
}: {
  project: WorkspaceProject;
  data: WorkspaceResponse;
  onClose: () => void;
  onFill: (gaps: ProjectGaps) => void;
  onDispatchRole: (row: RoleGapRow) => void;
}) {
  const gaps = projectRoleGaps(project, data);
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

        <p className="gaps-why">
          ทุกแถวคือ <b>role</b> · ไฟล์อยู่ใต้ role ที่เซ็นมันตอนปิดรอบ (
          <code>{data.pipeline.source}</code> § 7.2) — ไฟล์หนึ่งใบขึ้นที่เดียว
        </p>

        {gaps.rolesUnknown ? (
          <p className="gaps-warn">
            ⚠️ อ่านแผนที่ role → model ไม่ได้ ({data.dispatch.present ? '' : data.dispatch.reason}) ⇒
            บอกไม่ได้ว่า role ไหนขาด และปักหมุด tier ให้ session ไม่ได้ —
            แผงนี้เงียบโดยตั้งใจ ดีกว่ารายงานว่า &laquo;ไม่ขาด&raquo;
          </p>
        ) : (
          <>
            <div className="gaps-table" role="table">
              <div className="gaps-row gaps-head" role="row">
                <span className="g-role">role</span>
                <span className="g-cell">แถวในโปรเจกต์นี้</span>
                <span className="g-cell">พื้นผิวตอนปิดของ role นี้</span>
                <span className="g-act" />
              </div>
              {gaps.roles.map((row) => (
                <Row
                  key={row.slug}
                  row={row}
                  project={project}
                  commits={
                    activity && activity.present ? (commits[row.slug] ?? 0) : null
                  }
                  onDispatch={() => onDispatchRole(row)}
                />
              ))}
            </div>

            <Ownerless gaps={gaps} source={data.pipeline.source} />
          </>
        )}

        {!gaps.signatures.present && (
          <p className="gaps-warn">
            ⚠️ อ่านตารางเจ็ดลายเซ็นไม่ได้ ({gaps.signatures.reason}) ⇒ ทุกช่องตกไปอยู่
            <b> ไม่มี role ถือ</b> ข้างล่าง · คอลัมน์ <b>แถวในโปรเจกต์นี้</b> กับปุ่มยังใช้ได้
            เพราะไม่ได้พึ่ง SOP ใบนั้น
          </p>
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
        <p className="gaps-note">
          {!activity && !activityError && 'กำลังอ่าน git log…'}
          {activityError && `อ่าน commit ของโปรเจกต์นี้ไม่ได้: ${activityError}`}
          {activity && activity.present && (
            <>
              commit นับจาก <code>projects/{project.name}/</code> และ repo โค้ดของโปรเจกต์นี้
              ตั้งแต่ <code>{activity.window.since}</code> — <code>Assignment:</code>{' '}
              ไม่มีช่องโปรเจกต์ จึงนับจาก path · commit ที่แตะสองโปรเจกต์ถูกนับให้ทั้งคู่
            </>
          )}
        </p>

        <div className="dlg-foot">
          <p className="gaps-why">
            ปุ่มบนแถว<b>เปิด session ของ role นั้นเอง</b> — หนึ่งกด หนึ่ง role หนึ่งโปรเจกต์ ·
            ปุ่มข้างล่างเป็นคนละคำถาม: <b>ช่องพวกนี้ควรกลายเป็นแถวไหม</b> ⇒ กริล{' '}
            <code>forge</code> ที่แก้เฉพาะ <code>slices.md</code>
          </p>
          <button
            className="gaps-fill"
            onClick={() => onFill(projectGaps(project, data))}
          >
            ส่งช่องที่ขาดเข้ากริล
          </button>
        </div>
      </div>
    </div>
  );
}

function Row({
  row,
  project,
  commits,
  onDispatch,
}: {
  row: RoleGapRow;
  project: WorkspaceProject;
  /** `null` while `git log` is still being read, or when it could not be. */
  commits: number | null;
  onDispatch: () => void;
}) {
  return (
    <div className={`gaps-row${row.gap ? ' gap' : ''}`} role="row">
      <span className="g-role">
        <b>{row.role}</b>
        <em>{row.slug}</em>
      </span>

      <span className="g-cell" data-label="แถวในโปรเจกต์นี้">
        {row.rows > 0 ? (
          <>
            <span className="mark">✅</span> <b>{row.rows}</b> แถว
          </>
        ) : (
          <>
            <span className="mark">⬜</span> ไม่มีแถวเลย
          </>
        )}
        {commits !== null && <em>{commits} commit / 30 วัน</em>}
      </span>

      <span className="g-cell" data-label="พื้นผิวตอนปิดของ role นี้">
        {row.surfaces.length === 0 ? (
          <span className="g-half">
            ◐ ไม่ใช่ไฟล์ในโปรเจกต์
            {row.note && <em>{row.note}</em>}
            {!row.note && !row.closes && (
              <em>ตารางลายเซ็นไม่มีบรรทัดของ role นี้</em>
            )}
          </span>
        ) : (
          row.surfaces.map((s) => (
            <Surface key={s.token} surface={s} project={project.name} />
          ))
        )}
        {row.surfaces.length > 0 && row.note && <em>{row.note}</em>}
      </span>

      <span className="g-act">
        {row.gap && row.known && (
          <button className="g-go" onClick={onDispatch}>
            สั่ง {row.role}
          </button>
        )}
        {row.gap && !row.known && (
          <em className="g-nogo">ไม่อยู่ใน roles.md ⇒ ปักหมุด tier ไม่ได้</em>
        )}
      </span>
    </div>
  );
}

/** One §7.2 target as this project sees it. The join key is printed next to it
 *  on purpose (ADR-0042 §SD1 · `risks.md S-01` surface 7): the day either
 *  register re-words a location, the file quietly loses its owner and lands in
 *  the section below, and this is the only thing on screen that says why. */
function Surface({ surface, project }: { surface: RoleSurface; project: string }) {
  if (surface.level === 'workspace') {
    return (
      <span className="g-surface">
        <span className="g-half">◐</span> <code>{surface.token}</code>
        <em>
          เป้าระดับ workspace ({surface.have} ไฟล์) — วัดต่อโปรเจกต์ไม่ได้
        </em>
      </span>
    );
  }
  if (surface.level === 'unknown') {
    return (
      <span className="g-surface">
        <span className="g-half">◐</span> <code>{surface.token}</code>
        <em className="warn">
          ประกาศไว้แต่บอร์ดไม่รู้ว่าดูที่ไหนในระดับโปรเจกต์ · ทั้ง workspace มี{' '}
          {surface.have}
          {surface.total !== null ? `/${surface.total}` : ''}
        </em>
      </span>
    );
  }
  return (
    <span className="g-surface">
      <span className="mark">{surface.present ? '✅' : '⬜'}</span>{' '}
      <code>
        projects/{project}/{surface.token}
      </code>
      <em>join: {surface.joinKey}</em>
    </span>
  );
}

/** Slots no §7.2 line claims (ADR-0042 §SD2). Shown, never folded into the
 *  nearest role: the board may report that nobody signs for them, but deciding
 *  who *should* is the SOP's call, not this screen's. */
function Ownerless({
  gaps,
  source,
}: {
  gaps: ReturnType<typeof projectRoleGaps>;
  source: string;
}) {
  if (gaps.ownerless.length === 0) return null;
  return (
    <section className="gaps-axis gaps-orphan">
      <h4>
        ช่องที่ <em>ไม่มี role ถือ</em>
      </h4>
      <ul className="gaps-list">
        {gaps.ownerless.map((s) => (
          <li key={s.key} className={s.present ? 'ok' : 'missing'}>
            <span className="mark">{s.present ? '✅' : '⬜'}</span>
            <code>{s.key}</code>
            <em>{s.where}</em>
          </li>
        ))}
      </ul>
      <p className="gaps-note">
        <code>{source}</code> § 7.2 ไม่มีบรรทัดของช่องพวกนี้ ⇒{' '}
        <b>ด่านปิดรอบไม่มีใครเซ็นให้</b> · บอร์ดไม่เดาเจ้าของแทน SOP — จะยกให้ role ไหน
        เป็นการตัดสินของ SOP ไม่ใช่ของหน้าจอนี้ ⇒ สองแถวนี้จึงไม่มีปุ่ม
      </p>
    </section>
  );
}
