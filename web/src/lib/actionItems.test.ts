import { describe, expect, it } from 'vitest';
import { deriveActionItems, summarizeActionItems } from './actionItems';
import type { Application } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import type { Offer } from '@/types/offer';
import type { PracticeStats } from '@/types/question';

const now = '2026-07-02T09:00:00+08:00';

function app(overrides: Partial<Application>): Application {
  return {
    id: 1,
    company_name: '腾讯',
    position_name: '后端开发',
    job_url: '',
    status: 'applied',
    source: '',
    notes: '',
    applied_at: '2026-06-20T09:00:00+08:00',
    created_at: '2026-06-20T09:00:00+08:00',
    updated_at: '2026-06-20T09:00:00+08:00',
    ...overrides,
  };
}

function event(overrides: Partial<ScheduleEvent>): ScheduleEvent {
  return {
    id: 1,
    application_id: 1,
    event_type: 'interview',
    subtype: '',
    tags: [],
    round: 1,
    scheduled_at: '2026-07-03T10:00:00+08:00',
    duration_minutes: 60,
    location: '',
    notes: '',
    remind_at: null,
    status: 'todo',
    company_name: '字节跳动',
    position_name: '后端开发',
    created_at: '2026-07-01T09:00:00+08:00',
    ...overrides,
  };
}

function offer(overrides: Partial<Offer>): Offer {
  return {
    id: 1,
    application_id: 1,
    company_name: '美团',
    position_name: '后端开发',
    status: 'pending',
    base_monthly: 35000,
    months_per_year: 16,
    signing_bonus: 50000,
    equity: '',
    perks: '',
    deadline: '2026-07-04T09:00:00+08:00',
    notes: '',
    assessment: '',
    total_cash: 610000,
    created_at: '2026-07-01T09:00:00+08:00',
    updated_at: '2026-07-01T09:00:00+08:00',
    ...overrides,
  };
}

function stats(overrides: Partial<PracticeStats>): PracticeStats {
  return {
    total: 20,
    new: 4,
    practicing: 10,
    mastered: 6,
    due: 8,
    today_reviews: 0,
    streak_days: 2,
    ...overrides,
  };
}

