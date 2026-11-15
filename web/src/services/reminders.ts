import { createApiClient } from './http';
import type { ReminderCandidate } from '@/types/reminders';

const http = createApiClient({ baseURL: '/api', timeout: 10000 });

export async function listReminderCandidates(timezone: string, signal?: AbortSignal): Promise<ReminderCandidate[]> {
  const response = await http.get<{ items: ReminderCandidate[] }>('/reminders/candidates', { params: { timezone }, ...(signal ? { signal } : {}) });
  // A missing envelope is not an empty day. Preserve failures for callers.
  if (!Array.isArray(response.data?.items)) throw new Error('reminder_candidates_unavailable');
  return response.data.items;
}
