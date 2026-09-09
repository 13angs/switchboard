import { useState, useMemo, type ReactNode } from 'react';
import { startSession, type WorkspaceDispatch } from '../lib/api';
import type { DispatchRole } from '../lib/dispatch-prompt';

/** ADR-0037 §SD2 — grill has no row to resolve a role→tier pair from, so its
 *  box is two dropdowns (tier, effort) instead of the usual role list. `role`
 *  is the literal string this dialog sends, never looked up in `dispatch.roles`. */
export interface TierEffortPicker {
  role: string;
  /** tier name → model id, the same map the 7-role table reads
   *  (`dispatch.tiers`). */
  tiers: Record<string, string>;
  defaultTier: string;
  defaultEffort: string;
}

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
  /** Set only for the grill button (ADR-0037 §SD2) — replaces the role-list
   *  picker with tier/effort dropdowns. Mutually exclusive with `fixedRole`. */
  tierPicker?: TierEffortPicker | null;
  /** ADR-0037 §SD3 — grill opens a new tab and leaves `/work` where it is.
   *  Every other shape opens no room at all (ADR-0038 §SD1) — dispatch is
   *  fire-and-forget; the operator reopens the room later from the session
   *  board, not from this dialog. Default `"stay"`. */
  openMode?: "stay" | "new-tab";
  /** An extra paragraph above the prompt, for a shape that needs to say what
   *  it will not do. */
  notice?: ReactNode;
  /** ADR-0038 §SD1 — called after a successful `"stay"` dispatch, once the
   *  dialog has already closed. Not called for `"new-tab"` (grill), whose own
   *  new tab is the confirmation. */
  onDispatched?: () => void;
  compose: (role: DispatchRole) => string;
  onClose: () => void;
}

/**
 * Pick a role, read the prompt, send it (ADR-0030 §SD3).
 *
 * The prompt is shown in full and stays editable, so what the operator reads
 * here is exactly what runs — for a `"stay"` dispatch (every shape but
 * grill), the click on the send button *is* the decision to start
 * (ADR-0034 §SD1): the server types it in and submits it, no tab opens, and
 * this dialog just closes (ADR-0038 §SD1). Grill (`"new-tab"`) is the one
 * shape that still leaves a room open, because it is a live conversation the
 * operator sits through rather than a background dispatch (ADR-0037 §SD3).
 *
 * Deliberately ignorant of *what* it is dispatching. Three subjects reach it —
 * a slice in the `act` shape, a slice in the `prepare` shape (ADR-0036 §SD5),
 * and a ritual off a calendar bar (§SD4) — and each hands in its own composed
 * text. Keeping one dialog keeps one place that spawns a session, so the tier
 * pinning and the dispatch behavior cannot come apart per surface.
 */
