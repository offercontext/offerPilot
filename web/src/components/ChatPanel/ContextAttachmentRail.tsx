import { CloseOutlined, InboxOutlined } from '@ant-design/icons';
import { useState, type DragEvent } from 'react';
import type { PilotContextAttachment, PilotAttachmentKind } from '@/types/chat';
import styles from './ChatPanel.module.css';

const NATIVE_ATTACHMENT_TYPE = 'application/x-offerpilot-context-attachment';

const KIND_LABELS: Record<PilotAttachmentKind, string> = {
  application: '投递',
  offer: 'Offer',
  resume: '简历',
};

interface Props {
  attachments: PilotContextAttachment[];
  disabled?: boolean;
  onRemove: (attachment: PilotContextAttachment) => void;
  onNativeDrop?: (attachment: PilotContextAttachment) => void;
}

function hasNativeAttachmentType(event: DragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes(NATIVE_ATTACHMENT_TYPE);
}

function parseNativeAttachment(value: string): PilotContextAttachment | null {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object') return null;
    const { kind, id, label } = parsed as Record<string, unknown>;
    if (
      (kind !== 'application' && kind !== 'offer' && kind !== 'resume') ||
      typeof id !== 'string' ||
      !id.trim() ||
      typeof label !== 'string' ||
      !label.trim()
    ) {
      return null;
    }
    return { kind, id, label };
  } catch {
    return null;
  }
}

export default function ContextAttachmentRail({
  attachments,
  disabled,
  onRemove,
  onNativeDrop,
}: Props) {
  const [dragging, setDragging] = useState(false);

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    setDragging(false);
    if (disabled || !hasNativeAttachmentType(event)) return;
    const attachment = parseNativeAttachment(event.dataTransfer.getData(NATIVE_ATTACHMENT_TYPE));
    if (!attachment) return;
    event.preventDefault();
    onNativeDrop?.(attachment);
  }

  return (
    <div
      className={`${styles.contextAttachmentRail} ${dragging ? styles.contextAttachmentRailDragging : ''}`}
      data-testid="context-attachment-rail"
      onDragEnter={(event) => {
        if (!disabled && hasNativeAttachmentType(event)) setDragging(true);
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
      onDrop={handleDrop}
    >
      {attachments.length === 0 ? (
        <div className={styles.contextAttachmentEmpty}>
          <InboxOutlined aria-hidden="true" />
          <span>拖入投递、Offer 或简历，作为本次对话的参考</span>
        </div>
      ) : (
        <div className={styles.contextAttachmentList} aria-label="当前上下文附件">
          {attachments.map((attachment) => (
            <div key={`${attachment.kind}:${attachment.id}`} className={styles.contextAttachmentChip}>
              <span className={styles.contextAttachmentKind}>{KIND_LABELS[attachment.kind]}</span>
              <span className={styles.contextAttachmentLabel} title={attachment.label}>
                {attachment.label}
              </span>
              <button
                type="button"
                className={styles.contextAttachmentRemove}
                aria-label={`Remove ${attachment.label} from context`}
                title={`移除${attachment.label}`}
                disabled={disabled}
                onClick={() => onRemove(attachment)}
              >
                <CloseOutlined aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
