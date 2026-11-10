export type TrainingAssistance = 'unknown' | 'independent' | 'hinted' | 'reference';
export type TrainingScenario = 'original' | 'retry_timeout' | 'concurrent_retry' | 'payload_conflict' | 'transaction_gap' | 'expired_receipt' | 'stale_revision';
export interface TrainingDecisionAnswer {
  rubric_version: 'idempotency_retry.v1';
  question_id: string;
  choice_id: string;
}
export interface TrainingDecisionQuestion {
  rubric_version: 'idempotency_retry.v1';
  question_id: string;
  prompt: string;
  choices: Array<{ id: string; text: string }>;
}
export interface TrainingDecisionFeedback {
  scope: 'selected_option_only';
  text_assessment: 'not_evaluated';
  outcome: 'supported' | 'contradicted' | 'abstain';
  record_id: number;
  rubric_version: string | null;
  question_id: string | null;
  choice_id: string | null;
  rule_id: string | null;
  reason: string;
  next_step: string | null;
}
export interface TrainingEvidenceRecord {
  feedback?: TrainingDecisionFeedback;
  decision_answer?: TrainingDecisionAnswer | null;
  id: number;
  scenario_id: TrainingScenario;
  answer: string;
  assistance: TrainingAssistance;
  created_at: string;
  updated_at: string;
  withdrawn: boolean;
}
export interface TrainingEvidenceState {
  pool?: { version: 'idempotency_retry_v1'; total: number; practiced_scenario_ids: TrainingScenario[]; remaining_scenario_ids: TrainingScenario[]; exhausted: boolean };
  revision: number;
  skill_id: 'idempotency_retry';
  available: boolean;
  reason: string;
  records: TrainingEvidenceRecord[];
  scenarios: Array<{ id: TrainingScenario; prompt: string; decision_question?: TrainingDecisionQuestion | null }>;
  recommendation: null | {
    scenario_id: TrainingScenario;
    prompt: string;
    reason: string;
    evidence_record_ids: number[];
    comparison: string;
  };
}
interface CommandIdentity { expected_revision: number; idempotency_key: string }
export type TrainingEvidenceCommand = CommandIdentity & (
  | { action: 'record'; skill_id: 'idempotency_retry'; scenario_id: TrainingScenario; answer: string; assistance: TrainingAssistance; decision_answer?: TrainingDecisionAnswer | null }
  | { action: 'correct'; record_id: number; answer: string; assistance: TrainingAssistance; decision_answer?: TrainingDecisionAnswer | null }
  | { action: 'withdraw'; record_id: number }
);

export interface TrainingEvidenceDraft {
  decisionAnswer?: TrainingDecisionAnswer | null;
  retest?: boolean;
  retestScenarioChosen?: boolean;
  scenario: TrainingScenario;
  answer: string;
  assistance: TrainingAssistance;
  editingId: number | null;
  command: TrainingEvidenceCommand | null;
}
