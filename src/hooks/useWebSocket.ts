import { useRef, useEffect, useCallback, useState } from 'react';

export type ReadyState =
  | 'CONNECTING'
  | 'OPEN'
  | 'CLOSING'
  | 'CLOSED'
  | 'RECONNECTING';

/**
 * ADR-0027 §SD2 — heartbeat cadence. This only ever runs while the page is
 * visible, so the interval is chosen for "how long may a half-open socket go
 * unnoticed while someone is looking at it", not for holding a NAT binding
 * open — the server binds loopback and there is no NAT or proxy in the path.
 */
const HEARTBEAT_INTERVAL_MS = 25_000;
const PONG_TIMEOUT_MS = 10_000;

/** ADR-0027 §SD1 — jittered exponential backoff. */
const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 15_000;
const BACKOFF_JITTER = 0.5;

interface UseWebSocketOpts {
  url: string;
  enabled?: boolean;
  onMessage: (data: ArrayBuffer | string) => void;
  onOpen?: () => void;
  onClose?: (code: number) => void;
  onError?: () => void;
  /** Binary type for incoming messages (default: 'arraybuffer'). */
  binaryType?: BinaryType;
  /**
   * Stop reconnecting. Set once the session is over (an `exit` control frame),
   * as opposed to merely disconnected — a dead child process is not something
   * to retry into.
   */
  stopped?: boolean;
}

export function useWebSocket({
  url,
  enabled = true,
  onMessage,
  onOpen,
  onClose,
  onError,
  binaryType = 'arraybuffer',
  stopped = false,
}: UseWebSocketOpts) {
  const wsRef = useRef<WebSocket | null>(null);
  const [readyState, setReadyState] = useState<ReadyState>('CONNECTING');
  const onMessageRef = useRef(onMessage);
  const onOpenRef = useRef(onOpen);
  const onCloseRef = useRef(onClose);
  const onErrorRef = useRef(onError);
  const stoppedRef = useRef(stopped);
  /** Set by close() — a deliberate close must not trigger a reconnect. */
  const deliberateRef = useRef(false);

  // Keep callbacks fresh
  onMessageRef.current = onMessage;
  onOpenRef.current = onOpen;
  onCloseRef.current = onClose;
  onErrorRef.current = onError;
  stoppedRef.current = stopped;

  const send = useCallback((data: string | ArrayBuffer | Uint8Array) => {
    // ADR-0027 §SD4 — input typed while disconnected is dropped, never queued.
    // Terminal input is positional: replaying a half-typed command into a
    // prompt that has since moved runs something the user never asked for.
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  const close = useCallback(() => {
    deliberateRef.current = true;
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* ignore */
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setReadyState('CLOSED');
      return;
    }

    deliberateRef.current = false;

    let disposed = false;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let reconnectTimer: number | null = null;
    let heartbeatTimer: number | null = null;
    let pongTimer: number | null = null;

    const halted = () => disposed || stoppedRef.current || deliberateRef.current;

    const stopHeartbeat = () => {
      if (heartbeatTimer !== null) {
        window.clearInterval(heartbeatTimer);
        heartbeatTimer = null;
      }
      if (pongTimer !== null) {
        window.clearTimeout(pongTimer);
        pongTimer = null;
      }
    };

    /**
     * Send one application-level ping and arm the watchdog. Browsers cannot
     * send RFC 6455 ping frames from JavaScript, so this is the only probe
     * available on the client side.
     */
    const beat = () => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      try {
        ws.send(JSON.stringify({ type: 'ping' }));
      } catch {
        return;
      }
      if (pongTimer === null) {
        pongTimer = window.setTimeout(() => {
          pongTimer = null;
          // No reply: the socket is half-open. Closing it routes through
          // onclose, which schedules the reconnect.
          try {
            ws?.close();
          } catch {
            /* ignore */
          }
        }, PONG_TIMEOUT_MS);
      }
    };

    /**
     * ADR-0027 §SD2 — never run the heartbeat in a hidden tab. Chrome throttles
     * a hidden tab's timers to roughly one wake-up per minute, so a heartbeat
     * left running there fires late and reports a perfectly healthy socket as
     * dead — severing a connection that was fine, and burning battery to do it.
     */
    const startHeartbeat = () => {
      stopHeartbeat();
      if (document.visibilityState !== 'visible') return;
      heartbeatTimer = window.setInterval(beat, HEARTBEAT_INTERVAL_MS);
    };

    const scheduleReconnect = () => {
      if (halted() || reconnectTimer !== null) return;
      const backoff = Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_MAX_MS);
      const delay = backoff * (1 + Math.random() * BACKOFF_JITTER);
      attempt += 1;
      setReadyState('RECONNECTING');
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, delay);
    };

    const connect = () => {
      if (halted()) return;
      const sock = new WebSocket(url);
      sock.binaryType = binaryType;
      ws = sock;
      wsRef.current = sock;
      setReadyState(attempt === 0 ? 'CONNECTING' : 'RECONNECTING');

      sock.onopen = () => {
        if (disposed || sock !== ws) return;
        attempt = 0;
        setReadyState('OPEN');
        startHeartbeat();
        onOpenRef.current?.();
      };

      sock.onmessage = (ev: MessageEvent) => {
        // Swallow our own heartbeat replies; the consumer never sees them.
        if (typeof ev.data === 'string' && ev.data.includes('"pong"')) {
          try {
            if (JSON.parse(ev.data)?.type === 'pong') {
              if (pongTimer !== null) {
                window.clearTimeout(pongTimer);
                pongTimer = null;
              }
              return;
            }
          } catch {
            /* not ours — fall through to the consumer */
          }
        }
        onMessageRef.current(ev.data as ArrayBuffer | string);
      };

      sock.onclose = (ev: CloseEvent) => {
        if (sock !== ws) return; // a superseded socket closing late
        stopHeartbeat();
        onCloseRef.current?.(ev.code);
        if (halted()) {
          setReadyState('CLOSED');
          return;
        }
        scheduleReconnect();
      };

      sock.onerror = () => {
        if (sock !== ws) return;
        onErrorRef.current?.();
        // `close` follows an error; the reconnect is scheduled there.
      };
    };

    /**
     * ADR-0027 §SD1 — the returning-to-the-tab path. Chrome closes a BFCached
     * page's socket and fires close/error on restore *specifically* so this can
     * run, and a plain tab switch can leave a half-open socket whose readyState
     * still reads OPEN. Either way the user is looking at the terminal now, so
     * probe immediately instead of waiting out the backoff.
     */
    const revalidate = () => {
      if (halted()) return;
      if (document.visibilityState !== 'visible') {
        stopHeartbeat();
        return;
      }
      if (
        !ws ||
        ws.readyState === WebSocket.CLOSED ||
        ws.readyState === WebSocket.CLOSING
      ) {
        if (reconnectTimer !== null) {
          window.clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        attempt = 0;
        connect();
        return;
      }
      startHeartbeat();
      beat();
    };

    connect();
    document.addEventListener('visibilitychange', revalidate);
    window.addEventListener('pageshow', revalidate);

    return () => {
      disposed = true;
      document.removeEventListener('visibilitychange', revalidate);
      window.removeEventListener('pageshow', revalidate);
      stopHeartbeat();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
  }, [url, enabled, binaryType]);

  return { send, close, readyState };
}
