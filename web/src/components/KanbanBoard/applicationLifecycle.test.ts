import { describe, expect, it } from 'vitest';
import type { Application } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import {
  buildApplicationStatusPayload,
  filterAndSortApplications,
  formatNextApplicationEvent,
  resolveKanbanDropDestination,
  requiresClosedReason,
  willRecordFirstStatusTimestamp,
} from './applicationLifecycle';

function app(patch: Partial<Application>): Application {
  return {
    id: patch.id ?? 1,
    company_name: patch.company_name ?? 'ByteDance',
    position_name: patch.position_name ?? 'Backend',
    job_url: patch.job_url ?? '',
    status: patch.status ?? 'applied',
    source: patch.source ?? 'web',
    notes: patch.notes ?? '',
    applied_at: patch.applied_at ?? '2026-07-01T09:00:00+08:00',
    first_pending_at: patch.first_pending_at ?? null,
    first_applied_at: patch.first_applied_at ?? '2026-07-01T09:00:00+08:00',
    first_written_test_at: patch.first_written_test_at ?? null,
    first_interview_at: patch.first_interview_at ?? null,
    first_offer_at: patch.first_offer_at ?? null,
    closed_reason: patch.closed_reason ?? '',
    closed_at: patch.closed_at ?? null,
    deleted_at: patch.deleted_at ?? null,
    created_at: patch.created_at ?? '2026-07-01T09:00:00+08:00',
    updated_at: patch.updated_at ?? '2026-07-02T09:00:00+08:00',
  };
}

function event(patch: Partial<ScheduleEvent>): ScheduleEvent {
  return {
    id: patch.id ?? 1,
    application_id: patch.application_id ?? 1,
    event_type: patch.event_type ?? 'interview',
    subtype: patch.subtype ?? '',
    tags: patch.tags ?? [],
    round: patch.round ?? 1,
    scheduled_at: patch.scheduled_at ?? '2026-07-10T10:00:00+08:00',
    duration_minutes: patch.duration_minutes ?? 45,
    location: patch.location ?? '',
    notes: patch.notes ?? '',
    remind_at: patch.remind_at ?? null,
    status: patch.status ?? 'todo',
    created_at: patch.created_at ?? '2026-07-01T09:00:00+08:00',
  };
}

describe('application lifecycle helpers', () => {
  it('requires a close reason only when entering closed', () => {
    expect(requiresClosedReason('interview', 'closed')).toBe(true);
    expect(requiresClosedReason('closed', 'closed')).toBe(false);
    expect(requiresClosedReason('applied', 'offer')).toBe(false);
  });

  it('routes Kanban drops to either a lifecycle status or the Pilot context', () => {
    expect(resolveKanbanDropDestination('interview')).toEqual({ kind: 'status', status: 'interview' });
    expect(resolveKanbanDropDestination('pilot-context-drop')).toEqual({ kind: 'pilot' });
    expect(resolveKanbanDropDestination('unknown-drop-target')).toBeNull();
  });

  it('builds a closed status payload with reason', () => {
    const payload = buildApplicationStatusPayload(app({ status: 'offer' }), 'closed', '已接受其他 offer');

    expect(payload.status).toBe('closed');
    expect(payload.closed_reason).toBe('已接受其他 offer');
  });

  it('knows whether a first status timestamp will be recorded', () => {
    expect(willRecordFirstStatusTimestamp(app({ first_interview_at: null }), 'interview')).toBe(true);
    expect(
      willRecordFirstStatusTimestamp(app({ first_interview_at: '2026-07-02T09:00:00+08:00' }), 'interview')
    ).toBe(false);
  });

  it('filters out deleted applications and sorts by updated time', () => {
    const rows = filterAndSortApplications(
      [
        app({ id: 1, company_name: 'A', updated_at: '2026-07-01T09:00:00+08:00' }),
        app({ id: 2, company_name: 'B', updated_at: '2026-07-03T09:00:00+08:00' }),
        app({ id: 3, company_name: 'C', deleted_at: '2026-07-04T09:00:00+08:00' }),
      ],
      { keyword: '', status: 'all', sortBy: 'updated_desc' }
    );

    expect(rows.map((row) => row.id)).toEqual([2, 1]);
  });

  it('filters applications by keyword and status', () => {
    const rows = filterAndSortApplications(
      [
        app({ id: 1, company_name: 'ByteDance', position_name: 'Backend', status: 'interview' }),
        app({ id: 2, company_name: 'Tencent', position_name: 'Frontend', status: 'applied' }),
      ],
      { keyword: 'byte backend', status: 'interview', sortBy: 'updated_desc' }
    );

    expect(rows.map((row) => row.id)).toEqual([1]);
  });

  it('formats the nearest future event for a row', () => {
    const next = formatNextApplicationEvent(
      app({ id: 7 }),
      [
        event({ id: 1, application_id: 7, event_type: 'deadline', scheduled_at: '2026-07-08T10:00:00+08:00' }),
        event({ id: 2, application_id: 7, event_type: 'interview', scheduled_at: '2026-07-10T10:00:00+08:00' }),
        event({ id: 3, application_id: 9, event_type: 'written_test', scheduled_at: '2026-07-09T10:00:00+08:00' }),
      ],
      '2026-07-09T00:00:00+08:00'
    );

    expect(next).toContain('面试');
    expect(next).toContain('2026-07-10');
  });
});
