import { useEffect, useState } from 'react';
import {
  fetchTransitions,
  postTransition,
  type HandoffForm,
  type TransitionCandidate,
  type TransitionPublished,
} from '../lib/api';
import { BELT_COLUMNS } from '../lib/belt';
import { formShapeFor, type FormShape } from '../lib/transition-form';

const STAGE_LABEL: Record<string, string> = Object.fromEntries(
  BELT_COLUMNS.map((c) => [c.key, c.label]),
);

/**
 * The forward-handoff buttons on one belt card (slices.md S43a–S43c). Reads
 * `GET /work/transitions` for the acting role picked at the board header and
 * renders exactly what it says — a button per declared move out of this
 * row's stage, dimmed when `!allowed` with the gate's own reason as its
 * title. Nothing here decides whether a move may happen; that answer is
 * fetched, not computed (ADR-0044 §SD5).
 */
export function CardTransitions({
  project,
  client,
  sliceId,
  stage,
  actingRole,
  actingOffice,
  onMoved,
}: {
  project: string;
  /** The project's own `client:` frontmatter — the first segment of the
   *  `Assignment:` trailer `commit_message()` writes. */
  client: string;
  sliceId: string;
  stage: string;
  /** The role picked at the board header (S43a) — `null` before anyone has
   *  picked one, which every button reads as "not this seat's move yet". */
  actingRole: string | null;
  /** `dispatch.roles[].office` for the acting role — found on first real use
   *  (2026-09-12): leaving this unsent falls back to `-` server-side, and
   *  `.githooks/commit-msg` blocks any commit whose `Assignment:` office is
   *  `-`. The seat and its office travel together, never one without the
   *  other. */
  actingOffice: string;
  /** Called after a transition writes successfully, with the branch/commit
   *  the write landed on and where it got to outside this machine (S46 /
   *  ADR-0048) — the caller shows that as its confirmation. `published` is
   *  absent only against a server older than S46. */
  onMoved: (branch: string, commit: string, published?: TransitionPublished) => void;
}) {
  const [moves, setMoves] = useState<TransitionCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingForm, setPendingForm] = useState<{
    toStage: string;
    shape: FormShape;
  } | null>(null);
  const [sending, setSending] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    if (!actingRole) {
      setMoves(null);
      return;
    }
    fetchTransitions(project, sliceId, actingRole)
      .then((res) => {
        if (cancelled) return;
        if (!res.present) {
          setMoves([]);
          setError(res.reason);
          return;
        }
        setMoves(res.moves);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'อ่านด่านไม่ได้');
      });
    return () => {
      cancelled = true;
    };
  }, [project, sliceId, stage, actingRole]);

  function send(toStage: string, form: HandoffForm) {
    if (!actingRole) return;
    setSending(toStage);
    postTransition({
      project,
      sliceId,
      toStage,
      role: actingRole,
      office: actingOffice,
      client,
      form,
    })
      .then((res) => {
        setSending(null);
        if (res.ok) {
          setPendingForm(null);
          onMoved(res.branch, res.commit, res.published);
        } else {
          setError(res.reason ?? 'ด่านปฏิเสธ');
        }
      })
      .catch((e) => {
        setSending(null);
        setError(e instanceof Error ? e.message : 'ส่งต่อไม่สำเร็จ');
      });
  }

  if (!actingRole) {
    return (
      <span className="transition-hint">
        เลือก <b>สวมบทบาท</b> ที่หัวบอร์ดก่อนจะส่งต่อการ์ดนี้
      </span>
    );
  }
  if (!moves || moves.length === 0) {
    return error ? <span className="transition-error">{error}</span> : null;
  }

  return (
    <div className="transition-row">
      {moves.map((m) => (
        <button
          key={m.to_stage}
          className={`transition-btn${m.allowed ? '' : ' off'}`}
          disabled={!m.allowed || sending !== null}
          title={m.allowed ? `ส่งต่อไป ${STAGE_LABEL[m.to_stage] ?? m.to_stage}` : m.reason ?? ''}
          onClick={() => {
            const shape = formShapeFor(stage, m.to_stage);
            if (shape === 'none') send(m.to_stage, {});
            else setPendingForm({ toStage: m.to_stage, shape });
          }}
        >
          {sending === m.to_stage ? 'กำลังส่ง…' : `→ ${STAGE_LABEL[m.to_stage] ?? m.to_stage}`}
        </button>
      ))}
      {error && <span className="transition-error">{error}</span>}
      {pendingForm && (
        <TransitionFormDialog
          toStage={pendingForm.toStage}
          shape={pendingForm.shape}
          onCancel={() => setPendingForm(null)}
          onSubmit={(fields) => send(pendingForm.toStage, fields)}
        />
      )}
    </div>
  );
}

