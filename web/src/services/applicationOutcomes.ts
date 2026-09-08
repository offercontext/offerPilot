import type {
  ApplicationOutcome,
  ApplicationOutcomeSummary,
  ApplicationSubmissionSnapshot,
  CreateApplicationOutcomeInput,
  CreateApplicationSubmissionSnapshotInput,
} from '@/types/applicationOutcome';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 20_000 });

export async function listSubmissionSnapshots(applicationId: number): Promise<ApplicationSubmissionSnapshot[]> {
  const { data } = await http.get<ApplicationSubmissionSnapshot[]>(`/applications/${applicationId}/submission-snapshots`);
  return data;
}

export async function createSubmissionSnapshot(applicationId: number, input: CreateApplicationSubmissionSnapshotInput): Promise<ApplicationSubmissionSnapshot> {
  const { data } = await http.post<ApplicationSubmissionSnapshot>(`/applications/${applicationId}/submission-snapshots`, input);
  return data;
}

export async function listApplicationOutcomes(applicationId: number): Promise<ApplicationOutcome[]> {
  const { data } = await http.get<ApplicationOutcome[]>(`/applications/${applicationId}/outcomes`);
  return data;
}

export async function createApplicationOutcome(applicationId: number, input: CreateApplicationOutcomeInput): Promise<ApplicationOutcome> {
  const { data } = await http.post<ApplicationOutcome>(`/applications/${applicationId}/outcomes`, input);
  return data;
}

export async function getApplicationOutcomeSummary(applicationId: number): Promise<ApplicationOutcomeSummary> {
  const { data } = await http.get<ApplicationOutcomeSummary>(`/applications/${applicationId}/outcome-summary`);
  return data;
}
