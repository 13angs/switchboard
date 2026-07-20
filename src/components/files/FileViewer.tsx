import { useState, useEffect, type FC } from 'react';
import { fetchFileContent } from '../../lib/api';
import type { FileRef } from '../../lib/file-refs';

interface FileViewerProps {
  file: FileRef | null;
  sessionId: string | null;
}

const OP_LABEL: Record<string, string> = { read: 'Read', edit: 'Edited', write: 'Written' };

/** File content viewer — right panel: line numbers + syntax-highlighted code. */
export const FileViewer: FC<FileViewerProps> = ({ file, sessionId }) => {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!file || !sessionId) {
      setContent(null);
      setError('');
      return;
    }
    let active = true;
    setContent(null);
    setError('');
    setLoading(true);
    fetchFileContent(sessionId, file.path)
      .then((data) => {
        if (active) {
          setContent(data.content);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : 'Failed to load file');
          setLoading(false);
        }
      });
    return () => { active = false; };
  }, [file?.path, sessionId]);

  if (!file) {
    return (
      <div className="fv-empty">
        <div className="fv-empty-icon">📂</div>
        <div className="fv-empty-text">Select a file to view its contents</div>
        <div className="fv-empty-sub">
          Click any file from the list on the left to see what the agent read or changed.
        </div>
      </div>
    );
  }

  const opLabel = OP_LABEL[file.op] || file.toolName;

  return (
    <div className="file-viewer">
      <div className="fv-head">
        <span className="fv-path mono" title={file.path}>{file.path}</span>
        <span className={`fv-op fv-op-${file.op}`}>{opLabel}</span>
      </div>
      <div className="fv-body">
        {loading && (
          <div className="fv-empty">
            <div className="fv-empty-icon">⏳</div>
            <div className="fv-empty-text">Loading…</div>
          </div>
        )}
        {error && (
          <div className="fv-empty">
            <div className="fv-empty-icon">⚠️</div>
            <div className="fv-empty-text">Could not load file</div>
            <div className="fv-empty-sub">{error}</div>
          </div>
        )}
        {content != null && !loading && !error && (
          <CodeView content={content} path={file.path} />
        )}
      </div>
    </div>
  );
};

/** Line-numbered code display with thin syntax highlighting. */
const CodeView: FC<{ content: string; path: string }> = ({ content, path }) => {
  const ext = path.match(/\.([a-z0-9]+)$/i)?.[1] ?? '';
  const lines = content.split('\n');

  return (
    <div className="code-view">
      <div className="code-linenos mono">
        {lines.map((_, i) => (
          <span key={i}>{i + 1}</span>
        ))}
      </div>
      <div className="code-body">
        <pre className="mono">
          {lines.map((line, i) => (
            <span
              key={i}
              className="code-line"
              dangerouslySetInnerHTML={{ __html: highlightLine(line, ext) || '&nbsp;' }}
            />
          ))}
        </pre>
      </div>
    </div>
  );
};

// ── Thin syntax highlighting ──

const TS_TOKENS: [RegExp, string][] = [
  [/\/\*\*?[\s\S]*?\*\//g, 'cm'],
  [/\/\/.*/g, 'cm'],
  [/(['`])(?:\\.|(?!\1).)*?\1/g, 'str'],
  [/\b(import|export|from|const|let|var|function|return|if|else|for|of|in|async|await|type|interface|extends|class|new|throw|try|catch|typeof|instanceof|void|null|undefined|true|false)\b/g, 'kw'],
  [/\b([A-Z][a-zA-Z0-9_$]*)\b/g, 'tp'],
  [/\b([a-z_$][a-zA-Z0-9_$]*(?=\s*\())/g, 'fn'],
  [/\b(\d+(?:\.\d+)?)\b/g, 'num'],
];

const MD_TOKENS: [RegExp, string][] = [
  [/^# (.+)$/gm, '<span class="md-h1">#$1</span>'],
  [/^## (.+)$/gm, '<span class="md-h2">##$1</span>'],
  [/^### (.+)$/gm, '<span class="md-h3">###$1</span>'],
  [/`([^`]+)`/g, '<code>$1</code>'],
];

function highlightLine(line: string, ext: string): string {
  let html = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  if (ext === 'md') {
    for (const [pat, tmpl] of MD_TOKENS) html = html.replace(pat, tmpl);
  }
  if (ext === 'ts' || ext === 'tsx' || ext === 'js' || ext === 'py') {
    for (const [pat, cls] of TS_TOKENS) html = html.replace(pat, (m) => `<span class="${cls}">${m}</span>`);
  }
  return html;
}
