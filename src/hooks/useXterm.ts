import { useRef, useEffect, useCallback } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';

/** ADR-0027 §SD5 — trailing debounce for observer-driven fits. */
const FIT_DEBOUNCE_MS = 50;

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
  const fitTimerRef = useRef<number | null>(null);
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

  /**
   * ADR-0027 §SD5 — debounce the observer-driven fit. Device rotation and the
   * soft keyboard produce a burst of ResizeObserver callbacks; fitting on every
   * one moves the viewport ahead of the PTY it has to agree with, and cells
   * rendered against a size the backend has not applied yet come out corrupted.
   * Explicit `fit()` calls (view switch, reconnect) stay immediate.
   */
  const scheduleFit = useCallback(() => {
    if (fitTimerRef.current !== null) {
      window.clearTimeout(fitTimerRef.current);
    }
    fitTimerRef.current = window.setTimeout(() => {
      fitTimerRef.current = null;
      fit();
    }, FIT_DEBOUNCE_MS);
  }, [fit]);

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
    if (fitTimerRef.current !== null) {
      window.clearTimeout(fitTimerRef.current);
      fitTimerRef.current = null;
    }
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

    // ResizeObserver for container size changes (debounced — ADR-0027 §SD5)
    const ro = new ResizeObserver(() => scheduleFit());
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
      if (fitTimerRef.current !== null) {
        window.clearTimeout(fitTimerRef.current);
        fitTimerRef.current = null;
      }
      try {
        term.dispose();
      } catch {
        /* ignore */
      }
    };
  }, [containerRef, fontSize, fontFamily, fit, scheduleFit]);

  return { write, writeln, fit, dispose };
}
