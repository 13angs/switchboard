import { useState, useMemo } from 'react';
import {
  startSession,
  type WorkspaceDispatch,
  type WorkspaceProject,
  type WorkspaceSlice,
} from '../lib/api';
import { composePrompt, type DispatchRole } from '../lib/dispatch-prompt';

interface Props {
  project: WorkspaceProject;
  slice: WorkspaceSlice;
  dispatch: WorkspaceDispatch;
  onClose: () => void;
}

/**
 * Pick a role, read the prompt, send it (ADR-0030 §SD3).
 *
 * The prompt is shown in full and stays editable: it is typed into the session's
 * input box and left there unsent, so what the operator reads here is exactly
 * what they will press Enter on. Nothing starts running from this dialog.
 */
export function DispatchDialog({ project, slice, dispatch, onClose }: Props) {
  const roles = dispatch.present ? dispatch.roles : [];
  // The card opens on the role its own project belongs to (project.default_role,
  // derived from slices.md `team:`), not the first row of a table that has
  // nothing to do with this task — that fell through to CTO/heavy on every
  // card (ADR-0033, closes risks.md S-09). Unrecognised `team:` values still
  // fall back to the first role, same as before this fix.
  const defaultRole = roles.find((r) => r.role === project.default_role);
  const [roleName, setRoleName] = useState((defaultRole ?? roles[0])?.role ?? '');
  const role: DispatchRole | undefined = roles.find((r) => r.role === roleName);

  const composed = useMemo(
    () => (role ? composePrompt(project, slice, role) : ''),
    [project, slice, role],
  );
  const [prompt, setPrompt] = useState<string | null>(null);
  const text = prompt ?? composed;

  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    if (!role) return;
    setSending(true);
    setError(null);
    try {
      const res = await startSession('claude', 'claude', undefined, {
        model: role.model,
        effort: role.effort ?? undefined,
        prompt: text,
      });
      // The session may not have an id yet — it gets one at the first prompt,
      // which has not been sent. The attach page handles the id-less window.
      window.location.href = res.session_id
        ? `/agent?view=terminal&session_id=${encodeURIComponent(res.session_id)}`
        : '/agent?view=terminal&harness=claude';
    } catch (e) {
      setError(e instanceof Error ? e.message : 'สั่งงานไม่สำเร็จ');
      setSending(false);
    }
  }

  return (
    <div className="dlg-backdrop" onClick={onClose}>
      <div
        className="dlg"
        role="dialog"
        aria-modal="true"
        aria-label="สั่งงาน"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dlg-head">
          <h3>
            สั่งงาน · <code>{slice.id !== '—' ? slice.id : project.name}</code>
          </h3>
          <button className="dlg-x" onClick={onClose} aria-label="ปิด">
            ✕
          </button>
        </div>

        {!dispatch.present ? (
          <div className="dlg-blocked">
            <p>
              <b>สั่งงานตาม tier ไม่ได้ตอนนี้</b>
            </p>
            <p className="why">{dispatch.reason}</p>
            <p className="why">
              บอร์ดไม่เดาโมเดลให้ — session ที่ไม่ได้ปักหมุดจะสืบทอดรุ่นของ
              process ที่ launch server ซึ่งไม่ใช่ค่าที่ปลอดภัยกว่า
            </p>
          </div>
        ) : (
          <>
            <div className="dlg-roles">
              {roles.map((r) => (
                <button
                  key={r.role}
                  className={`role${r.role === roleName ? ' on' : ''}`}
                  onClick={() => {
                    setRoleName(r.role);
                    setPrompt(null); // re-compose for the new role
                  }}
                >
                  <span className="rname">{r.role}</span>
                  <span className={`rtier t-${r.tier}`}>{r.tier}</span>
                </button>
              ))}
            </div>

            {role && (
              <p className="dlg-model">
                รันด้วย <code>{role.model}</code>
                {role.effort && (
                  <>
                    {' '}
                    · effort <code>{role.effort}</code>
                  </>
                )}
                <span className="src">
                  · แผนที่มาจาก {dispatch.source.roles} +{' '}
                  {dispatch.source.tiers}
                </span>
              </p>
            )}

            <label className="dlg-label" htmlFor="dispatch-prompt">
              ข้อความที่จะพิมพ์ลงในห้อง — <b>ยังไม่กด Enter ให้</b>
            </label>
            <textarea
              id="dispatch-prompt"
              className="dlg-prompt"
              value={text}
              rows={14}
              onChange={(e) => setPrompt(e.target.value)}
            />

            {error && <div className="dlg-error">{error}</div>}

            <div className="dlg-foot">
              <span className="dlg-note">
                เปิด session ใหม่แล้วพิมพ์ข้อความนี้ค้างไว้ ·{' '}
                <b>คุณเป็นคนกด Enter</b>
              </span>
              <button className="dlg-cancel" onClick={onClose}>
                ยกเลิก
              </button>
              <button
                className="dlg-go"
                onClick={send}
                disabled={sending || !role}
              >
                {sending ? 'กำลังเปิด…' : 'เปิดห้อง + พิมพ์ให้'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
