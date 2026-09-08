export type SourceState = 'current' | 'changed' | 'missing';
export type ApplicationOutcomeStage = 'applied' | 'screening' | 'written_test' | 'interview' | 'offer' | 'closed';
export type ApplicationOutcomeResult = 'advanced' | 'rejected' | 'withdrawn' | 'no_response' | 'offer_received' | 'other';
export type ApplicationFeedbackTag = 'technical_depth' | 'communication' | 'system_design' | 'domain_experience' | 'leadership' | 'collaboration' | 'other';

export interface ApplicationSubmissionSnapshot {
  id: number;
  application_id: number;
  resume_id: number;
  resume_title: string;
  jd_version_id: number;
  jd_version_number: number;
  material_kit_id: number | null;
  resume_snapshot: Record<string, unknown>;
  jd_snapshot: string;
  material_snapshot: Record<string, unknown> | null;
  note: string;
  source_kind: 'ui' | 'pilot';
  source_states: Record<'resume' | 'jd' | 'material', SourceState>;
  submitted_at: string;
  created_at: string;
}

export interface CreateApplicationSubmissionSnapshotInput {
  resume_id: number;
  jd_version_id: number;
  material_kit_id: number | null;
  submitted_at: string;
  note: string;
  idempotency_key: string;
}

export interface ApplicationOutcome {
  id: number;
  application_id: number;
  submission_snapshot_id: number;
  application_event_id: number | null;
  stage: ApplicationOutcomeStage;
  result: ApplicationOutcomeResult;
  feedback_text: string;
  reflection_text: string;
  next_action_text: string;
  feedback_tags: ApplicationFeedbackTag[];
  source_kind: 'ui' | 'pilot';
  occurred_at: string;
  created_at: string;
}

export interface CreateApplicationOutcomeInput {
  submission_snapshot_id: number;
  application_event_id: number | null;
  stage: ApplicationOutcomeStage;
  result: ApplicationOutcomeResult;
  feedback_text: string;
  reflection_text: string;
  next_action_text: string;
  feedback_tags: ApplicationFeedbackTag[];
  occurred_at: string;
  idempotency_key: string;
}

export interface ApplicationOutcomeSummary {
  total: number;
  stage_counts: Record<string, number>;
  result_counts: Record<string, number>;
  feedback_tag_counts: Record<string, number>;
  next_actions_pending: number;
}
