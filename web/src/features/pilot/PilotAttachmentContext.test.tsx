import { describe, expect, it } from 'vitest';
import {
  emptyPilotAttachmentState,
  pilotAttachmentStateReducer,
  type PilotAttachmentState,
} from './PilotAttachmentContext';

const application = { kind: 'application' as const, id: '7', label: 'ByteDance · Backend' };
const offer = { kind: 'offer' as const, id: '8', label: 'ByteDance · Offer' };
const resume = { kind: 'resume' as const, id: '9', label: 'Backend resume' };

function reduce(
  state: PilotAttachmentState,
  ...actions: Parameters<typeof pilotAttachmentStateReducer>[1][]
): PilotAttachmentState {
  return actions.reduce(pilotAttachmentStateReducer, state);
}

describe('PilotAttachmentContext attachment draft store', () => {
  it('restores attachments when switching back to an existing conversation', () => {
    const withConversationSeven = reduce(emptyPilotAttachmentState(),
      { type: 'add', key: 'conversation:7', attachment: application },
    );
    const onConversationEight = reduce(withConversationSeven, { type: 'clear', key: 'conversation:8' });
    const backOnConversationSeven = onConversationEight;

    expect(onConversationEight.drafts['conversation:8']?.attachments ?? []).toEqual([]);
    expect(backOnConversationSeven.drafts['conversation:7']?.attachments).toEqual([application]);
  });

  it('isolates a fresh request draft from persisted conversations', () => {
    const state = reduce(emptyPilotAttachmentState(),
      { type: 'add', key: 'conversation:7', attachment: application },
      { type: 'add', key: 'new:15', attachment: offer },
    );

    expect(state.drafts['conversation:7']?.attachments).toEqual([application]);
    expect(state.drafts['new:15']?.attachments).toEqual([offer]);
  });

  it('removes and clears only the active draft', () => {
    const populated = reduce(emptyPilotAttachmentState(),
      { type: 'add', key: 'conversation:7', attachment: application },
      { type: 'add', key: 'conversation:7', attachment: resume },
      { type: 'add', key: 'conversation:8', attachment: offer },
    );
    const removed = reduce(populated,
      { type: 'remove', key: 'conversation:7', attachmentOrKey: 'application:7' },
    );
    const cleared = reduce(removed, { type: 'clear', key: 'conversation:7' });

    expect(removed.drafts['conversation:7']?.attachments).toEqual([resume]);
    expect(cleared.drafts['conversation:7']?.attachments).toEqual([]);
    expect(cleared.drafts['conversation:8']?.attachments).toEqual([offer]);
  });

  it('keeps the attachment-limit notice on the active draft and ignores adds without a key', () => {
    const attachments = Array.from({ length: 6 }, (_, index) => ({
      kind: 'resume' as const,
      id: String(index + 1),
      label: `Resume ${index + 1}`,
    }));
    const limited = reduce(emptyPilotAttachmentState(),
      ...attachments.map((attachment) => ({ type: 'add' as const, key: 'new:15' as const, attachment })),
    );

    expect(limited.drafts['new:15']?.attachments).toHaveLength(5);
    expect(limited.drafts['new:15']?.message).toBeTruthy();
  });

  it('clears only the keyed draft selected by a successful send', () => {
    const normalNewDraft = reduce(emptyPilotAttachmentState(),
      { type: 'add', key: 'new:draft-1', attachment: application },
      { type: 'add', key: 'new:draft-2', attachment: offer },
    );
    const successfulSend = reduce(normalNewDraft, { type: 'clear', key: 'new:draft-1' });
    const failedSend = normalNewDraft;
    const abortedSend = normalNewDraft;

    expect(successfulSend.drafts['new:draft-1']?.attachments).toEqual([]);
    expect(successfulSend.drafts['new:draft-2']?.attachments).toEqual([offer]);
    expect(failedSend.drafts['new:draft-1']?.attachments).toEqual([application]);
    expect(abortedSend.drafts['new:draft-1']?.attachments).toEqual([application]);
  });
});
