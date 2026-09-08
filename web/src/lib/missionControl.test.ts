import { describe, expect, it } from 'vitest';
import dayjs from 'dayjs';
import type { Application } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import type { Offer } from '@/types/offer';
import type { MaterialKitViewModel } from '@/types/materialKit';
import type { PracticeStats } from '@/types/question';
import type { PipelineInsight } from './pipelineInsights';
import {
  READINESS_MATERIAL_STATUS_LABELS,
  READINESS_STATE_LABELS,
  deriveMissionControl,
  groupMissionActions,
  selectDefaultFocusApplicationId,
} from './missionControl';

const now = dayjs('2026-07-04T09:00:00+08:00');

function app(patch: Partial<Application> & Pick<Application, 'id'>): Application {
  return {
    id: patch.id,
    company_name: patch.company_name ?? `Company ${patch.id}`,
    position_name: patch.position_name ?? 'Backend Engineer',
    job_url: patch.job_url ?? '',
    status: patch.status ?? 'applied',
    source: patch.source ?? 'manual',
    notes: patch.notes ?? '',
    applied_at: patch.applied_at ?? '2026-07-01',
    created_at: patch.created_at ?? '2026-07-01T09:00:00+08:00',
    updated_at: patch.updated_at ?? '2026-07-01T09:00:00+08:00',
  };
}

function event(patch: Partial<ScheduleEvent> & Pick<ScheduleEvent, 'id' | 'application_id'>): ScheduleEvent {
  return {
    id: patch.id,
    application_id: patch.application_id,
    event_type: patch.event_type ?? 'interview',
    subtype: patch.subtype ?? '',
    tags: patch.tags ?? [],
    round: patch.round ?? 1,
    scheduled_at: patch.scheduled_at ?? '2026-07-05T10:00:00+08:00',
    duration_minutes: patch.duration_minutes ?? 60,
    location: patch.location ?? '',
    notes: patch.notes ?? '',
    remind_at: patch.remind_at ?? null,
    status: patch.status ?? 'todo',
    company_name: patch.company_name,
    position_name: patch.position_name,
    created_at: patch.created_at ?? '2026-07-01T09:00:00+08:00',
  };
}

function kit(applicationId: number, status: MaterialKitViewModel['status']): MaterialKitViewModel {
  return {
    id: applicationId,
    application_id: applicationId,
    jd_snapshot: '',
    status,
    content: {
      resume_advice: { summary: '', highlights: [], rewrite_bullets: [], gaps: [], notes: '' },
      messages: [],
      checklist: [],
    },
    created_at: '2026-07-01T09:00:00+08:00',
    updated_at: '2026-07-01T09:00:00+08:00',
  };
}

function insight(patch: Partial<PipelineInsight> & Pick<PipelineInsight, 'id' | 'kind'>): PipelineInsight {
  return {
    id: patch.id,
    kind: patch.kind,
    priority: patch.priority ?? 'p2',
    title: patch.title ?? patch.id,
    reason: patch.reason ?? 'reason',
    evidence: patch.evidence ?? ['evidence'],
    primaryAction: patch.primaryAction ?? { label: 'Open', target: 'board', appId: patch.appId },
    sortKey: patch.sortKey ?? 100,
    appId: patch.appId,
    offerId: patch.offerId,
    eventId: patch.eventId,
    questionCount: patch.questionCount,
  };
}

