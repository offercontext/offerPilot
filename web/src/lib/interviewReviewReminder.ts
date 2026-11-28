import type { PipelineInsight, ActionCommand } from './pipelineInsights';
import type { InterviewNote } from '@/types/note';

const validId = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value > 0;

/** A reminder can name a task, but cannot prove that it is still available. */
export function exactReviewReminder(item: PipelineInsight, action: ActionCommand, now: number) {
  const fact = item.candidate;
  if (item.kind !== 'interview_review' || action.target !== 'interview_review'
    || fact?.reason_code !== 'review' || fact.subject_type !== 'application_event'
    || fact.event_type !== 'interview' || !validId(fact.application_id) || !validId(fact.event_id)
    || fact.subject_id !== fact.event_id || item.id !== fact.id
    || item.appId !== fact.application_id || item.eventId !== fact.event_id
    || action.appId !== fact.application_id || action.eventId !== fact.event_id
    || !Number.isFinite(fact.display_from) || !Number.isFinite(fact.display_until)
    || now < fact.display_from * 1000 || now >= fact.display_until! * 1000) return null;
  return { applicationId: fact.application_id, eventId: fact.event_id, scheduledAt: fact.due_at };
}

export function resolveExactReviewNote(rows: unknown, applicationId: number, eventId: number):
  { ready: true; note: InterviewNote | null } | { ready: false } {
  if (!Array.isArray(rows)) return { ready: false };
  let note: InterviewNote | null = null;
  for (const row of rows) {
    if (!row || !validId(row.id) || row.application_id !== applicationId
      || (row.application_event_id !== null && !validId(row.application_event_id))) return { ready: false };
    if (row.application_event_id !== eventId) continue;
    if (note) return { ready: false };
    note = row;
  }
  return { ready: true, note };
}
