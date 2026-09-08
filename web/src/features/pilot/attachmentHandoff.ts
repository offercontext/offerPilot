import type { PilotAttachmentConversationKey } from './PilotAttachmentContext';

export function retainPilotAttachmentKey(
  currentKey?: PilotAttachmentConversationKey,
  reportedKey?: PilotAttachmentConversationKey,
): PilotAttachmentConversationKey | undefined {
  return reportedKey ?? currentKey;
}
