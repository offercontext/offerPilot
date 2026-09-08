export type OpportunityFitRecommendation = 'advance' | 'hold' | 'decline';

export interface OpportunityFitEvidenceRef {
  source: 'jd' | 'resume' | 'user_assertion';
  path: string;
  excerpt: string;
}

export interface OpportunityFitTriage {
  summary: { text: string; evidence_refs: OpportunityFitEvidenceRef[] };
  recommendation: OpportunityFitRecommendation;
  hard_constraints: Array<{
    id: string;
    requirement: string;
    status: 'met' | 'unmet' | 'unknown';
    explanation: string;
    evidence_refs: OpportunityFitEvidenceRef[];
  }>;
  fit_signals: Array<{
    id: string;
    statement: string;
    evidence_refs: OpportunityFitEvidenceRef[];
  }>;
  gaps: Array<{
    id: string;
    requirement: string;
    kind: 'required' | 'preferred';
    candidate_status: 'met' | 'unmet' | 'unknown';
    evidence_refs: OpportunityFitEvidenceRef[];
  }>;
  deadline: {
    status: 'stated' | 'not_stated';
    text: string;
    evidence_refs: OpportunityFitEvidenceRef[];
  };
  next_questions: string[];
}

export interface OpportunityFitDeepReview {
  strengths: Array<{ id: string; statement: string; evidence_refs: OpportunityFitEvidenceRef[] }>;
  gaps_to_address: Array<{ id: string; statement: string; evidence_refs: OpportunityFitEvidenceRef[] }>;
  questions_to_clarify: Array<{ id: string; statement: string; evidence_refs: OpportunityFitEvidenceRef[] }>;
  recommended_path: 'prepare_materials' | 'clarify_first' | 'do_not_pursue';
  next_actions: Array<{ id: string; label: string; kind: 'open_material_kit' | 'add_assertion' | 'record_deadline' }>;
}

export interface OpportunityFitSource {
  application: { id: number; company_name: string; position_name: string };
  resume: { id: number; title: string; sha256: string };
  jd: { source_label: string; sha256: string; text: string };
  candidate_assertions: Array<{ index: number; text: string }>;
}

export interface OpportunityFitReviewSummary {
  schema_version: 1;
  id: number;
  application_id: number;
  resume_id: number | null;
  status: 'triage_complete' | 'deep_reviewed';
  summary: OpportunityFitTriage['summary'];
  recommendation: OpportunityFitRecommendation;
  source_fingerprint_sha256: string;
  triage_sha256: string;
  deep_review_sha256: string | null;
  created_at: string;
  deep_reviewed_at: string | null;
}

export interface OpportunityFitReview extends OpportunityFitReviewSummary {
  source: OpportunityFitSource;
  triage: OpportunityFitTriage;
  deep_review: OpportunityFitDeepReview | null;
}

export interface CreateOpportunityFitReviewInput {
  resume_id: number;
  jd_text: string;
  jd_source_label: string;
  candidate_assertions: string[];
  idempotency_key: string;
}

export type OpportunityFitV2EvidenceRef = {
  source: 'jd' | 'resume' | 'user_assertion';
  path: string;
  excerpt: string;
};

export interface OpportunityFitV2Proposal {
  schema_version: 2;
  stage: 'triage' | 'deep_review';
  source: { kind: 'opportunity_fit'; contract_version: 'opportunity_fit.v2'; snapshot_version: '1' };
  summary: { text: string; rationale: string; evidence_refs: OpportunityFitV2EvidenceRef[] };
  conditions: Array<{ id: string; text: string; rationale: string; evidence_refs: OpportunityFitV2EvidenceRef[] }>;
  risks: Array<{ id: string; text: string; rationale: string; evidence_refs: OpportunityFitV2EvidenceRef[] }>;
  questions: Array<{ question_id: string; text: string; evidence_refs: OpportunityFitV2EvidenceRef[] }>;
  next_steps: Array<{ id: string; text: string; rationale: string; evidence_refs: OpportunityFitV2EvidenceRef[] }>;
}

export interface OpportunityFitV2StageResponse {
  id: number;
  review_id: number;
  stage_id: number;
  application_id: number;
  jd_version_id?: number | null;
  resume_id: number | null;
  stage: 'triage' | 'deep_review';
  schema_version: 2;
  stage_status: 'generating' | 'provider_unknown' | 'ready' | 'confirmed' | 'source_conflict';
  parent_triage_stage_id: number | null;
  idempotency_key: string;
  source_fingerprint_sha256: string;
  proposal_sha256: string;
  proposal?: OpportunityFitV2Proposal;
  confirmation_token?: string;
  created_at: string;
}

export interface OpportunityFitV2SessionSummary {
  id: number;
  review_id: number;
  application_id: number;
  schema_version: 2;
  status: 'active';
  triage_idempotency_key: string;
  stage_count: number;
  latest_stage: OpportunityFitV2StageResponse | null;
  created_at: string;
}

export interface OpportunityFitV2SessionResponse {
  id: number;
  review_id: number;
  application_id: number;
  schema_version: 2;
  status: 'active';
  triage_idempotency_key: string;
  stages: OpportunityFitV2StageResponse[];
  created_at: string;
}

export interface OpportunityFitV2Draft {
  applicationId: number;
  resumeId?: number;
  jdText: string;
  jdVersionId?: number;
  assertionsText: string;
  triageKey: string | null;
  deepKey: string | null;
  triage: OpportunityFitV2StageResponse | null;
  deep: OpportunityFitV2StageResponse | null;
  historical: boolean;
  resultUnknown: boolean;
  error: string | null;
}

export function createOpportunityFitV2Draft(applicationId: number): OpportunityFitV2Draft {
  return {
    applicationId,
    resumeId: undefined,
    jdText: '',
    jdVersionId: undefined,
    assertionsText: '',
    triageKey: null,
    deepKey: null,
    triage: null,
    deep: null,
    historical: false,
    resultUnknown: false,
    error: null,
  };
}

export interface CreateOpportunityFitV2Input {
  schema_version: 2;
  resume_id: number;
  jd_version_id: number;
  jd_source_label: string;
  candidate_assertions: string[];
  idempotency_key: string;
}
