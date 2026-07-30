import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';
import { useSessionNotifications } from '../hooks/useSessionNotifications';
import { Topbar } from '../components/shared/Topbar';
import { LeftDrawer } from '../components/shared/LeftDrawer';
import { RightDrawer } from '../components/shared/RightDrawer';
import { StatusBar } from '../components/shared/StatusBar';
import { TerminalBody } from '../components/terminal/TerminalBody';
import { FilesPanel } from '../components/terminal/FilesPanel';
import { StateBanner } from '../components/chat/StateBanner';
import { MessageList } from '../components/chat/MessageList';
import { RawJsonView } from '../components/chat/RawJsonView';
import { ReadOnlyBar } from '../components/chat/ReadOnlyBar';
import { FileNavigator } from '../components/files/FileNavigator';
import {
  fetchBoardState,
  fetchRichTranscript,
  killSession,
  startSession,
} from '../lib/api';
import type { SessionDrawerView, SessionInfo, SessionStatus, FileEntry } from '../lib/terminal-types';
import type { NotificationEvent, SessionCard, RichMessage, RichContentBlock } from '../lib/types';
import { ago } from '../lib/format';
import { extractPullRequests, mergePullRequests, type PullRequestRef } from '../lib/prs';
import { sessionDisplayTitle } from '../lib/session-title';
import { messagesToMarkdown } from '../lib/export-markdown';
import './Terminal.css';
import './Chat.css';

type AgentView = 'terminal' | 'chat' | 'files';
type TerminalCenterView = 'terminal' | 'files';
type ChatState = 'connecting' | 'loading' | 'ended' | 'error' | 'ready' | null;

interface Message extends RichMessage {}

function initialAgentView(): AgentView {
  const v = new URLSearchParams(location.search).get('view');
  if (v === 'chat' || v === 'files') return v;
  return 'terminal';
}

function buildWsUrl(
  sessionId: string | null,
  harness: string | null,
  provider: string | null
): string {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  if (harness) params.set('harness', harness);
  if (provider) params.set('provider', provider);
  const qs = params.toString();
  return `ws://${location.host}/ws/agent${qs ? '?' + qs : ''}`;
}

function cardToSessionInfo(c: SessionCard): SessionInfo {
  return {
    session_id: c.session_id,
    title: c.title || c.session_id || 'Untitled',
    activity: c.activity,
    harness: c.harness,
    provider: c.provider,
    pr_number: c.pr_number,
    pr_url: c.pr_url,
    pr_state: c.pr_state,
    git_branch: c.git_branch,
    worktree_path: c.worktree_path,
    dismissed: Boolean(c.dismissed),
    auto_archived: Boolean(c.auto_archived),
    turn_count: c.turn_count,
    total_cost_usd: c.total_cost_usd,
    cost_partial: c.cost_partial,
    unpriced_models: c.unpriced_models,
    rates_checked_on: c.rates_checked_on,
    age: ago(c.last_ts),
    status: 'connecting',
    health: c.health,
  };
}

function replaceSessionParams(sessionId: string, harness: string, provider: string | null) {
  const url = new URL(location.href);
  url.searchParams.set('session_id', sessionId);
  url.searchParams.set('harness', harness);
  if (provider) url.searchParams.set('provider', provider);
  history.replaceState(null, '', url.toString());
}

function messageKey(m: Message): string {
  const text = m.content
    ?.map((b) => (b.type === 'text' ? b.text || '' : b.type === 'tool_use' ? `[tool:${b.name}]` : ''))
    .join(' ') ?? '';
  return `${m.ts}\0${m.role}\0${text}`;
}

function mergeTranscriptMessages(current: Message[], incoming: Message[]): Message[] {
  if (incoming.length === 0) return current;
  const seen = new Set(current.map(messageKey));
  const next = [
    ...current,
    ...incoming.filter((m) => !seen.has(messageKey(m))),
  ];
  return next;
}

/** Flatten rich content blocks to plain text for PR extraction etc. */
function flattenContent(blocks: RichContentBlock[] | undefined): string {
  if (!blocks) return '';
  return blocks
    .map((b) => {
      if (b.type === 'text') return b.text || '';
      if (b.type === 'tool_use') return `[tool: ${b.name || '?'}]`;
      if (b.type === 'tool_result') return '[tool_result]';
      if (b.type === 'thinking') return '';
      return '';
    })
    .join(' ');
}

