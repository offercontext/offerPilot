import type { ScheduleEventType } from './event';

/** Read-only facts, not delivery permission. Unix timestamps are UTC seconds. */
export interface ReminderCandidate {
  id: string;
  subject_type: 'application' | 'application_event' | 'offer';
  subject_id: number;
  reason_code: 'offer_response_deadline' | 'deadline' | 'interview_preparation' | 'review' | 'stale_application' | 'event_upcoming';
  source_revision: string;
  application_id: number | null;
  event_id: number | null;
  event_type: ScheduleEventType | null;
  offer_id: number | null;
  company: string;
  position: string;
  due_at: number;
  display_from: number;
  display_until: number | null;
  delivery_due_at: number | null;
  delivery_expires_at: number | null;
  updated_at: number | null;
  deadline_label: string | null;
}
