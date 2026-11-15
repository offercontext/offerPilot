import axios from 'axios';
import type { TrainingEvidenceCommand, TrainingEvidenceState } from '@/types/trainingEvidence';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 15000 });
export class TrainingEvidenceError extends Error {
  constructor(public readonly status?: number, public readonly code?: string) {
    super('training_evidence_request_failed');
  }
}
function safeError(error: unknown): TrainingEvidenceError {
  const response = axios.isAxiosError(error) ? error.response : undefined;
  return new TrainingEvidenceError(response?.status, typeof response?.data?.error_code === 'string' ? response.data.error_code : undefined);
}
export async function getTrainingEvidence(planId: number): Promise<TrainingEvidenceState> {
  try { return (await http.get(`/interview-practice/plans/${planId}/training-evidence`)).data; }
  catch (error) { throw safeError(error); }
}
export async function changeTrainingEvidence(planId: number, command: TrainingEvidenceCommand): Promise<TrainingEvidenceState> {
  try { return (await http.post(`/interview-practice/plans/${planId}/training-evidence`, command)).data; }
  catch (error) { throw safeError(error); }
}