export function DispatchDialog({
  action,
  subject,
  dispatch,
  preferredRole,
  fixedRole = null,
  tierPicker = null,
  openMode = 'stay',
  notice,
  onDispatched,
  compose,
  onClose,
}: Props) {
  const all = dispatch.present ? dispatch.roles : [];
  // A fixed role narrows the picker to one entry rather than hiding it: the
  // operator still has to see which role and which tier is about to run.
  const roles = fixedRole ? all.filter((r) => r.role === fixedRole.role) : all;
  const defaultRole = roles.find((r) => r.role === preferredRole);
  const [roleName, setRoleName] = useState((defaultRole ?? roles[0])?.role ?? '');

  // ADR-0037 §SD2 — grill's own two-dropdown picker, independent of the
  // role-list state above. Both live in `useState` scoped to this component
  // instance, so a fresh dialog open always starts back at the defaults
  // (§SD2(ก): the override never sticks past the click that made it).
  const tierNames = tierPicker ? Object.keys(tierPicker.tiers) : [];
  const [tier, setTier] = useState(tierPicker?.defaultTier ?? '');
  const [effort, setEffort] = useState(tierPicker?.defaultEffort ?? '');

  const role: DispatchRole | undefined = tierPicker
    ? tier in tierPicker.tiers
      ? {
          role: tierPicker.role,
          tier,
          model: tierPicker.tiers[tier],
          // roles.md § โมเดลต่อ role: light rejects --effort outright.
          effort: tier === 'light' ? null : effort,
        }
      : undefined
    : roles.find((r) => r.role === roleName);

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
      if (openMode === 'new-tab') {
        // Grill has no row yet to reopen it from later (ADR-0037 §SD1), so it
        // still needs the room dispatch just spawned. The session may not
        // have an id yet — it gets one at the first prompt, and grill does
        // not submit one (ADR-0038 §SD2 only submits the board's own
        // dispatch signature). Carry the attach_key the server already
        // issued for this PTY (ADR-0028 §SD1) so the terminal page's first WS
        // connect attaches to it instead of spawning a second, blank PTY
        // (risks.md S-11) — session_id, when present, is the stronger identity.
        const params = new URLSearchParams({ view: 'terminal', harness: 'claude' });
        if (res.session_id) {
          params.set('session_id', res.session_id);
        } else if (res.attach_key) {
          params.set('attach_key', res.attach_key);
        }
        window.open(`/agent?${params.toString()}`, '_blank');
        setSending(false);
        onClose();
      } else {
        // ADR-0038 §SD1/§SD2 — every other shape stays on /work: the server
        // already submitted the prompt (its first turn is what gives it a
        // session_id and puts it on the board later), so there is no room to
        // navigate to and no identity worth carrying here.
        setSending(false);
        onClose();
        onDispatched?.();
      }
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
            {tierPicker ? (
              // ADR-0037 §SD2 — grill has no row to resolve a role from yet,
              // so the operator picks tier + effort directly instead of a
              // role list. Resets to the defaults every time this dialog
              // mounts fresh — nothing here is written back to roles.md.
              <div className="dlg-tier-picker">
                <label>
                  model
                  <select
                    value={tier}
                    onChange={(e) => {
                      setTier(e.target.value);
                      setPrompt(null);
                    }}
                  >
                    {tierNames.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  effort
                  <select
                    value={effort}
                    disabled={tier === 'light'}
                    onChange={(e) => {
                      setEffort(e.target.value);
                      setPrompt(null);
                    }}
                  >
                    {['low', 'medium', 'high', 'xhigh'].map((ef) => (
                      <option key={ef} value={ef}>
                        {ef}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ) : (
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
            )}

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
                  ·{' '}
                  {tierPicker
                    ? `แผนที่มาจาก ${dispatch.source.tiers}`
                    : `แผนที่มาจาก ${dispatch.source.roles} + ${dispatch.source.tiers}`}
                </span>
              </p>
            )}

            {notice}

            <label className="dlg-label" htmlFor="dispatch-prompt">
              ข้อความที่จะพิมพ์ลงในห้อง —{' '}
              <b>
                {openMode === 'new-tab' ? 'ยังไม่กด Enter ให้' : 'กด Enter ให้เองหลังพิมพ์'}
              </b>
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
                {openMode === 'new-tab' ? (
                  <>
                    เปิดแท็บใหม่แล้วพิมพ์ข้อความนี้ค้างไว้ — แท็บ /work นี้ยังอยู่ที่เดิม ·{' '}
                    <b>คุณเป็นคนกด Enter</b>
                  </>
                ) : (
                  <>
                    ไม่เปิดห้องให้ — สั่งแล้วเริ่มทำงานเลย ·{' '}
                    <b>เข้าห้องได้จากบอร์ด session เมื่อไหร่ก็ได้</b>
                  </>
                )}
              </span>
              <button className="dlg-cancel" onClick={onClose}>
                ยกเลิก
              </button>
              <button
                className="dlg-go"
                onClick={send}
                disabled={sending || !role}
              >
                {sending
                  ? 'กำลังสั่ง…'
                  : openMode === 'new-tab'
                    ? 'เปิดห้อง + พิมพ์ให้'
                    : 'สั่งงาน'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
