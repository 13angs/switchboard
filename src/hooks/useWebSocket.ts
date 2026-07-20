import { useRef, useEffect, useCallback, useState } from 'react';

export type ReadyState = 'CONNECTING' | 'OPEN' | 'CLOSING' | 'CLOSED';

interface UseWebSocketOpts {
  url: string;
  enabled?: boolean;
  onMessage: (data: ArrayBuffer | string) => void;
  onOpen?: () => void;
  onClose?: (code: number) => void;
  onError?: () => void;
  /** Binary type for incoming messages (default: 'arraybuffer'). */
  binaryType?: BinaryType;
}

export function useWebSocket({
  url,
  enabled = true,
  onMessage,
  onOpen,
  onClose,
  onError,
  binaryType = 'arraybuffer',
}: UseWebSocketOpts) {
  const wsRef = useRef<WebSocket | null>(null);
  const [readyState, setReadyState] = useState<ReadyState>('CONNECTING');
  const onMessageRef = useRef(onMessage);
  const onOpenRef = useRef(onOpen);
  const onCloseRef = useRef(onClose);
  const onErrorRef = useRef(onError);

  // Keep callbacks fresh
  onMessageRef.current = onMessage;
  onOpenRef.current = onOpen;
  onCloseRef.current = onClose;
  onErrorRef.current = onError;

  const send = useCallback((data: string | ArrayBuffer | Uint8Array) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  const close = useCallback(() => {
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

    const ws = new WebSocket(url);
    ws.binaryType = binaryType;
    wsRef.current = ws;

    ws.onopen = () => {
      setReadyState('OPEN');
      onOpenRef.current?.();
    };

    ws.onmessage = (ev: MessageEvent) => {
      onMessageRef.current(ev.data as ArrayBuffer | string);
    };

    ws.onclose = (ev: CloseEvent) => {
      setReadyState('CLOSED');
      onCloseRef.current?.(ev.code);
    };

    ws.onerror = () => {
      setReadyState('CLOSED');
      onErrorRef.current?.();
    };

    return () => {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };
  }, [url, enabled, binaryType]);

  return { send, close, readyState };
}
