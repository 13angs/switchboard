import { useState, useMemo, useCallback, type FC } from 'react';
import { extractFileRefs, type FileRef, type GroupMode } from '../../lib/file-refs';
import type { RichMessage } from '../../lib/types';
import { FileList } from './FileList';
import { FileViewer } from './FileViewer';

interface FileNavigatorProps {
  messages: RichMessage[];
  sessionId: string | null;
}

/** Agent File Navigator — split panel: file list + content viewer.
 *  Shown at /agent?view=files. */
export const FileNavigator: FC<FileNavigatorProps> = ({ messages, sessionId }) => {
  const [groupMode, setGroupMode] = useState<GroupMode>('operation');
  const [selectedFile, setSelectedFile] = useState<FileRef | null>(null);

  const fileRefs = useMemo(() => extractFileRefs(messages), [messages]);

  const handleSelect = useCallback((file: FileRef) => {
    setSelectedFile(file);
  }, []);

  return (
    <div className="files-nav">
      <FileList
        fileRefs={fileRefs}
        groupMode={groupMode}
        onGroupMode={setGroupMode}
        selectedPath={selectedFile?.path ?? null}
        onSelect={handleSelect}
      />
      <FileViewer file={selectedFile} sessionId={sessionId} />
    </div>
  );
};
