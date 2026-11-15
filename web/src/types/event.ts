import { t } from '@/i18n';
export type ScheduleEventType = 'written_test' | 'interview' | 'offer_step' | 'deadline' | 'custom';

export const EVENT_TYPE_LABELS: Record<ScheduleEventType, string> = {
  get written_test() { return t("笔试"); },
  get interview() { return t("面试"); },
  get offer_step() { return t("Offer 进展"); },
  get deadline() { return t("截止"); },
  get custom() { return t("自定义"); },
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
