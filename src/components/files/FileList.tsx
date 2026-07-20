import { useMemo, type FC } from 'react';
import { basename, dirname, ago } from '../../lib/format';
import { groupFileRefs, type FileRef, type GroupMode } from '../../lib/file-refs';

interface FileListProps {
  fileRefs: FileRef[];
  groupMode: GroupMode;
  onGroupMode: (mode: GroupMode) => void;
  selectedPath: string | null;
  onSelect: (file: FileRef) => void;
}

const OP_ICON: Record<string, string> = { read: '📖', edit: '✏️', write: '📝' };

export const FileList: FC<FileListProps> = ({
  fileRefs,
  groupMode,
  onGroupMode,
  selectedPath,
  onSelect,
}) => {
  const groups = useMemo(
    () => groupFileRefs(fileRefs, groupMode),
    [fileRefs, groupMode]
  );

  return (
    <div className="file-list">
      <div className="fl-head">
        <span className="fl-title">Files Touched</span>
        <span className="fl-count mono">{fileRefs.length}</span>
        <select
          className="fl-group-select mono"
          value={groupMode}
          onChange={(e) => onGroupMode(e.target.value as GroupMode)}
        >
          <option value="operation">By Operation</option>
          <option value="directory">By Directory</option>
          <option value="time">By Time</option>
        </select>
      </div>
      <div className="fl-body">
        {groups.map((g) => (
          <div className="file-group" key={g.key}>
            <div className="fg-head">
              <span className="fg-icon">{g.icon}</span>
              <span className="fg-label">{g.label}</span>
              <span className="fg-count">{g.files.length}</span>
            </div>
            {g.files.map((f) => (
              <FileRow
                key={`${f.path}:${f.op}`}
                file={f}
                active={selectedPath === f.path}
                onClick={() => onSelect(f)}
              />
            ))}
          </div>
        ))}
        {fileRefs.length === 0 && (
          <div className="fl-empty">
            No files touched yet.<br />
            <span className="fl-empty-sub">
              Files appear as the agent reads, edits, or writes them.
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

const FileRow: FC<{ file: FileRef; active: boolean; onClick: () => void }> = ({
  file,
  active,
  onClick,
}) => {
  return (
    <div
      className={`file-row${active ? ' active' : ''}`}
      onClick={onClick}
      role="option"
      aria-selected={active}
    >
      <span className={`fr-dot fr-dot-${file.op}`} />
      <span className="fr-icon">{OP_ICON[file.op]}</span>
      <div className="fr-info">
        <div className="fr-name">{basename(file.path)}</div>
        <div className="fr-dir mono">{dirname(file.path)}</div>
      </div>
      <span className="fr-ts mono">{ago(file.ts)}</span>
    </div>
  );
};
