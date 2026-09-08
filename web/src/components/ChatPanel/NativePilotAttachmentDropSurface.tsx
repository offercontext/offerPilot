import { useState, type DragEvent, type ReactNode } from 'react';
import type { PilotContextAttachment, PilotAttachmentKind } from '@/types/chat';
import styles from './ChatPanel.module.css';

const NATIVE_ATTACHMENT_TYPE = 'application/x-offerpilot-context-attachment';

interface Props {
  children: ReactNode;
  disabled?: boolean;
  onNativeDrop: (attachment: PilotContextAttachment) => void;
}

function hasNativeAttachmentType(event: DragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes(NATIVE_ATTACHMENT_TYPE);
}

function parseNativeAttachment(value: string): PilotContextAttachment | null {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object') return null;
    const { kind, id, label } = parsed as Record<string, unknown>;
    const validKind: PilotAttachmentKind[] = ['application', 'offer', 'resume'];
    if (!validKind.includes(kind as PilotAttachmentKind) || typeof id !== 'string' || !id.trim() || typeof label !== 'string' || !label.trim()) {
      return null;
    }
    return { kind: kind as PilotAttachmentKind, id, label };
  } catch {
    return null;
  }
}

export default function NativePilotAttachmentDropSurface({ children, disabled, onNativeDrop }: Props) {
  const [dragging, setDragging] = useState(false);

  return (
    <div
      className={`${styles.nativePilotDropSurface} ${dragging ? styles.nativePilotDropSurfaceDragging : ''}`}
      data-testid="pilot-native-drop-surface"
      data-dragging={dragging || undefined}
      onDragEnter={(event) => {
        if (disabled || !hasNativeAttachmentType(event)) return;
        const payload = event.dataTransfer.getData(NATIVE_ATTACHMENT_TYPE);
        if (payload && !parseNativeAttachment(payload)) return;
        setDragging(true);
      }}
      onDragOver={(event) => {
        if (disabled || !hasNativeAttachmentType(event)) return;
        const payload = event.dataTransfer.getData(NATIVE_ATTACHMENT_TYPE);
        if (payload && !parseNativeAttachment(payload)) {
          setDragging(false);
          return;
        }
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={(event) => {
        setDragging(false);
        if (disabled || event.defaultPrevented || !hasNativeAttachmentType(event)) return;
        const attachment = parseNativeAttachment(event.dataTransfer.getData(NATIVE_ATTACHMENT_TYPE));
        if (!attachment) return;
        event.preventDefault();
        onNativeDrop(attachment);
      }}
    >
      {children}
      {dragging ? <div className={styles.nativePilotDropHint}>松开以加入 Pilot 上下文</div> : null}
    </div>
  );
}