describe('deriveMissionControl', () => {
  it('builds weekly metrics with explicit target states', () => {
    const summary = deriveMissionControl({
      apps: [
        app({ id: 1, applied_at: '2026-07-01' }),
        app({ id: 2, applied_at: '2026-07-02' }),
      ],
      events: [event({ id: 1, application_id: 1 })],
      offers: [],
      materialKits: [kit(1, 'ready'), kit(2, 'draft')],
      practiceStats: { total: 12, due: 4, mastered: 3, practicing: 5, new: 4 } as PracticeStats,
      insights: [insight({ id: 'questions-due', kind: 'question_due', questionCount: 4 })],
      healthLabel: 'watch',
      weeklyTarget: 6,
      now,
    });

    expect(summary.metrics.find((metric) => metric.kind === 'applications')).toMatchObject({
      current: 2,
      target: 6,
      state: 'behind',
      targetView: 'board',
    });
    expect(summary.metrics.find((metric) => metric.kind === 'practice')).toMatchObject({
      current: 4,
      target: 1,
      state: 'watch',
      targetView: 'questions',
    });
    expect(summary.metrics.find((metric) => metric.kind === 'materials')).toMatchObject({
      current: 1,
      target: 2,
      state: 'watch',
      targetView: 'board',
    });
  });

  it('classifies readiness as blocked for imminent interviews without ready material', () => {
    const summary = deriveMissionControl({
      apps: [app({ id: 1, status: 'interview', company_name: 'ByteDance' })],
      events: [event({ id: 1, application_id: 1, scheduled_at: '2026-07-04T18:00:00+08:00' })],
      offers: [],
      materialKits: [kit(1, 'draft')],
      practiceStats: { total: 0, due: 0, mastered: 0, practicing: 0, new: 0 } as PracticeStats,
      insights: [],
      healthLabel: 'watch',
      weeklyTarget: 6,
      now,
    });

    expect(summary.readiness[0]).toMatchObject({
      applicationId: 1,
      companyName: 'ByteDance',
      readiness: 'blocked',
      materialStatus: 'draft',
      hasUpcomingEvent: true,
    });
    expect(summary.readiness[0].evidence.join(' ')).toContain('24 小时');
  });

  it('provides localized labels for readiness and material statuses', () => {
    const summary = deriveMissionControl({
      apps: [app({ id: 1, status: 'written_test', company_name: 'ByteDance' })],
      events: [],
      offers: [],
      materialKits: [],
      practiceStats: null,
      insights: [],
      healthLabel: 'watch',
      weeklyTarget: 6,
      now,
    });

    expect(READINESS_STATE_LABELS.watch).toBe('关注');
    expect(READINESS_MATERIAL_STATUS_LABELS.missing).toBe('缺失');
    expect(summary.readiness[0]).toMatchObject({
      readiness: 'watch',
      materialStatus: 'missing',
    });
    expect(summary.readiness[0].evidence).toContain('材料状态：缺失');
  });

  it('counts same-day date-only pending offer deadlines as urgent', () => {
    const summary = deriveMissionControl({
      apps: [],
      events: [],
      offers: [
        {
          id: 1,
          application_id: 1,
          company_name: 'Deadline Co',
          position_name: 'Backend Engineer',
          status: 'pending',
          base_monthly: 30000,
          months_per_year: 16,
          signing_bonus: 0,
          equity: '',
          perks: '',
          deadline: '2026-07-04',
          notes: '',
          assessment: '',
          total_cash: 480000,
          created_at: '2026-07-01T09:00:00+08:00',
          updated_at: '2026-07-01T09:00:00+08:00',
        } satisfies Offer,
      ],
      materialKits: [],
      practiceStats: null,
      insights: [],
      healthLabel: 'watch',
      weeklyTarget: 6,
      now,
    });

    expect(summary.metrics.find((metric) => metric.kind === 'offers')).toMatchObject({
      current: 1,
      state: 'blocked',
      targetView: 'offers',
    });
  });

  it('groups actions into urgent prepare and momentum buckets', () => {
    const groups = groupMissionActions([
      insight({ id: 'p0-offer', kind: 'offer_deadline', priority: 'p0' }),
      insight({ id: 'prepare', kind: 'interview_soon', priority: 'p1' }),
      insight({ id: 'momentum', kind: 'weekly_goal_gap', priority: 'p2' }),
    ]);

    expect(groups.urgent.map((item) => item.id)).toEqual(['p0-offer']);
    expect(groups.prepare.map((item) => item.id)).toEqual(['prepare']);
    expect(groups.momentum.map((item) => item.id)).toEqual(['momentum']);
  });

  it('selects a default focus application from urgent readiness before other active apps', () => {
    const focus = selectDefaultFocusApplicationId([
      {
        applicationId: 2,
        companyName: 'Ready Co',
        positionName: 'Backend',
        status: 'applied',
        readiness: 'ready',
        materialStatus: 'ready',
        hasUpcomingEvent: false,
        evidence: [],
      },
      {
        applicationId: 1,
        companyName: 'Blocked Co',
        positionName: 'Backend',
        status: 'interview',
        readiness: 'blocked',
        materialStatus: 'draft',
        hasUpcomingEvent: true,
        evidence: [],
      },
    ]);

    expect(focus).toBe(1);
  });

  it('handles null optional arrays and produces an empty-state headline', () => {
    const summary = deriveMissionControl({
      apps: null,
      events: null,
      offers: null,
      materialKits: null,
      practiceStats: null,
      insights: [],
      healthLabel: 'healthy',
      weeklyTarget: 6,
      now,
    });

    expect(summary.metrics.find((metric) => metric.kind === 'applications')).toMatchObject({
      current: 0,
      state: 'behind',
    });
    expect(summary.readiness).toEqual([]);
    expect(summary.headline).toContain('添加第一条投递');
  });
});
