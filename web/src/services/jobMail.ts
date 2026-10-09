import { createApiClient } from './http';
import type { JobMailImportInput, JobMailRun, JobMailStatus, JobMailPreview, JobMailPreviewInput, JobMailReceipt, JobMailSuggestion, JobMailSuggestionPage } from '@/types/jobMail';

const http = createApiClient({ baseURL: '/api/job-mail', timeout: 20_000 });
export const JOB_MAIL_QUERY_KEY = ['job-mail'];
export const JOB_MAIL_SUGGESTIONS_KEY = [...JOB_MAIL_QUERY_KEY, 'suggestions'];
export const JOB_MAIL_COUNT_KEY = [...JOB_MAIL_QUERY_KEY, 'count'];
export const JOB_MAIL_STATUS_KEY = [...JOB_MAIL_QUERY_KEY, 'status'];

export async function listJobMailSuggestions(params: { status?: string; limit?: number; offset?: number } = {}): Promise<JobMailSuggestionPage> {
  return (await http.get<JobMailSuggestionPage>('/suggestions', { params })).data;
}
export async function getJobMailPendingCount(): Promise<number> {
  return (await listJobMailSuggestions({ status: 'unprocessed', limit: 1 })).pending_count;
}
export async function getJobMailSuggestion(id: string): Promise<JobMailSuggestion> {
  return (await http.get<JobMailSuggestion>(`/suggestions/${id}`)).data;
}
export async function importJobMail(input: JobMailImportInput): Promise<{ items: JobMailSuggestion[]; deduplicated: boolean; evidence_id: string }> {
  return (await http.post('/imports', input)).data;
}
export async function previewJobMail(id: string, input: JobMailPreviewInput): Promise<JobMailPreview> {
  return (await http.post<JobMailPreview>(`/suggestions/${id}/preview`, input)).data;
}
function verifiedReceipt(value: JobMailReceipt, operationId: string, suggestionId?: string): JobMailReceipt {
  if (!value || value.operation_id !== operationId || (suggestionId && value.suggestion_id !== suggestionId)
      || !Number.isSafeInteger(value.application_event_id) || value.application_event_id <= 0
      || !value.after || !Number.isSafeInteger(value.after.application_id) || Number(value.after.application_id) <= 0
      || !Number.isFinite(Date.parse(value.confirmed_at))) {
    throw new Error('邮件确认回执不完整，请按原操作标识核对结果');
  }
  return value;
}
export async function confirmJobMail(id: string, input: JobMailPreviewInput & { preview_token: string; explicit_confirmation: true }): Promise<JobMailReceipt> {
  return verifiedReceipt((await http.post<JobMailReceipt>(`/suggestions/${id}/confirm`, input)).data, input.operation_id, id);
}
export async function getJobMailReceipt(operationId: string): Promise<JobMailReceipt> {
  return verifiedReceipt((await http.get<JobMailReceipt>(`/receipts/${encodeURIComponent(operationId)}`)).data, operationId);
}
export async function ignoreJobMail(id: string, suggestionVersion: number): Promise<JobMailSuggestion> {
  return (await http.post<JobMailSuggestion>(`/suggestions/${id}/ignore`, { suggestion_version: suggestionVersion })).data;
}

export async function getJobMailStatus(): Promise<JobMailStatus> {
  return (await http.get<JobMailStatus>('/status')).data;
}
export async function updateJobMailSettings(input: { sync_mode: 'manual' | 'automatic'; interval_minutes: number; folders?: string[]; ai_enabled?: false }): Promise<JobMailStatus> {
  return (await http.put<JobMailStatus>('/settings', input)).data;
}
export async function connectSyntheticJobMail(input: { provider: 'synthetic'; email: string; folders: string[]; backfill_days: 0 | 7 }): Promise<JobMailStatus> {
  return (await http.post<JobMailStatus>('/connection', input)).data;
}
export async function syncJobMail(): Promise<JobMailRun> {
  return (await http.post<JobMailRun>('/sync', {})).data;
}
export async function cancelJobMailSync(runId: string): Promise<JobMailRun> {
  return (await http.post<JobMailRun>(`/sync/${encodeURIComponent(runId)}/cancel`, {})).data;
}
export async function disconnectJobMail(): Promise<JobMailStatus> {
  return (await http.post<JobMailStatus>('/disconnect', {})).data;
}
