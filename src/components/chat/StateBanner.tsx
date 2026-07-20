import type { FC } from 'react';

interface StateBannerProps {
  state: 'connecting' | 'loading' | 'ended' | 'error' | 'ready' | null;
  errorMessage?: string;
}

export const StateBanner: FC<StateBannerProps> = ({ state, errorMessage }) => {
  if (!state || state === 'ready') return null;

  return (
    <div className={`state-banner st-${state}`}>
      {state === 'connecting' && (
        <>
          <span className="banner-spinner" />
          Starting Claude…
        </>
      )}
      {state === 'loading' && (
        <>
          <span className="banner-spinner" />
          Loading messages…
        </>
      )}
      {state === 'ended' && (
        <>
          Session ended — <a href="/">back to board</a>
        </>
      )}
      {state === 'error' && (
        <>
          {errorMessage || 'Connection lost — retrying…'}
        </>
      )}
    </div>
  );
};
