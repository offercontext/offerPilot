export interface MockInterviewTurn {
  turn_no: number;
  question: string;
  answer: string;
  question_kind?: 'follow_up' | 'new_topic';
  parent_turn_no?: number | null;
  topic_root_turn_no?: number;
  basis_refs?: Array<{ source: string; path: string; excerpt: string }>;
}

export interface MockInterviewProposal {
  schema_version: string;
  proposal_status: 'normal' | 'safe_empty';
  strengths: MockInterviewFeedbackBlock[];
  practice_points: MockInterviewFeedbackBlock[];
  follow_up_questions: MockInterviewFeedbackBlock[];
  next_practice_steps: MockInterviewFeedbackBlock[];
}

export interface MockInterviewFeedbackBlock {
  id: string;
  text: string;
  evidence_refs: Array<{ source: string; path: string; excerpt: string }>;
}

export interface MockInterviewAttemptResponse {
  attempt_id: number;
  attempt_status: string;
  generation_revision: number;
  operation_id?: string;
  jd_version_id?: number | null;
  context_kind?: 'application_event' | 'quick_practice';
  application_id?: number | null;
  event_id?: number | null;
  practice_case_id?: number | null;
  turn: MockInterviewTurn;
}

export interface MockInterviewQuestionResponse extends MockInterviewAttemptResponse {}

export interface MockInterviewProposalResponse {
  proposal_id: number;
  proposal_status: 'normal' | 'safe_empty';
  proposal_hash: string;
  proposal: MockInterviewProposal;
  operation_id?: string;
}

export interface MockInterviewPendingResponse {
  attempt_id: number;
  attempt_status: 'generating_question' | 'generating_feedback' | 'provider_unknown';
  retry_after_ms: number;
  operation_id?: string;
}

export interface MockInterviewHistoryItem extends MockInterviewProposalResponse {
  attempt_id: number;
  source_fingerprint: string;
  transcript_fingerprint: string;
  created_at: string;
  source_status?: 'current' | 'source_changed';
  turns?: MockInterviewTurn[];
  review_draft?: { draft_id: number; status: string; selected_blocks: unknown[] } | null;
  context_kind?: 'application_event' | 'quick_practice';
  application_id?: number | null;
  event_id?: number | null;
  practice_case_id?: number | null;
}
