import { useDroppable } from '@dnd-kit/core';
import type { ReactNode } from 'react';
import type { PilotContextAttachment } from '@/types/chat';
import NativePilotAttachmentDropSurface from '@/components/ChatPanel/NativePilotAttachmentDropSurface';
import { PILOT_CONTEXT_DROP_ID } from './applicationLifecycle';
import styles from './PilotContextDropTarget.module.css';

interface PilotContextDropTargetProps {
  children: ReactNode;
  disabled?: boolean;
  onNativeDrop?: (attachment: PilotContextAttachment) => void;
}

export default function PilotContextDropTarget({
  children,
  disabled,
  onNativeDrop,
}: PilotContextDropTargetProps) {
  const { isOver, setNodeRef } = useDroppable({ id: PILOT_CONTEXT_DROP_ID });

  return (
    <div
      ref={setNodeRef}
      className={`${styles.target} ${isOver ? styles.targetOver : ''}`}
      data-testid="pilot-context-drop"
    >
      {onNativeDrop ? (
        <NativePilotAttachmentDropSurface disabled={disabled} onNativeDrop={onNativeDrop}>
          {children}
        </NativePilotAttachmentDropSurface>
      ) : children}
      {isOver ? <div className={styles.dropHint}>松开以加入 Pilot 上下文</div> : null}
    </div>
  );
}
