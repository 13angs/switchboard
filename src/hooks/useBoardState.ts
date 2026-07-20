import { useMemo, useRef } from 'react';
import { usePoll } from './usePoll';
import { fetchBoardState } from '../lib/api';
import type { BoardState, SessionCard } from '../lib/types';

/** Shallow field equality for two cards (SessionCard is a flat DTO). */
function cardEqual(a: SessionCard, b: SessionCard): boolean {
  const ak = Object.keys(a) as (keyof SessionCard)[];
  const bk = Object.keys(b) as (keyof SessionCard)[];
  if (ak.length !== bk.length) return false;
  return ak.every((k) => a[k] === b[k]);
}

export interface BoardViewModel {
  state: BoardState | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
  /** Cards grouped by activity column */
  columns: Record<string, SessionCard[]>;
  /** Archived (dismissed or auto_archived) cards */
  archived: SessionCard[];
}

export function useBoardState(pollMs: number = 5000): BoardViewModel {
  const { data, error, loading, refetch } = usePoll<BoardState>(
    fetchBoardState,
    pollMs
  );

  // Each poll returns freshly-parsed JSON, so every card is a new object even
  // when nothing changed — which would defeat React.memo(Card). Reuse the prior
  // render's object for any card whose fields are unchanged, so memoised cards
  // keep a stable identity across polls and skip re-rendering.
  const prevById = useRef<Map<string, SessionCard>>(new Map());
  const { columns, archived } = useMemo(() => {
    const sessions = data?.sessions ?? [];
    const nextById = new Map<string, SessionCard>();
    const cols: Record<string, SessionCard[]> = {
      Working: [],
      Awaiting: [],
      Idle: [],
      Blocked: [],
    };
    const arch: SessionCard[] = [];

    for (const raw of sessions) {
      const prev = prevById.current.get(raw.session_id);
      const c = prev && cardEqual(prev, raw) ? prev : raw;
      nextById.set(raw.session_id, c);
      if (c.dismissed || c.auto_archived) {
        arch.push(c);
      } else {
        const col = cols[c.activity] ? c.activity : 'Idle';
        cols[col].push(c);
      }
    }
    prevById.current = nextById;
    return { columns: cols, archived: arch };
  }, [data]);

  return { state: data, error, loading, refetch, columns, archived };
}
