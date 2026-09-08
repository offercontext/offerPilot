export type ScheduleEventType = 'written_test' | 'interview' | 'offer_step' | 'deadline' | 'custom';

export const EVENT_TYPE_LABELS: Record<ScheduleEventType, string> = {
  written_test: '笔试',
  interview: '面试',
  offer_step: 'Offer 进展',
  deadline: '截止',
  custom: '自定义',
};

export interface ScheduleEvent {
  id: number;
  application_id: number;
  event_type: ScheduleEventType;
  subtype: string;
  tags: string[];
  round: number;
  scheduled_at: string;
  duration_minutes: number;
  location: string;
  notes: string;
  remind_at?: string | null;
  status: string;
  company_name?: string;
  position_name?: string;
  created_at: string;
}

export interface ScheduleEventInput {
  application_id: number;
  event_type: ScheduleEventType;
  subtype?: string;
  tags?: string[];
  round?: number;
  scheduled_at: string;
  duration_minutes: number;
  location?: string;
  notes?: string;
  remind_at?: string | null;
  status?: string;
}
