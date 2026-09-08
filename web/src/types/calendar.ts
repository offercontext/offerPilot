// Mirrors Go api.CalendarEntry JSON tags.
import type { ScheduleEventType } from '@/types/event';

export type CalendarEntryType = 'interview' | 'applied' | ScheduleEventType;

export interface CalendarEntry {
  date: string;
  type: CalendarEntryType;
  title: string;
  subtitle?: string;
  app_id: number;
  note_id?: number;
  event_id?: number;
  event_type?: ScheduleEventType;
  scheduled_at?: string;
  duration_minutes?: number;
  location?: string;
  editable?: boolean;
}
