export type TrainingAssistance = 'unknown' | 'independent' | 'hinted' | 'reference';
export type TrainingScenario = 'original' | 'retry_timeout' | 'concurrent_retry';
export interface TrainingEvidenceRecord {
  id: number;
  scenario_id: TrainingScenario;
  answer: string;
  assistance: TrainingAssistance;
  created_at: string;
  updated_at: string;
  withdrawn: boolean;
}
export interface TrainingEvidenceState {
  revision: number;
  skill_id: 'idempotency_retry';
  available: boolean;
  reason: string;
  records: TrainingEvidenceRecord[];
  scenarios: Array<{ id: TrainingScenario; prompt: string }>;
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
  | { action: 'record'; skill_id: 'idempotency_retry'; scenario_id: TrainingScenario; answer: string; assistance: TrainingAssistance }
  | { action: 'correct'; record_id: number; answer: string; assistance: TrainingAssistance }
  | { action: 'withdraw'; record_id: number }
);

export interface TrainingEvidenceDraft {
  scenario: TrainingScenario;
  answer: string;
  assistance: TrainingAssistance;
  editingId: number | null;
  command: TrainingEvidenceCommand | null;
}
