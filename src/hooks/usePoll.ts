import { useEffect, useRef, useState, useCallback } from 'react';

/** Generic poll hook — calls `fetchFn` every `intervalMs` ms.
 *  Cleans up on unmount; pauses when the tab is hidden. */
export function usePoll<T>(
  fetchFn: () => Promise<T>,
  intervalMs: number
): { data: T | null; error: string | null; loading: boolean; refetch: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  const tick = useCallback(async () => {
    try {
      const result = await fetchFn();
      if (mountedRef.current) {
        setData(result);
        setError(null);
        setLoading(false);
      }
    } catch (e) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : 'fetch failed');
        setLoading(false);
      }
    }
  }, [fetchFn]);

  const refetch = useCallback(() => {
    setLoading(true);
    tick();
  }, [tick]);

  useEffect(() => {
    mountedRef.current = true;
    tick(); // immediate first fetch

    timerRef.current = setInterval(tick, intervalMs);

    // Pause polling when the tab is hidden
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        tick();
        if (timerRef.current) clearInterval(timerRef.current);
        timerRef.current = setInterval(tick, intervalMs);
      } else {
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
      }
    };
    document.addEventListener('visibilitychange', onVis);

    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearInterval(timerRef.current);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [tick, intervalMs]);

  return { data, error, loading, refetch };
}
