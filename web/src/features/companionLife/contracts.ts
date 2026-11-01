export interface CompanionSettings {
  enabled: boolean;
  proactivity: 'off' | 'less' | 'default';
  hide_status: boolean;
}
export interface CompanionActivity {
  id: string;
  template_id: string;
  title: string;
  status: 'active' | 'paused' | 'waiting_shared' | 'completed' | 'abandoned';
  step_index: number;
  step_count: number;
  step_title: string;
  started_at: number;
  updated_at: number;
  participants: string[];
  promise: string | null;
  paused_from?: 'active' | 'waiting_shared';
}
export interface CompanionPresence {
  session_id: string | null;
  session_quiet: boolean;
  active: boolean;
  blocked: boolean;
  blockers: string[];
}
export interface CompanionSnapshot {
  identity: string;
  revision: number;
  persona_version: string;
  settings: CompanionSettings;
  state: { interests: string[]; activity: CompanionActivity | null; wish: { id: string; activity_id: string; text: string } | null };
  recent_events: { id: string; revision: number; category: 'virtual' | 'actual'; event_type: string; activity_id: string | null; text: string; occurred_at: number; source: string }[];
  shared_participation: { id: string; activity_id: string; status: 'promised' | 'completed' | 'abandoned'; promise: string; created_at: number; completed_at: number | null }[];
  presence: CompanionPresence;
  receipt?: { command_id: string; revision: number };
  changed?: boolean;
}
export interface CompanionSignals {
  visible: boolean;
  typing: boolean;
  work_running: boolean;
  awaiting_approval: boolean;
  mock_interview: boolean;
  quiet: boolean;
  failure: boolean;
}
export type CompanionEvent = 'app_open' | 'chat_end' | 'work_completed' | 'user_interaction' | 'pause' | 'resume' | 'abandon' | 'join' | 'complete_shared' | 'withdraw_shared' | 'decline_wish' | 'accept_wish' | 'quiet_session' | 'resume_session';
export interface CompanionEnvelope { identity: string; expected_revision: number; command_id: string }
export type CompanionCommand =
  | { kind: 'event'; body: CompanionEnvelope & { event: CompanionEvent; activity_id?: string; wish_id?: string } }
  | { kind: 'settings'; body: CompanionEnvelope & { settings: CompanionSettings } }
  | { kind: 'reset'; body: CompanionEnvelope & { confirmed: true } };
export interface CompanionBid { source_event_type?: 'activity_started' | 'activity_progressed' | 'activity_completed'; id: string; kind: 'companion_life'; result_text: string; source_revision: number; source_identity: string }
export interface CompanionMirror { invitation?: 'activity_started' | 'activity_progressed' | 'activity_completed'; title: string; detail: string; status: CompanionActivity['status'] | 'idle'; virtual: true }