describe('deriveActionItems', () => {
  it('prioritizes urgent offer deadlines before interviews and stale applications', () => {
    const items = deriveActionItems({
      apps: [app({ id: 7, company_name: '腾讯', updated_at: '2026-06-18T09:00:00+08:00' })],
      events: [event({ id: 3, application_id: 8, scheduled_at: '2026-07-03T08:00:00+08:00' })],
      offers: [offer({ id: 2, deadline: '2026-07-04T09:00:00+08:00' })],
      practiceStats: stats({ due: 8 }),
      now,
    });

    expect(items.map((item) => item.kind)).toEqual([
      'offer_deadline',
      'interview_soon',
      'stale_application',
      'question_due',
    ]);
    expect(items[0]).toMatchObject({
      priority: 'p0',
      target: 'offers',
      offerId: 2,
      primaryActionLabel: '处理 Offer',
    });
  });

  it('includes date-only offer deadlines that expire on the current calendar day', () => {
    const items = deriveActionItems({
      apps: [],
      events: [],
      offers: [offer({ id: 11, deadline: '2026-07-02' })],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      id: 'offer-11',
      kind: 'offer_deadline',
      priority: 'p0',
      title: '美团 Offer 答复期今天到期',
      target: 'offers',
      offerId: 11,
    });
  });

  it('labels timed offer deadlines later today as due today', () => {
    const items = deriveActionItems({
      apps: [],
      events: [],
      offers: [offer({ id: 23, deadline: '2026-07-02T18:00:00+08:00' })],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      id: 'offer-23',
      kind: 'offer_deadline',
      priority: 'p0',
      title: '美团 Offer 答复期今天到期',
      target: 'offers',
      offerId: 23,
    });
  });

  it('skips malformed offer deadlines without creating NaN titles', () => {
    const items = deriveActionItems({
      apps: [],
      events: [],
      offers: [offer({ id: 12, deadline: 'not-a-date' })],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items.some((item) => item.kind === 'offer_deadline')).toBe(false);
    expect(items.some((item) => item.title.includes('NaN'))).toBe(false);
  });

  it('skips date-shaped invalid offer deadlines without rolling over', () => {
    const items = deriveActionItems({
      apps: [],
      events: [],
      offers: [offer({ id: 15, deadline: '2026-06-32' })],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items.some((item) => item.kind === 'offer_deadline')).toBe(false);
    expect(items.some((item) => item.title.includes('今天到期'))).toBe(false);
  });

  it('skips malformed interview event dates without creating invalid titles', () => {
    const items = deriveActionItems({
      apps: [],
      events: [event({ id: 13, scheduled_at: 'not-a-date' })],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items.some((item) => item.kind === 'interview_soon')).toBe(false);
    expect(items.some((item) => item.title.includes('Invalid Date'))).toBe(false);
  });

  it('skips date-shaped invalid interview event dates without rolling over', () => {
    const items = deriveActionItems({
      apps: [],
      events: [event({ id: 16, scheduled_at: '2026-05-01T10:00:00+08:00' })],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now: '2026-04-30T09:00:00+08:00',
    });

    const rolledOverItems = deriveActionItems({
      apps: [],
      events: [event({ id: 17, scheduled_at: '2026-04-31T10:00:00+08:00' })],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now: '2026-04-30T09:00:00+08:00',
    });

    expect(items.some((item) => item.kind === 'interview_soon')).toBe(true);
    expect(rolledOverItems.some((item) => item.kind === 'interview_soon')).toBe(false);
    expect(rolledOverItems.some((item) => item.title.includes('Invalid Date'))).toBe(false);
  });

  it('accepts RFC3339 Z interview timestamps that cross the local calendar date', () => {
    const items = deriveActionItems({
      apps: [],
      events: [event({ id: 20, scheduled_at: '2026-07-02T16:30:00Z' })],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items).toContainEqual(
      expect.objectContaining({
        id: 'interview-20',
        kind: 'interview_soon',
        priority: 'p0',
      }),
    );
  });

  it('skips slash malformed interview event dates without rolling over', () => {
    const items = deriveActionItems({
      apps: [],
      events: [event({ id: 21, scheduled_at: '2026/02/30' })],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now: '2026-03-01T09:00:00+08:00',
    });

    expect(items.some((item) => item.kind === 'interview_soon')).toBe(false);
    expect(items.some((item) => item.title.includes('Invalid Date'))).toBe(false);
  });

  it('uses P0 for interviews within 24 hours and P1 for interviews within 72 hours', () => {
    const items = deriveActionItems({
      apps: [],
      events: [
        event({ id: 1, scheduled_at: '2026-07-02T18:00:00+08:00' }),
        event({ id: 2, scheduled_at: '2026-07-04T08:00:00+08:00' }),
        event({ id: 3, scheduled_at: '2026-07-05T09:00:00+08:00' }),
      ],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items).toHaveLength(3);
    expect(items[0]).toMatchObject({ id: 'interview-1', priority: 'p0' });
    expect(items[1]).toMatchObject({ id: 'interview-2', priority: 'p1' });
    expect(items[2]).toMatchObject({ id: 'interview-3', priority: 'p1' });
  });

  it('skips malformed application dates without creating NaN stale titles', () => {
    const items = deriveActionItems({
      apps: [app({ id: 14, updated_at: 'not-a-date', applied_at: 'not-a-date' })],
      events: [],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items.some((item) => item.kind === 'stale_application')).toBe(false);
    expect(items.some((item) => item.title.includes('NaN'))).toBe(false);
  });

  it('skips date-shaped invalid application dates without creating NaN stale titles', () => {
    const items = deriveActionItems({
      apps: [
        app({
          id: 18,
          updated_at: '2026-02-31T09:00:00+08:00',
          applied_at: '2026-02-31T09:00:00+08:00',
        }),
      ],
      events: [],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items.some((item) => item.kind === 'stale_application')).toBe(false);
    expect(items.some((item) => item.title.includes('NaN'))).toBe(false);
  });

  it('skips non-zero-padded malformed application dates without creating stale actions', () => {
    const items = deriveActionItems({
      apps: [
        app({
          id: 22,
          updated_at: '2026-2-30',
          applied_at: '2026-2-30',
        }),
      ],
      events: [],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now: '2026-03-15T09:00:00+08:00',
    });

    expect(items.some((item) => item.kind === 'stale_application')).toBe(false);
    expect(items.some((item) => item.title.includes('NaN'))).toBe(false);
  });

  it('creates stale actions only for waiting applications older than seven days', () => {
    const items = deriveActionItems({
      apps: [
        app({ id: 1, status: 'applied', updated_at: '2026-06-24T09:00:00+08:00' }),
        app({ id: 2, status: 'written_test', updated_at: '2026-06-17T09:00:00+08:00' }),
        app({ id: 3, status: 'interview', updated_at: '2026-06-10T09:00:00+08:00' }),
        app({ id: 4, status: 'closed', updated_at: '2026-06-01T09:00:00+08:00' }),
      ],
      events: [event({ id: 9, application_id: 3, scheduled_at: '2026-07-05T09:01:00+08:00' })],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items.map((item) => item.id)).toEqual(['stale-2', 'stale-1']);
    expect(items[0]).toMatchObject({ priority: 'p1', appId: 2 });
    expect(items[1]).toMatchObject({ priority: 'p2', appId: 1 });
  });

  it('creates a no-next-event action for interview applications without future events', () => {
    const items = deriveActionItems({
      apps: [app({ id: 5, status: 'interview', company_name: '阿里', position_name: 'Go 开发' })],
      events: [],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      id: 'no-next-5',
      kind: 'no_next_event',
      priority: 'p2',
      target: 'calendar',
      appId: 5,
      primaryActionLabel: '安排下一步',
    });
  });

  it('creates material kit actions for active waiting applications without a completed kit', () => {
    const items = deriveActionItems({
      apps: [app({ id: 30, status: 'applied', company_name: 'Acme', position_name: 'Backend' })],
      events: [],
      offers: [],
      materialKits: [{ application_id: 30, complete: false }],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items).toContainEqual(
      expect.objectContaining({
        id: 'material-kit-30',
        kind: 'material_kit_incomplete',
        priority: 'p2',
        target: 'board',
        appId: 30,
        primaryActionLabel: '打开材料包',
      }),
    );
  });

  it('does not let an invalid future-shaped event suppress no-next-event actions', () => {
    const items = deriveActionItems({
      apps: [app({ id: 19, status: 'interview', company_name: '阿里', position_name: 'Go 开发' })],
      events: [event({ id: 19, application_id: 19, scheduled_at: '2026-07-32T10:00:00+08:00' })],
      offers: [],
      practiceStats: stats({ due: 0 }),
      now,
    });

    expect(items.some((item) => item.kind === 'interview_soon')).toBe(false);
    expect(items).toContainEqual(
      expect.objectContaining({
        id: 'no-next-19',
        kind: 'no_next_event',
        appId: 19,
      }),
    );
  });

  it('summarizes urgent, interview, stale, and due-question counts', () => {
    const items = deriveActionItems({
      apps: [app({ id: 1, updated_at: '2026-06-17T09:00:00+08:00' })],
      events: [event({ id: 1, scheduled_at: '2026-07-02T18:00:00+08:00' })],
      offers: [offer({ id: 1, deadline: '2026-07-04T09:00:00+08:00' })],
      practiceStats: stats({ due: 12 }),
      now,
    });

    expect(summarizeActionItems(items)).toEqual({
      p0: 2,
      interviewSoon: 1,
      stale: 1,
      dueQuestions: 12,
    });
  });
});
