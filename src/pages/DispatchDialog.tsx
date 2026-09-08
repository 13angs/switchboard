import { useState, useMemo, type ReactNode } from 'react';
import { startSession, type WorkspaceDispatch } from '../lib/api';
import type { DispatchRole } from '../lib/dispatch-prompt';

interface Props {
  /** The word this dialog and its send button wear — `dispatchLabel(shape)`. */
  action: string;
  /** What is being handed out: a slice id, a project, or a ritual key. */
  subject: string;
  dispatch: WorkspaceDispatch;
  /** Which role the picker opens on. */
  preferredRole: string | null;
  /** Set when the row names its own owner and the operator may not swap it —
   *  a ritual's id embeds its role and office, so a different role would run
   *  under an id that names someone else (ADR-0036 §SD3). */
  fixedRole?: { role: string; why: string } | null;
  /** An extra paragraph above the prompt, for a shape that needs to say what
   *  it will not do. */
  notice?: ReactNode;
  compose: (role: DispatchRole) => string;
  onClose: () => void;
}

/**
 * Pick a role, read the prompt, send it (ADR-0030 §SD3).
 *
 * The prompt is shown in full and stays editable: it is typed into the session's
 * input box and left there unsent, so what the operator reads here is exactly
 * what they will press Enter on. Nothing starts running from this dialog.
 *
 * Deliberately ignorant of *what* it is dispatching. Three subjects reach it —
 * a slice in the `act` shape, a slice in the `prepare` shape (ADR-0036 §SD5),
 * and a ritual off a calendar bar (§SD4) — and each hands in its own composed
 * text. Keeping one dialog keeps one place that spawns a session, so the tier
 * pinning and the "we do not press Enter" promise cannot come apart per surface.
 */
export function DispatchDialog({
  action,
  subject,
  dispatch,
  preferredRole,
  fixedRole = null,
  notice,
  compose,
  onClose,
}: Props) {
  const all = dispatch.present ? dispatch.roles : [];
  // A fixed role narrows the picker to one entry rather than hiding it: the
  // operator still has to see which role and which tier is about to run.
  const roles = fixedRole ? all.filter((r) => r.role === fixedRole.role) : all;
  const defaultRole = roles.find((r) => r.role === preferredRole);
  const [roleName, setRoleName] = useState((defaultRole ?? roles[0])?.role ?? '');
  const role: DispatchRole | undefined = roles.find((r) => r.role === roleName);

  const composed = useMemo(() => (role ? compose(role) : ''), [role, compose]);
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
      // which has not been sent. Carry the attach_key the server already
      // issued for this PTY (ADR-0028 §SD1) so the terminal page's first WS
      // connect attaches to it instead of spawning a second, blank PTY
      // (risks.md S-11) — session_id, when present, is the stronger identity.
      const params = new URLSearchParams({ view: 'terminal', harness: 'claude' });
      if (res.session_id) {
        params.set('session_id', res.session_id);
      } else if (res.attach_key) {
        params.set('attach_key', res.attach_key);
      }
      window.location.href = `/agent?${params.toString()}`;
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
        aria-label={action}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dlg-head">
          <h3>
            {action} · <code>{subject}</code>
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
                  disabled={!!fixedRole}
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

            {fixedRole && <p className="dlg-fixed-role">{fixedRole.why}</p>}

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

            {notice}

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
