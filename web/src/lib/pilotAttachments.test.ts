import { describe, expect, it } from 'vitest';
import type { PilotContextAttachment } from '@/types/chat';
import {
  PILOT_ATTACHMENT_LIMIT,
  addPilotAttachment,
  emptyPilotAttachmentDraft,
  pilotAttachmentKey,
  pilotQuickQuestions,
  removePilotAttachment,
} from './pilotAttachments';

const application: PilotContextAttachment = {
  kind: 'application',
  id: 'application-1',
  label: '星海科技 · 前端工程师',
};

const offer: PilotContextAttachment = {
  kind: 'offer',
  id: 'offer-1',
  label: '星海科技 Offer',
};

const resume: PilotContextAttachment = {
  kind: 'resume',
  id: 'resume-1',
  label: '前端工程师简历',
};

describe('pilot attachments', () => {
  it('creates independent empty attachment drafts', () => {
    const first = emptyPilotAttachmentDraft();
    first.attachments.push(application);

    expect(emptyPilotAttachmentDraft().attachments).toEqual([]);
  });

  it('keys attachments by kind and id', () => {
    expect(pilotAttachmentKey(application)).toBe('application:application-1');
  });

  it('deduplicates matching attachments while preserving first-added order', () => {
    const first = addPilotAttachment(emptyPilotAttachmentDraft(), application);
    const second = addPilotAttachment(first, offer);
    const duplicate = addPilotAttachment(second, { ...application, label: '已改名的岗位' });

    expect(duplicate.attachments).toEqual([application, offer]);
    expect(duplicate.message).toBeUndefined();
  });

  it('returns a visible message instead of silently dropping an attachment over the limit', () => {
    let draft = emptyPilotAttachmentDraft();
    for (let index = 0; index < PILOT_ATTACHMENT_LIMIT; index += 1) {
      draft = addPilotAttachment(draft, {
        kind: 'application',
        id: String(index),
        label: `岗位 ${index}`,
      });
    }

    const result = addPilotAttachment(draft, resume);

    expect(result.attachments).toHaveLength(PILOT_ATTACHMENT_LIMIT);
    expect(result.message).toBe('最多添加 5 个上下文对象');
  });

  it('removes the requested attachment by kind and id', () => {
    const draft = addPilotAttachment(addPilotAttachment(emptyPilotAttachmentDraft(), application), resume);

    expect(removePilotAttachment(draft, application)).toEqual({ attachments: [resume] });
  });

  it('suggests the required deterministic questions for an application and resume', () => {
    const draft = addPilotAttachment(addPilotAttachment(emptyPilotAttachmentDraft(), application), resume);

    expect(pilotQuickQuestions(draft.attachments)).toEqual([
      '分析简历与岗位的匹配差距',
      '给出最值得修改的三处',
      '生成自我介绍提纲',
    ]);
  });
});