/**
 * The dialog a form-gated move opens (slices.md S43c, ADR-0046 §SD1) — three
 * mandatory fields for a handoff (`PR` auto-filled from the row · `ทดสอบได้
 * ที่ไหน` · `สิ่งที่เปลี่ยน / ข้อควรระวัง`, `ข้อมูลทดสอบ` left optional), one
 * for a reject (`เหตุผล`), one for a deploy (`release / tag`). The submit
 * button stays off while a mandatory field is blank; the real check still
 * runs again at `POST /work/transition`, so a dialog that got this wrong
 * fails there, it does not bypass it.
 */
function TransitionFormDialog({
  toStage,
  shape,
  onCancel,
  onSubmit,
}: {
  toStage: string;
  shape: FormShape;
  onCancel: () => void;
  onSubmit: (fields: HandoffForm) => void;
}) {
  const [env, setEnv] = useState('');
  const [risk, setRisk] = useState('');
  const [data, setData] = useState('');
  const [reason, setReason] = useState('');
  const [release, setRelease] = useState('');

  const ready =
    shape === 'handoff'
      ? env.trim() !== '' && risk.trim() !== ''
      : shape === 'reason'
        ? reason.trim() !== ''
        : shape === 'release'
          ? release.trim() !== ''
          : true;

  function submit() {
    if (!ready) return;
    if (shape === 'handoff') onSubmit({ env, risk, data: data || undefined });
    else if (shape === 'reason') onSubmit({ reason });
    else if (shape === 'release') onSubmit({ release });
    else onSubmit({});
  }

  return (
    <div className="dlg-backdrop" onClick={onCancel}>
      <div
        className="dlg"
        role="dialog"
        aria-modal="true"
        aria-label={`ส่งต่อไป ${STAGE_LABEL[toStage] ?? toStage}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dlg-head">
          <h3>ส่งต่อไป · {STAGE_LABEL[toStage] ?? toStage}</h3>
          <button className="dlg-x" onClick={onCancel} aria-label="ปิด">
            ✕
          </button>
        </div>

        {shape === 'handoff' && (
          <>
            <label className="dlg-label" htmlFor="tf-env">
              ทดสอบได้ที่ไหน <b>*</b>
            </label>
            <textarea
              id="tf-env"
              className="dlg-prompt"
              rows={2}
              value={env}
              onChange={(e) => setEnv(e.target.value)}
            />
            <label className="dlg-label" htmlFor="tf-risk">
              สิ่งที่เปลี่ยน / ข้อควรระวัง <b>*</b>
            </label>
            <textarea
              id="tf-risk"
              className="dlg-prompt"
              rows={3}
              value={risk}
              onChange={(e) => setRisk(e.target.value)}
            />
            <label className="dlg-label" htmlFor="tf-data">
              ข้อมูลทดสอบ
            </label>
            <textarea
              id="tf-data"
              className="dlg-prompt"
              rows={2}
              value={data}
              onChange={(e) => setData(e.target.value)}
            />
          </>
        )}

        {shape === 'reason' && (
          <>
            <label className="dlg-label" htmlFor="tf-reason">
              เหตุผลที่ตีกลับ <b>*</b>
            </label>
            <textarea
              id="tf-reason"
              className="dlg-prompt"
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </>
        )}

        {shape === 'release' && (
          <>
            <label className="dlg-label" htmlFor="tf-release">
              release / tag <b>*</b>
            </label>
            <textarea
              id="tf-release"
              className="dlg-prompt"
              rows={2}
              value={release}
              onChange={(e) => setRelease(e.target.value)}
            />
          </>
        )}

        <div className="dlg-foot">
          <span className="dlg-note">
            ข้อความที่กรอกไปโผล่ใน <b>body ของ commit</b> ที่บอร์ดสร้าง — ไม่ใช่ในเซลล์ของแถว
            (ADR-0046 §SD2)
          </span>
          <button className="dlg-cancel" onClick={onCancel}>
            ยกเลิก
          </button>
          <button className="dlg-go" onClick={submit} disabled={!ready}>
            ส่งต่อ
          </button>
        </div>
      </div>
    </div>
  );
}
