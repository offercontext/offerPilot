import type { DragEvent } from 'react';
import type { PilotContextAttachment } from '@/types/chat';

export const NATIVE_PILOT_ATTACHMENT_TYPE = 'application/x-offerpilot-context-attachment';

export interface PilotAttachmentDragBinding {
  draggable: true;
  'aria-label': string;
  onDragStart: (event: DragEvent<HTMLElement>) => void;
}

export function createPilotAttachmentDragBinding(
  attachment: PilotContextAttachment,
): PilotAttachmentDragBinding {
  return {
    draggable: true,
    'aria-label': `Drag ${attachment.label} to Pilot context`,
    onDragStart: (event) => {
      event.dataTransfer.setData(NATIVE_PILOT_ATTACHMENT_TYPE, JSON.stringify(attachment));
      event.dataTransfer.effectAllowed = 'copy';
    },
  };
}
