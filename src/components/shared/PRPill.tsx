import type { FC } from 'react';

interface PRPillProps {
  number: number | null;
  url: string | null;
  state: string | null; // 'mergeable' | 'ci-fail' | 'review' | null
  label?: string | null;
}

export const PRPill: FC<PRPillProps> = ({ number, url, state, label }) => {
  if (!number) {
    return <span className="pr-pill no-pr">No PR</span>;
  }

  const cls = state === 'mergeable' ? 'mergeable'
    : state === 'ci-fail' ? 'ci-fail'
    : state === 'review' ? 'review'
    : 'no-pr';

  const base = label || `PR #${number}`;
  const pillLabel = state === 'mergeable' ? `${base} · mergeable`
    : state === 'ci-fail' ? `${base} · CI failed`
    : state === 'review' ? `${base} · review`
    : base;

  if (url) {
    return (
      <a className={`pr-pill ${cls}`} href={url} target="_blank" rel="noopener noreferrer">
        <span className="pr-dot" />
        {pillLabel}
      </a>
    );
  }

  return (
    <span className={`pr-pill ${cls}`}>
      <span className="pr-dot" />
      {pillLabel}
    </span>
  );
};
