import { useRef, useEffect, useCallback } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';

// Terminal theme — matches design tokens
const TERM_THEME = {
  background: '#0c0e12',
  foreground: '#7bd88f',
  cursor: '#8b7dff',
  selectionBackground: '#2a3f6e',
  black: '#1e222b',
  red: '#ef6b6b',
  green: '#74b98a',
  yellow: '#f59f4c',
  blue: '#5b8cff',
  magenta: '#c084fc',
  cyan: '#22d3ee',
  white: '#f4f5f7',
};

interface UseXtermOpts {
  containerRef: React.RefObject<HTMLDivElement | null>;
  onData: (data: string) => void;
  onResize?: (cols: number, rows: number) => void;
  fontSize?: number;
  fontFamily?: string;
}

export function useXterm({
  containerRef,
  onData,
  onResize,
  fontSize = 13,
  fontFamily = 'ui-monospace, "Cascadia Code", "Fira Code", monospace',
}: UseXtermOpts) {
  const termRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const onDataRef = useRef(onData);
  const onResizeRef = useRef(onResize);
  onDataRef.current = onData;
  onResizeRef.current = onResize;

  const fit = useCallback(() => {
    const container = containerRef.current;
    if (!container || !fitAddonRef.current || container.clientWidth === 0 || container.clientHeight === 0) {
      return;
    }
    try {
      fitAddonRef.current.fit();
    } catch {
      /* ignore */
    }
  }, [containerRef]);

  /** Write raw bytes to the terminal (PTY output). */
  const write = useCallback((data: Uint8Array | string) => {
    termRef.current?.write(data);
  }, []);

  /** Write a control/status message (colored, prefixed). */
  const writeln = useCallback((text: string, cls?: string) => {
    // xterm.js doesn't support CSS classes — use ANSI escapes for color
    const t = termRef.current;
    if (!t) return;
    if (cls === 'info') t.writeln(`\x1b[2m${text}\x1b[0m`);
    else if (cls === 'warn') t.writeln(`\x1b[33m${text}\x1b[0m`);
    else if (cls === 'err') t.writeln(`\x1b[31m${text}\x1b[0m`);
    else if (cls === 'muted') t.writeln(`\x1b[2m${text}\x1b[0m`);
    else t.writeln(text);
  }, []);

  const dispose = useCallback(() => {
    resizeObserverRef.current?.disconnect();
    try {
      termRef.current?.dispose();
    } catch {
      /* ignore */
    }
    termRef.current = null;
    fitAddonRef.current = null;
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize,
      fontFamily,
      theme: TERM_THEME,
      allowProposedApi: false,
    });
    termRef.current = term;

    const fitAddon = new FitAddon();
    fitAddonRef.current = fitAddon;
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());

    // ResizeObserver for container size changes
    const ro = new ResizeObserver(() => fit());
    ro.observe(container);
    resizeObserverRef.current = ro;

    term.open(container);

    // Double rAF for post-layout fit
    requestAnimationFrame(() => requestAnimationFrame(() => fit()));

    // Data from xterm → parent
    term.onData((data) => onDataRef.current(data));

    // Resize → parent
    term.onResize(({ cols, rows }) => {
      onResizeRef.current?.(cols, rows);
    });

    return () => {
      ro.disconnect();
      try {
        term.dispose();
      } catch {
        /* ignore */
      }
    };
  }, [containerRef, fontSize, fontFamily, fit]);

  return { write, writeln, fit, dispose };
}
