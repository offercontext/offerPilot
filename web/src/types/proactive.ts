export type ProactiveJobKind =
  | 'deadline'
  | 'interview_preparation'
  | 'stale_application'
  | 'review'
  | 'interview_draft';

export type ProactiveJobState =
  | 'pending'
  | 'leased'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'result_unknown';

export interface ProactiveSettings {
  enabled: boolean;
  reminders_enabled: boolean;
  drafts_enabled: boolean;
  application_ids: number[];
  timezone: string;
  quiet_start_hour: number;
  quiet_end_hour: number;
  max_reminders_per_day: number;
  max_drafts_per_day: number;
}

export interface ProactiveSettingsResponse {
  revision: number;
  settings: ProactiveSettings;
}

export interface ProactiveJob {
  id: string;
  kind: ProactiveJobKind;
  application_id: number;
  event_id: number | null;
  state: ProactiveJobState;
  reason: string;
  /** UTC Unix timestamp in seconds, as returned by the local API. */
  due_at: number;
  /** UTC Unix timestamp in seconds, as returned by the local API. */
  created_at: number;
  /** Present on the backend view; null until a reminder is published. */
  published_at?: number | null;
  result_text: string;
  turn_id: string;
  execution_generation: number;
  error_code: string;
}

export interface ProactiveJobsResponse {
  items: ProactiveJob[];
}

export const DEFAULT_PROACTIVE_SETTINGS: ProactiveSettings = {
  enabled: false,
  reminders_enabled: false,
  drafts_enabled: false,
  application_ids: [],
  timezone: 'Asia/Shanghai',
  quiet_start_hour: 22,
  quiet_end_hour: 8,
  max_reminders_per_day: 4,
  max_drafts_per_day: 1,
};

export const PROACTIVE_KIND_LABELS: Record<ProactiveJobKind, string> = {
  deadline: '截止提醒',
  interview_preparation: '面试准备提醒',
  stale_application: '投递跟进提醒',
  review: '面试复盘提醒',
  interview_draft: '面试准备草稿',
};

export const PROACTIVE_STATE_LABELS: Record<ProactiveJobState, string> = {
  pending: '等待处理',
  leased: '即将处理',
  running: '处理中',
  succeeded: '已完成',
  failed: '处理失败',
  cancelled: '已取消',
  result_unknown: '需要检查',
};

export function isProactiveJobActive(state: ProactiveJobState): boolean {
  return state === 'pending' || state === 'leased' || state === 'running';
}
