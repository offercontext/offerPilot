import dayjs from 'dayjs';
import { describe, expect, it } from 'vitest';
import { derivePipelineInsights, summarizePipelineHealth } from './pipelineInsights';
import type { Application } from '@/types/application';
import type { PracticeStats } from '@/types/question';
import type { ReminderCandidate } from '@/types/reminders';
import rawCorpus from '../../../contracts/reminder-candidates.v1.json?raw';

const corpus = JSON.parse(rawCorpus) as { cases: Array<{ name: string; now: string; expected: ReminderCandidate[] }> };
const input = { apps: [], events: [], offers: [] };
const application: Application = { id: 1, company_name: 'Acme', position_name: 'Engineer',
  job_url: '', status: 'applied', source: '', notes: '', applied_at: '2026-01-01',
  created_at: '2026-01-01', updated_at: '2026-01-01' };

const kinds = { event_upcoming: 'event_upcoming', deadline: 'application_deadline', offer_response_deadline: 'offer_deadline',
  interview_preparation: 'interview_soon', review: 'interview_review', stale_application: 'stale_application' };

describe('shared reminder candidate presentation', () => {
  it.each(corpus.cases)('$name preserves backend identities, reasons and targets', ({ now, expected }) => {
    const insights = derivePipelineInsights({ ...input, now, reminderCandidates: expected });
    expect(insights.map((item) => item.id).sort()).toEqual(expected.map((item) => item.id).sort());
    for (const fact of expected) {
      const insight = insights.find((item) => item.id === fact.id)!;
      expect(insight.kind).toBe(kinds[fact.reason_code]);
      expect(insight.candidate).toEqual(fact);
      expect(insight.primaryAction.appId).toBe(fact.application_id ?? undefined);
      expect(insight.primaryAction.eventId).toBe(fact.event_id ?? undefined);
      expect(insight.primaryAction.offerId).toBe(fact.offer_id ?? undefined);
    }
  });

  it('labels ordinary calendar events without turning them into interview preparation', () => {
    const scenario = corpus.cases.find((item) => item.name === 'display_events_24h')!;
    const insights = derivePipelineInsights({ ...input, now: scenario.now, reminderCandidates: scenario.expected });
    expect(insights.map((item) => item.reason)).toEqual([
      '笔试 将在 24 小时后开始。', 'Offer 进展 将在 24 小时后开始。', '自定义 将在 24 小时后开始。',
    ]);
    expect(insights.every((item) => item.kind === 'event_upcoming' && item.primaryAction.target === 'calendar')).toBe(true);
    expect(insights.every((item) => item.candidate?.delivery_due_at === null)).toBe(true);
  });

  it('does not reconstruct reminder eligibility from incomplete client sources', () => {
    expect(derivePipelineInsights({ ...input, apps: [application], now: '2026-09-09' })).toEqual([]);
  });

  it('keeps elapsed 7/8/14-day cards visible while delivery windows remain distinct', () => {
    for (const days of [7, 8, 14]) {
      const scenario = corpus.cases.find((item) => item.name === `stale_${days}_days`)!;
      const insight = derivePipelineInsights({ ...input, now: scenario.now, reminderCandidates: scenario.expected })[0];
      expect(insight.reason).toBe(`已 ${days} 天没有更新。`);
      expect(insight.priority).toBe(days >= 14 ? 'p1' : 'p2');
    }
  });

  it('expires a fetched interview locally without waiting for refetch', () => {
    const scenario = corpus.cases.find((item) => item.name === 'interview_24h')!;
    const due = scenario.expected[0].due_at;
    expect(derivePipelineInsights({ ...input, now: dayjs(due * 1000), reminderCandidates: scenario.expected })).toEqual([]);
  });

  it('keeps Offer response deadlines and ApplicationEvent deadlines separate', () => {
    const scenario = corpus.cases.find((item) => item.name === 'both_deadline_subjects')!;
    const insights = derivePipelineInsights({ ...input, now: scenario.now, reminderCandidates: scenario.expected });
    expect(insights).toHaveLength(2);
    expect(new Set(insights.map((item) => item.id)).size).toBe(2);
    expect(insights.find((item) => item.kind === 'offer_deadline')?.primaryAction.target).toBe('offers');
    expect(insights.find((item) => item.kind === 'application_deadline')?.primaryAction.target).toBe('calendar');
  });

  it('does not count a cancelled event as an upcoming step', () => {
    const insights = derivePipelineInsights({ ...input, apps: [{ ...application, status: 'interview' }],
      now: '2026-09-09T04:00:00Z', events: [{ id: 1, application_id: 1, event_type: 'interview',
        subtype: '', tags: [], round: 1, scheduled_at: '2026-09-10T04:00:00Z', duration_minutes: 30,
        location: '', notes: '', status: 'cancelled', created_at: '' }] });
    expect(insights.map((item) => item.kind)).toEqual(['no_next_event']);
  });

  it('retains practice statistics and explicit weekly-target health', () => {
    const stats: PracticeStats = { total: 20, new: 3, practicing: 12, mastered: 5,
      due: 12, today_reviews: 0, streak_days: 0 };
    const insights = derivePipelineInsights({ ...input, practiceStats: stats, now: '2026-09-09' });
    expect(insights[0]).toMatchObject({ kind: 'question_due', priority: 'p1', questionCount: 12 });
    expect(summarizePipelineHealth([], insights, 6, '2026-09-09').weeklyTarget).toBe(6);
  });
});

describe('explicit goal opt-in', () => {
  const now = '2026-07-01T12:00:00';
  const makeApplication = (overrides: Partial<Application>): Application => ({ ...application, ...overrides });
  it.each([undefined, 0])('does not create a target gap when absent or paused (%s)', (weeklyTarget) => {
    const input = { apps: [], events: [], offers: [], weeklyTarget, now };
    expect(derivePipelineInsights(input).some((item) => item.kind === 'weekly_goal_gap')).toBe(false);
    expect(summarizePipelineHealth([], [], weeklyTarget, now).label).toBe('healthy');
  });
  it('uses the calendar week for an explicit target, matching weekly mission metrics', () => {
    const apps = [makeApplication({ id: 1, applied_at: '2026-06-27' }), makeApplication({ id: 2, applied_at: '2026-06-28' }), makeApplication({ id: 3, applied_at: '2026-07-05' })];
    const health = summarizePipelineHealth(apps, [], 4, now);
    expect(health.weeklyApplications).toBe(1);
    expect(health.weeklyGap).toBe(3);
  });
});
