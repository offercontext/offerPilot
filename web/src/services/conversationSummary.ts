import { createApiClient } from './http';
const http = createApiClient({ baseURL: '/api' });
export interface OlderSummary {
  cached: boolean;
  revision: number;
  summary: { from_message_id: number; through_message_id: number; omitted_messages: number;
    items: Array<{ kind: 'user_statement' | 'model_inference'; source_message_id: number; excerpt: string; truncated: boolean }> };
}
export async function generateOlderSummary(conversationId: number): Promise<OlderSummary> {
  return (await http.post<OlderSummary>(`/conversations/${conversationId}/older-summary`, { confirmed: true })).data;
}
export async function withdrawOlderSummary(conversationId: number): Promise<void> {
  await http.delete(`/conversations/${conversationId}/older-summary`);
}
