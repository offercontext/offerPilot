import { describe, expect, it } from 'vitest';
import type { Application } from '@/types/application';
import type { ApplicationOutcome, ApplicationSubmissionSnapshot } from '@/types/applicationOutcome';
import type { ScheduleEvent } from '@/types/event';
import {
  buildApplicationCommunicationDraft,
  isThankYouDraftAvailable,
} from './applicationCommunicationDraft';

const application = {
  id: 7,
  company_name: '云栖智能',
  position_name: '高级后端工程师',
  status: 'interview',
} as Application;

const snapshot = {
  id: 11,
  application_id: 7,
  resume_id: 2,
  resume_title: '筱哲 · 高级后端工程师版',
  jd_version_id: 3,
  jd_version_number: 2,
  material_kit_id: null,
  resume_snapshot: {},
  jd_snapshot: '负责高并发 API 设计。',
  material_snapshot: null,
  note: '',
  source_kind: 'ui',
  source_states: { resume: 'current', jd: 'current', material: 'missing' },
  submitted_at: '2026-08-12T09:00:00Z',
  created_at: '2026-08-12T09:00:00Z',
} satisfies ApplicationSubmissionSnapshot;

const interviewEvent = {
  id: 31,
  application_id: 7,
  event_type: 'interview',
  subtype: 'technical',
  tags: [],
  round: 2,
  scheduled_at: '2026-08-15T10:30:00+08:00',
  duration_minutes: 60,
  location: '',
  notes: '',
  status: 'scheduled',
  created_at: '2026-08-10T10:00:00Z',
} satisfies ScheduleEvent;

const interviewOutcome = {
  id: 41,
  application_id: 7,
  submission_snapshot_id: 11,
  application_event_id: 31,
  stage: 'interview',
  result: 'advanced',
  feedback_text: '',
  reflection_text: '',
  next_action_text: '',
  feedback_tags: [],
  source_kind: 'ui',
  occurred_at: '2026-08-15T11:40:00+08:00',
  created_at: '2026-08-15T11:40:00+08:00',
} satisfies ApplicationOutcome;

describe('applicationCommunicationDraft', () => {
  it('builds a deterministic follow-up from a frozen submission snapshot', () => {
    const result = buildApplicationCommunicationDraft({
      kind: 'follow_up',
      application,
      snapshot,
      recipientName: '林女士',
      timezoneOffsetMinutes: -480,
    });

    expect(result.subject).toBe('关于「高级后端工程师」申请进展的跟进');
    expect(result.body).toContain('林女士，您好：');
    expect(result.body).toContain('我于 2026年8月12日提交了贵公司「高级后端工程师」职位的申请');
    expect(result.body).not.toContain('面试交流');
    expect(result.evidenceLabels).toEqual([
      '投递档案 #11 · 筱哲 · 高级后端工程师版 · JD v2',
      '投递时间 · 2026-08-12',
      '当前投递信息 · 云栖智能 · 高级后端工程师',
    ]);
  });

  it('builds a thank-you draft only from explicit event, outcome, and user highlight', () => {
    const result = buildApplicationCommunicationDraft({
      kind: 'thank_you',
      application,
      snapshot,
      event: interviewEvent,
      outcome: interviewOutcome,
      highlight: '服务稳定性与可观测性',
      timezoneOffsetMinutes: -480,
    });

    expect(result.subject).toBe('感谢「高级后端工程师」面试交流');
    expect(result.body).toContain('您好：');
    expect(result.body).toContain('关于「服务稳定性与可观测性」的讨论');
    expect(result.evidenceLabels).toEqual([
      '投递档案 #11 · 筱哲 · 高级后端工程师版 · JD v2',
      '投递时间 · 2026-08-12',
      '结果 #41 · 面试 · 进入下一阶段',
      '日程 #31 · 第 2 轮面试 · 2026-08-15',
      '用户确认亮点',
      '当前投递信息 · 云栖智能 · 高级后端工程师',
    ]);
  });

  it('omits invalid dates and never guesses a contact name', () => {
    const result = buildApplicationCommunicationDraft({
      kind: 'follow_up',
      application,
      snapshot: { ...snapshot, submitted_at: 'not-a-date' },
      recipientName: '   ',
      timezoneOffsetMinutes: -480,
    });

    expect(result.body.startsWith('您好：')).toBe(true);
    expect(result.body).not.toContain('我于');
    expect(result.evidenceLabels).not.toContainEqual(expect.stringContaining('投递时间'));
  });

  it('rejects overlong highlights by Unicode code point', () => {
    expect(() => buildApplicationCommunicationDraft({
      kind: 'thank_you',
      application,
      snapshot,
      event: interviewEvent,
      outcome: interviewOutcome,
      highlight: '🙂'.repeat(241),
      timezoneOffsetMinutes: -480,
    })).toThrow('交流亮点不能超过 240 个字符');
  });

  it('enables thank-you only with a recorded interview outcome', () => {
    expect(isThankYouDraftAvailable([])).toBe(false);
    expect(isThankYouDraftAvailable([interviewOutcome])).toBe(true);
    expect(isThankYouDraftAvailable([{ ...interviewOutcome, stage: 'screening' }])).toBe(false);
  });

  it('rejects missing or mismatched thank-you sources', () => {
    expect(() => buildApplicationCommunicationDraft({
      kind: 'thank_you', application, snapshot, timezoneOffsetMinutes: -480,
    })).toThrow('请选择一条已记录的面试结果');
    expect(() => buildApplicationCommunicationDraft({
      kind: 'thank_you', application, snapshot, outcome: interviewOutcome,
      event: { ...interviewEvent, id: 99 }, timezoneOffsetMinutes: -480,
    })).toThrow('面试日程与结果记录不匹配');
    expect(() => buildApplicationCommunicationDraft({
      kind: 'thank_you', application, snapshot, outcome: interviewOutcome,
      event: { ...interviewEvent, status: 'cancelled' }, timezoneOffsetMinutes: -480,
    })).toThrow('面试日程不可用于感谢信');
    expect(() => buildApplicationCommunicationDraft({
      kind: 'thank_you', application, snapshot: { ...snapshot, id: 12 }, outcome: interviewOutcome,
      timezoneOffsetMinutes: -480,
    })).toThrow('面试结果不属于所选投递档案');
  });

  it('renders calendar dates with an explicit timezone offset', () => {
    const result = buildApplicationCommunicationDraft({
      kind: 'follow_up',
      application,
      snapshot: { ...snapshot, submitted_at: '2026-08-12T16:30:00Z' },
      timezoneOffsetMinutes: -480,
    });
    expect(result.body).toContain('2026年8月13日');
    expect(result.evidenceLabels).toContain('投递时间 · 2026-08-13');
  });
});