export function AgentPage() {
  const qs = new URLSearchParams(location.search);
  const initialSessionId = qs.get('session_id');
  const harness = qs.get('harness') ?? 'claude';
  const provider = qs.get('provider');
  const label = qs.get('label');
  const initialView = initialAgentView();

  const [agentView, setAgentView] = useState<AgentView>(initialView);
  const [terminalMounted, setTerminalMounted] = useState(initialView === 'terminal');
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [status, setStatus] = useState<SessionStatus>(initialSessionId ? 'waiting' : 'connecting');
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [terminalView, setTerminalView] = useState<TerminalCenterView>('terminal');
  const [sessionDrawerView, setSessionDrawerView] = useState<SessionDrawerView>('active');
  const [ended, setEnded] = useState(false);
  const [allSessions, setAllSessions] = useState<SessionInfo[]>([]);
  const [observedPrs, setObservedPrs] = useState<PullRequestRef[]>([]);
  const [files] = useState<FileEntry[]>([]);

  const [chatState, setChatState] = useState<ChatState>(initialSessionId ? 'loading' : 'connecting');
  const [errorMsg, setErrorMsg] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [typing, setTyping] = useState(false);
  // Transient debug aid — local only, resets on session switch (ADR-0015 §SD4).
  const [showRawJson, setShowRawJson] = useState(false);
  const lastTsRef = useRef<string | null>(null);
  const typingRef = useRef(false);
  const startRequestedRef = useRef(false);
  const transcriptRefreshTimerRef = useRef<number | null>(null);

  const termApiRef = useRef<{
    write: (data: Uint8Array | string) => void;
    writeln: (text: string, cls?: string) => void;
    fit: () => void;
    dispose: () => void;
  } | null>(null);
  const outputDecoderRef = useRef(new TextDecoder());
  const outputScanRef = useRef('');

  const applyTranscriptMessages = useCallback((incoming: Message[], replace = false) => {
    setMessages((prev) => {
      const merged = replace ? incoming : mergeTranscriptMessages(prev, incoming);
      lastTsRef.current = merged.length > 0 ? merged[merged.length - 1].ts : null;
      return merged;
    });
    if (incoming.some((m) => m.role === 'assistant')) {
      setTyping(false);
    }
    if (incoming.length > 0 || replace) {
      setChatState('ready');
    }
  }, []);

  const refreshTranscript = useCallback(
    async (replace = false) => {
      if (!sessionId) return;
      const data = await fetchRichTranscript(
        sessionId,
        replace ? undefined : lastTsRef.current ?? undefined
      );
      applyTranscriptMessages(data.messages ?? [], replace);
    },
    [applyTranscriptMessages, sessionId]
  );

  const scheduleTranscriptRefresh = useCallback(
    (delay = 250) => {
      if (!sessionId) return;
      if (transcriptRefreshTimerRef.current !== null) {
        window.clearTimeout(transcriptRefreshTimerRef.current);
      }
      transcriptRefreshTimerRef.current = window.setTimeout(() => {
        transcriptRefreshTimerRef.current = null;
        void refreshTranscript();
      }, delay);
    },
    [refreshTranscript, sessionId]
  );

  const switchAgentView = useCallback(
    (nextView: AgentView) => {
      if (nextView === 'terminal' && (sessionId || !startRequestedRef.current)) {
        setTerminalMounted(true);
        requestAnimationFrame(() => termApiRef.current?.fit());
      }
      setAgentView(nextView);
      const url = new URL(location.href);
      url.searchParams.set('view', nextView);
      if (sessionId) url.searchParams.set('session_id', sessionId);
      history.pushState(null, '', url.toString());
    },
    [sessionId]
  );

  useEffect(() => {
    const onPopState = () => {
      const nextView = initialAgentView();
      if (nextView === 'terminal' && (sessionId || !startRequestedRef.current)) {
        setTerminalMounted(true);
        requestAnimationFrame(() => termApiRef.current?.fit());
      }
      setAgentView(nextView);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [sessionId]);

  useEffect(() => {
    if (agentView === 'terminal' && sessionId) {
      setTerminalMounted(true);
      requestAnimationFrame(() => termApiRef.current?.fit());
    }
  }, [agentView, sessionId]);

  // The raw-JSON view is a per-session debug aid, never a stored preference
  // (ADR-0015 §SD4) — switching sessions returns to rendered bubbles.
  useEffect(() => {
    setShowRawJson(false);
  }, [sessionId]);

  useEffect(() => {
    if (initialSessionId || !provider || agentView !== 'chat' || startRequestedRef.current) return;
    startRequestedRef.current = true;
    let active = true;

    const discover = async () => {
      try {
        let data = await startSession(harness, provider, label ?? undefined);
        if (!active) return;
        if (!data.session_id && !data.session_started) {
          await new Promise((r) => setTimeout(r, 2000));
          data = await startSession(harness, provider, label ?? undefined);
        }
        if (data.session_id && active) {
          setSessionId(data.session_id);
          replaceSessionParams(data.session_id, harness, provider);
          setChatState('loading');
        } else if (active) {
          setChatState('error');
          setErrorMsg('No session_id returned');
        }
      } catch {
        if (active) {
          setChatState('error');
          setErrorMsg('Failed to start session');
        }
      }
    };

    discover();
    return () => {
      active = false;
    };
  }, [agentView, initialSessionId, harness, provider, label]);

  // ADR-0010 §3: board state fetch extracted from its own poll timer;
  // now called from within the transcript poll (merged 5s cycle).
  const fetchBoardStateIfActive = useCallback(
    async (active: { current: boolean }) => {
      try {
        const state = await fetchBoardState();
        if (!active.current) return;
        const infos = state.sessions.map(cardToSessionInfo);
        setAllSessions(infos);
        if (sessionId) {
          const card = state.sessions.find((c) => c.session_id === sessionId);
          if (card) {
            setSession({ ...cardToSessionInfo(card), status });
          }
        }
      } catch {
        /* keep last known state */
      }
    },
    [sessionId, status]
  );

  const wsUrl = buildWsUrl(sessionId, harness, provider);
  const { send: wsSend, close: wsClose } = useWebSocket({
    url: wsUrl,
    enabled: terminalMounted,
    binaryType: 'arraybuffer',
    onMessage: useCallback(
      (data: ArrayBuffer | string) => {
        if (typeof data !== 'string') {
          termApiRef.current?.write(new Uint8Array(data));
          scheduleTranscriptRefresh();
          const text = outputDecoderRef.current.decode(data, { stream: true });
          const scan = outputScanRef.current + text;
          outputScanRef.current = scan.slice(-2000);
          const prs = extractPullRequests(scan);
          if (prs.length > 0) {
            setObservedPrs((prev) => mergePullRequests(prev, prs));
          }
          return;
        }
        try {
          const ctl = JSON.parse(data);
          if (ctl.type === 'session_id' && ctl.id) {
            const sid = ctl.id;
            setSessionId(sid);
            setStatus('connected');
            replaceSessionParams(sid, harness, provider);
          } else if (ctl.type === 'exit') {
            setEnded(true);
            setStatus('ended');
            setChatState('ended');
            termApiRef.current?.writeln(
              ctl.code != null
                ? `\r\n[Process exited with code ${ctl.code}]`
                : '\r\n[Connection closed]',
              'warn'
            );
          } else if (ctl.type === 'error') {
            termApiRef.current?.writeln(`[${ctl.message || 'error'}]`, 'err');
          }
        } catch {
          /* malformed control frame */
        }
      },
      [harness, provider, scheduleTranscriptRefresh]
    ),
    onOpen: useCallback(() => {
      termApiRef.current?.fit();
    }, []),
  });

  const handleData = useCallback((data: string) => wsSend(data), [wsSend]);

  const handleResize = useCallback(
    (cols: number, rows: number) => {
      wsSend(JSON.stringify({ type: 'resize', cols, rows }));
    },
    [wsSend]
  );

  const handleMount = useCallback(
    (api: {
      write: (data: Uint8Array | string) => void;
      writeln: (text: string, cls?: string) => void;
      fit: () => void;
      dispose: () => void;
    }) => {
      termApiRef.current = api;
    },
    []
  );

  useEffect(() => {
    typingRef.current = typing;
  }, [typing]);

  useEffect(() => {
    return () => {
      if (transcriptRefreshTimerRef.current !== null) {
        window.clearTimeout(transcriptRefreshTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!sessionId) return;

    let active = true;
    const activeRef = { current: true };

    const loadHistory = async () => {
      try {
        await Promise.all([
          refreshTranscript(true),
          fetchBoardStateIfActive(activeRef),
        ]);
        if (!active) return;
        setChatState('ready');
        setStatus('connected');
      } catch {
        if (active && lastTsRef.current === null) {
          setChatState('error');
          setErrorMsg('Failed to load messages');
        }
      }
    };

    const poll = async () => {
      try {
        await Promise.all([
          refreshTranscript(),
          fetchBoardStateIfActive(activeRef),
        ]);
        if (!active) return;
      } catch {
        if (!typingRef.current) {
          setChatState('error');
          setErrorMsg('Connection lost - retrying...');
        }
      }
    };

    loadHistory();
    const timer = setInterval(poll, 5000);
    return () => {
      active = false;
      activeRef.current = false;
      clearInterval(timer);
    };
  }, [refreshTranscript, fetchBoardStateIfActive, sessionId]);

  useEffect(() => {
    if (!sessionId || !typing) return;
    const timer = setInterval(() => {
      void refreshTranscript();
    }, 1000);
    return () => clearInterval(timer);
  }, [refreshTranscript, sessionId, typing]);

  const handleKill = useCallback(async () => {
    if (sessionId) {
      try {
        await killSession(sessionId);
      } catch {
        /* ignore */
      }
    }
    termApiRef.current?.dispose();
    window.close();
  }, [sessionId]);

  const handleClose = useCallback(() => {
    wsClose();
    window.close();
  }, [wsClose]);

  const handleSelectSession = useCallback(
    (nextSessionId: string) => {
      const selected = allSessions.find((s) => s.session_id === nextSessionId);
      const params = new URLSearchParams();
      params.set('view', agentView);
      params.set('session_id', nextSessionId);
      if (selected?.harness) params.set('harness', selected.harness);
      if (selected?.provider) params.set('provider', selected.provider);
      location.assign(`/agent?${params.toString()}`);
    },
    [agentView, allSessions]
  );

  const titleShort = sessionDisplayTitle(label, session, sessionId, provider);
  const handleSessionEvent = useCallback(
    (event: NotificationEvent) => {
      scheduleTranscriptRefresh(50);
      if (event.type === 'input_ready') {
        setTyping(false);
      }
    },
    [scheduleTranscriptRefresh]
  );
  const sessionAlert = useSessionNotifications(sessionId, titleShort, handleSessionEvent);
  const chatPrs = useMemo(
    () => extractPullRequests(messages.map((m) => flattenContent(m.content)).join('\n')),
    [messages]
  );
  const pullRequests = agentView === 'chat' ? chatPrs : observedPrs;

  const displaySession: SessionInfo = session ?? {
    session_id: sessionId ?? '',
    title: titleShort,
    activity: 'Working',
    harness,
    provider: provider ?? (harness === 'codex' ? 'openai' : 'claude'),
    pr_number: null,
    pr_url: null,
    pr_state: null,
    git_branch: null,
    worktree_path: null,
    dismissed: false,
    auto_archived: false,
    turn_count: 0,
    total_cost_usd: null,
    age: '-',
    status,
  };

  // Export the transcript as markdown — client-side Blob download (ADR-0007).
  const handleExport = () => {
    const md = messagesToMarkdown(messages, {
      title: titleShort,
      harness: displaySession.harness,
      provider: displaySession.provider,
      totalCostUsd: displaySession.total_cost_usd,
    });
    const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcript-${sessionId ?? 'session'}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div id="page">
      <Topbar
        session={displaySession}
        sessionAlert={sessionAlert}
        leftOpen={leftOpen}
        rightOpen={rightOpen}
        agentView={agentView}
        onAgentViewChange={switchAgentView}
        onToggleLeft={() => setLeftOpen((o) => !o)}
        onToggleRight={() => setRightOpen((o) => !o)}
        onKill={handleKill}
        onClose={handleClose}
      />

      <div id="main">
        <LeftDrawer
          open={leftOpen}
          sessions={allSessions}
          activeSessionId={sessionId}
          view={sessionDrawerView}
          onViewChange={setSessionDrawerView}
          onSelectSession={handleSelectSession}
        />

        <div className="center-area">
          {terminalMounted && (
            <div className={`agent-pane${agentView === 'terminal' ? ' active' : ''}`}>
              {terminalView === 'terminal' ? (
                <TerminalBody
                  onData={handleData}
                  onResize={handleResize}
                  onMount={handleMount}
                  ended={ended}
                />
              ) : (
                <FilesPanel
                  files={files}
                  branch={displaySession.git_branch}
                  visible={true}
                />
              )}
            </div>
          )}

          <div className={`agent-pane chat-body${agentView === 'chat' ? ' active' : ''}`}>
            <StateBanner state={chatState} errorMessage={errorMsg} />
            {showRawJson ? (
              <RawJsonView messages={messages} />
            ) : (
              <MessageList messages={messages} typing={typing} />
            )}
            <div className="chat-bottom">
              <ReadOnlyBar
                state={typing ? 'working' : ended ? 'ended' : chatState}
                sessionId={sessionId}
                costUsd={displaySession.total_cost_usd}
                costPartial={displaySession.cost_partial}
                unpricedModels={displaySession.unpriced_models}
                ratesCheckedOn={displaySession.rates_checked_on}
                onExport={messages.length > 0 ? handleExport : undefined}
                onToggleJson={
                  messages.length > 0 ? () => setShowRawJson((v) => !v) : undefined
                }
                showJson={showRawJson}
                onSwitchToTerminal={() => switchAgentView('terminal')}
              />
            </div>
          </div>

          {agentView === 'files' && (
            <FileNavigator messages={messages} sessionId={sessionId} />
          )}
        </div>

        <RightDrawer
          open={rightOpen}
          session={displaySession}
          pullRequests={pullRequests}
          view={terminalView}
          onViewChange={setTerminalView}
          hideViewToggle={agentView === 'chat' || agentView === 'files'}
        />
      </div>

      <StatusBar status={ended ? 'ended' : status} sessionId={sessionId} />
    </div>
  );
}
