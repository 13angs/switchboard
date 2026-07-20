import { useState, useCallback, useEffect, useMemo } from 'react';
import { useBoardState } from '../hooks/useBoardState';
import { useNotifications } from '../hooks/useNotifications';
import {
  fetchTranscript,
  killSession,
  dismissSession,
  dismissSessions,
  undismissSessions,
} from '../lib/api';
import { useToast, ToastContainer } from '../components/shared/Toast';
import { Header } from '../components/board/Header';
import { BoardGrid } from '../components/board/BoardGrid';
import { NewSessionDialog } from '../components/board/NewSessionDialog';
import type { SessionCard } from '../lib/types';
import './Board.css';

function sessionUrl(view: 'chat' | 'terminal', card: SessionCard): string {
  const params = new URLSearchParams();
  params.set('view', view);
  params.set('session_id', card.session_id);
  if (card.harness) params.set('harness', card.harness);
  if (card.provider) params.set('provider', card.provider);
  if (card.title) params.set('label', card.title);
  return `/agent?${params.toString()}`;
}

export function Board() {
  const { state, error, loading, refetch, columns, archived } = useBoardState(5000);

  const [view, setView] = useState<'active' | 'archive'>('active');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [transcripts, setTranscripts] = useState<
    Record<string, { role: string; text: string; ts: string }[] | null>
  >({});
  const [loadingTranscripts, setLoadingTranscripts] = useState<Record<string, boolean>>({});
  const { toasts, toast } = useToast();
  const notifications = useNotifications(toast);
  const visibleCards = useMemo(() => {
    if (view === 'archive') return archived;
    return Object.values(columns).flat();
  }, [archived, columns, view]);
  const visibleIds = useMemo(() => visibleCards.map((c) => c.session_id), [visibleCards]);
  const selectedVisibleCount = useMemo(
    () => visibleIds.filter((id) => selectedIds.has(id)).length,
    [selectedIds, visibleIds]
  );

  useEffect(() => {
    setSelectedIds((prev) => {
      const visible = new Set(visibleIds);
      const next = new Set([...prev].filter((id) => visible.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [visibleIds]);

  const toggleTranscript = useCallback(
    async (sessionId: string) => {
      if (expandedId === sessionId) {
        setExpandedId(null);
        return;
      }
      setExpandedId(sessionId);
      if (transcripts[sessionId] !== undefined) return;

      setLoadingTranscripts((prev) => ({ ...prev, [sessionId]: true }));
      try {
        const res = await fetchTranscript(sessionId);
        setTranscripts((prev) => ({ ...prev, [sessionId]: res.messages }));
      } catch {
        setTranscripts((prev) => ({ ...prev, [sessionId]: null }));
      } finally {
        setLoadingTranscripts((prev) => ({ ...prev, [sessionId]: false }));
      }
    },
    [expandedId, transcripts]
  );

  const openChat = useCallback((card: SessionCard) => {
    notifications.markSessionRead(card.session_id);
    window.open(sessionUrl('chat', card), '_blank');
  }, [notifications]);

  const openTerminal = useCallback((card: SessionCard) => {
    notifications.markSessionRead(card.session_id);
    window.open(sessionUrl('terminal', card), '_blank');
  }, [notifications]);

  const handleKill = useCallback(
    async (sessionId: string) => {
      try {
        await killSession(sessionId);
        toast('Session killed');
        refetch();
      } catch {
        toast('Kill failed');
      }
    },
    [toast, refetch]
  );

  const handleDismiss = useCallback(
    async (sessionId: string) => {
      try {
        await dismissSession(sessionId);
        toast('Session dismissed');
        refetch();
      } catch {
        toast('Dismiss failed');
      }
    },
    [toast, refetch]
  );

  const toggleSelected = useCallback((sessionId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }, []);

  const selectAllVisible = useCallback(() => {
    setSelectedIds(new Set(visibleIds));
  }, [visibleIds]);

  const clearSelected = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const dismissSelected = useCallback(async () => {
    const ids = visibleIds.filter((id) => selectedIds.has(id));
    if (ids.length === 0) return;
    try {
      const res = await dismissSessions(ids);
      toast(`Dismissed ${res.count ?? ids.length} sessions`);
      setSelectedIds(new Set());
      refetch();
    } catch {
      toast('Dismiss selected failed');
    }
  }, [refetch, selectedIds, toast, visibleIds]);

  const restoreSelected = useCallback(async () => {
    const ids = visibleIds.filter((id) => selectedIds.has(id));
    if (ids.length === 0) return;
    try {
      const res = await undismissSessions(ids);
      toast(`Restored ${res.count ?? ids.length} sessions`);
      setSelectedIds(new Set());
      refetch();
    } catch {
      toast('Restore selected failed');
    }
  }, [refetch, selectedIds, toast, visibleIds]);

  const copyId = useCallback(
    async (sessionId: string) => {
      try {
        await navigator.clipboard.writeText(sessionId);
      } catch {
        // fallback
        const ta = document.createElement('textarea');
        ta.value = sessionId;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      toast('Session ID copied');
    },
    [toast]
  );

  const newSession = useCallback(() => {
    setDialogOpen(true);
  }, []);

  const startSession = useCallback(
    (harness: string, provider: string, label: string) => {
      setDialogOpen(false);
      // New sessions always open in the terminal — the sole interaction
      // surface (ADR-0005). Chat is a transcript viewer for existing sessions.
      let url = `/agent?view=terminal&harness=${encodeURIComponent(harness)}&provider=${encodeURIComponent(provider)}`;
      if (label) url += `&label=${encodeURIComponent(label)}`;
      window.open(url, '_blank');
    },
    []
  );

  return (
    <>
      <Header
        repo={state?.repo ?? (error ? 'offline' : '—')}
        view={view}
        onViewChange={setView}
        onNewSession={newSession}
        unreadApprovals={notifications.unreadCount}
      />

      {loading && !state && (
        <div className="loading-banner">connecting…</div>
      )}

      {error && !state && (
        <div className="error-banner">offline — {error}</div>
      )}

      {visibleCards.length > 0 && (
        <div className="bulk-bar" aria-live="polite">
          <span className="bulk-count">
            <span className="tnum">{selectedVisibleCount}</span> selected
          </span>
          <button onClick={selectAllVisible}>
            Select all visible
          </button>
          <button onClick={clearSelected} disabled={selectedVisibleCount === 0}>
            Clear
          </button>
          <span className="bulk-spacer" />
          {view === 'archive' ? (
            <button
              className="primary"
              onClick={restoreSelected}
              disabled={selectedVisibleCount === 0}
            >
              Restore selected
            </button>
          ) : (
            <button
              className="danger-action"
              onClick={dismissSelected}
              disabled={selectedVisibleCount === 0}
            >
              Dismiss selected
            </button>
          )}
        </div>
      )}

      <BoardGrid
        columns={columns}
        archived={archived}
        view={view}
        expandedId={expandedId}
        onToggleTranscript={toggleTranscript}
        onOpenChat={openChat}
        onOpenTerminal={openTerminal}
        onKill={handleKill}
        onDismiss={handleDismiss}
        onCopyId={copyId}
        selectedIds={selectedIds}
        onToggleSelected={toggleSelected}
        transcripts={transcripts}
        loadingTranscripts={loadingTranscripts}
      />

      {dialogOpen && (
        <NewSessionDialog
          onClose={() => setDialogOpen(false)}
          onStart={startSession}
          launchers={state?.launchers ?? [{ harness: 'claude', providers: state?.providers ?? ['claude'] }]}
        />
      )}

      <ToastContainer toasts={toasts} />
    </>
  );
}
