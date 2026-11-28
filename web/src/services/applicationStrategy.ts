import type { ApplicationOutcome } from '@/types/applicationOutcome';
import { createApiClient } from './http';

export type StrategyDecision = 'keep' | 'revise' | 'drop';
export interface StrategyArchive {
  id: number;
  application_id: number;
  submitted_at: string;
  company_name: string;
  position_name: string;
  resume_title: string;
  jd_version_number: number;
}
export type StrategyOutcome = Pick<ApplicationOutcome, 'id' | 'application_id' | 'submission_snapshot_id' | 'stage' | 'result' | 'occurred_at'> & {
  has_external_feedback: boolean;
};
export interface StrategyState {
  origin: StrategyOutcome;
  change: { proposal: string; revision: number; applied_detail: string | null; decision: StrategyDecision | null; decided_outcome_id: number | null } | null;
  target: StrategyArchive | null;
  comparable_outcomes: StrategyOutcome[];
}
export type StrategyAction =
  | { action: 'propose'; proposal: string }
  | { action: 'confirm_applied'; expected_revision: number; target_application_id: number; target_snapshot_id: number; applied_detail: string }
  | { action: 'decide'; expected_revision: number; target_outcome_id: number; decision: StrategyDecision };
export type StrategyCommand = StrategyAction & { idempotency_key: string };

const http = createApiClient({ baseURL: '/api', timeout: 20_000 });
const path = (app: number, outcome: number) => `/applications/${app}/outcomes/${outcome}/strategy`;
export async function getStrategy(app: number, outcome: number, signal?: AbortSignal): Promise<StrategyState> {
  return (await http.get<StrategyState>(path(app, outcome), { signal })).data;
}
export async function listStrategyArchives(app: number, outcome: number, targetApp: number, signal?: AbortSignal): Promise<StrategyArchive[]> {
  return (await http.get<StrategyArchive[]>(path(app, outcome) + '/target-snapshots', { params: { target_application_id: targetApp }, signal })).data;
}
export async function writeStrategy(app: number, outcome: number, input: StrategyCommand): Promise<StrategyState> {
  return (await http.post<StrategyState>(path(app, outcome), input)).data;
}
