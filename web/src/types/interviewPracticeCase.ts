export interface InterviewPracticeCase {
  id: number;
  idempotency_key: string;
  position_name_snapshot: string;
  jd_text_snapshot: string;
  jd_fingerprint_sha256: string;
  resume_id: number;
  resume_content_snapshot: Record<string, unknown>;
  resume_fingerprint_sha256: string;
  status: 'active' | 'archived';
  source_status: 'current' | 'source_changed' | 'unknown';
  created_at: string;
  archived_at: string | null;
}

export interface InterviewPracticeCaseListResponse {
  items: InterviewPracticeCase[];
}

export interface InterviewAttemptContext {
  context_kind: 'application_event' | 'quick_practice';
  application_id: number | null;
  event_id: number | null;
  practice_case_id: number | null;
}
