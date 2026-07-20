import { useRef, useEffect, type FC } from 'react';
import { useXterm } from '../../hooks/useXterm';

interface TerminalBodyProps {
  onData: (data: string) => void;
  onResize: (cols: number, rows: number) => void;
  /** Callback to get the write/writeln/dispose methods for external use. */
  onMount: (api: {
    write: (data: Uint8Array | string) => void;
    writeln: (text: string, cls?: string) => void;
    fit: () => void;
    dispose: () => void;
  }) => void;
  ended: boolean;
}

export const TerminalBody: FC<TerminalBodyProps> = ({
  onData,
  onResize,
  onMount,
  ended,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const { write, writeln, fit, dispose } = useXterm({
    containerRef,
    onData,
    onResize,
  });

  useEffect(() => {
    onMount({ write, writeln, fit, dispose });
    return () => {
      dispose();
    };
  }, [write, writeln, fit, dispose, onMount]);

  return (
    <div className="terminal-body">
      <div ref={containerRef} className="xterm-container" />
      {ended && (
        <div className="ended-overlay">
          <div className="ended-card">
            <h4>Session ended</h4>
            <p>
              The PTY process exited. Output below is preserved.
              Start a new session from the board.
            </p>
          </div>
        </div>
      )}
    </div>
  );
};
