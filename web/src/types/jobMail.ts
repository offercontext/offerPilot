import type { ScheduleEventType } from './event';

export interface JobMailFields {
  event_type?: ScheduleEventType;
  subtype?: string;
  scheduled_at?: string;
  duration_minutes?: number;
  location?: string;
  notes?: string;
  round?: number;
  tags?: string[];
  remind_at?: string | null;
}

export interface JobMailEvidence {
  id: string;
  subject: string;
  sender: string;
  received_at: string;
  snippet: string | null;
  body_fingerprint: string;
  truncated: boolean;
  cleared_at: string | null;
}

export interface JobMailReceipt {
  operation_id: string;
  suggestion_id: string;
  application_event_id: number;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown>;
  confirmed_fields: Record<string, unknown>;
  confirmed_at: string;
  replayed: boolean;
}

export interface JobMailSuggestion {
  id: string;
  version: number;
  status: 'pending' | 'applied' | 'ignored' | 'manual_required';
  action: 'create_event' | 'update_event' | 'manual_only';
  reason: string;
  time_mode: string;
  proposed_fields: JobMailFields;
  field_evidence: Record<string, unknown>;
  application_candidates: { id: number; company_name: string; position_name: string }[];
  target_event_id: number | null;
  evidence: JobMailEvidence | null;
  receipt: JobMailReceipt | null;
}

export interface JobMailPreviewInput {
  operation_id: string;
  suggestion_version: number;
  application_id: number;
  target_event_id?: number;
  edited_fields: JobMailFields;
}

export interface JobMailPreview {
  preview_token: string;
  operation_id: string;
  suggestion_id: string;
  suggestion_version: number;
  action: string;
  application_id: number;
  application_snapshot: { id: number; company_name: string; position_name: string; status: string; updated_at: string };
  target_event_id: number | null;
  edited_fields: JobMailFields;
  before: Record<string, unknown> | null;
  after: Record<string, unknown>;
  changes: { field: string; before: unknown; after: unknown }[];
  scope_version: number | null;
  expires_at: string;
  warnings: string[];
}

export interface JobMailImportInput {
  subject: string;
  sender: string;
  received_at: string;
  body_text: string;
}

// Local run observations only. Null means not observed, never an inferred zero.
export interface JobMailReadDiagnostic {
  folder_index: number;
  since_utc: string | null;
  until_utc: string | null;
  cursor_uidvalidity: number | null;
  cursor_before: number | null;
  cursor_after: number | null;
  read: {
    stage: 'read' | 'connect' | 'select' | 'search' | 'metadata' | 'body' | 'parse' | 'complete';
    error_code: string | null;
    server_uidvalidity: number | null;
    server_uidnext: number | null;
    effective_after_uid: number | null;
    uidvalidity_reset: boolean | null;
    search_returned: number | null;
    uid_filtered: number | null;
    eligible_uids: number | null;
    batch_limit_deferred: number | null;
    metadata_checked: number | null;
    before_since: number | null;
    after_until_observed: number | null;
    body_fetched: number | null;
    messages_returned: number | null;
    last_uid: number | null;
    has_more: boolean | null;
  };
  outcomes: {
    scanned: number;
    not_job_related: number;
    before_since: number;
    duplicates: number;
    budget_deferred: number;
  };
}

export interface JobMailRun {
  id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
  trigger: string;
  progress: { scanned: number; candidates: number; duplicates: number; deferred: number; failed_folders: string[]; read_diagnostics?: JobMailReadDiagnostic[] };
  error_code: string | null;
}
export interface JobMailConnection {
  id: string;
  email_masked: string;
  provider: 'qq' | 'synthetic';
  status: 'connected' | 'disconnected' | 'credential_delete_pending';
  folders: string[];
  scope_version: number;
  sync_mode: 'manual' | 'automatic';
  interval_minutes: number;
  ai_enabled: false;
  start_at: string;
  next_run_at: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  not_before_at: string | null;
}
export interface JobMailStatus {
  capabilities: { real_connection: boolean; ai_recognition: false; synthetic_connection: boolean };
  connection: JobMailConnection | null;
  run: JobMailRun | null;
  budget: { limit: number; used: number; remaining: number };
  execution_location: string;
  available_folders: string[];
}

export interface JobMailSuggestionPage {
  items: JobMailSuggestion[];
  total: number;
  pending_count: number;
  has_more: boolean;
}
