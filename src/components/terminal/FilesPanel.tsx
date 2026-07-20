import type { FC } from 'react';
import type { FileEntry } from '../../lib/terminal-types';

interface FilesPanelProps {
  files: FileEntry[];
  branch: string | null;
  visible: boolean;
}

export const FilesPanel: FC<FilesPanelProps> = ({ files, branch, visible }) => {
  if (!visible) return null;

  const staged = files.filter((f) => f.staged);
  const unstaged = files.filter((f) => !f.staged);

  return (
    <div className="files-panel">
      {files.length === 0 && (
        <div style={{ color: 'var(--faint)', padding: 8, fontSize: 12 }}>
          No file changes detected
        </div>
      )}

      {staged.length > 0 && (
        <>
          <div className="section-label">Staged</div>
          {staged.map((f, i) => (
            <FileRow key={i} file={f} />
          ))}
        </>
      )}

      {unstaged.length > 0 && (
        <>
          <div className="section-label">Unstaged</div>
          {unstaged.map((f, i) => (
            <FileRow key={i} file={f} />
          ))}
        </>
      )}

      {files.length > 0 && (
        <div className="commit-area">
          <textarea placeholder="Commit message…" rows={3} />
          <div className="commit-bar">
            <span className="branch mono">{branch || '—'}</span>
            <button className="primary">Commit & Push</button>
            <button>Create PR</button>
          </div>
        </div>
      )}
    </div>
  );
};

const FileRow: FC<{ file: FileEntry }> = ({ file }) => {
  return (
    <div className="file-row">
      <span className="name">{file.name}</span>
      {file.adds > 0 && <span className="adds">+{file.adds}</span>}
      {file.dels > 0 && <span className="dels">−{file.dels}</span>}
      {file.staged && <span className="staged">staged</span>}
    </div>
  );
};
