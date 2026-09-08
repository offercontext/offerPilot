import { createApiClient } from '@/services/http';
import type { ActionPresentationV1, PilotPresentationSnapshot } from './contracts';

const http = createApiClient({ baseURL: '/api', timeout: 15000 });
export async function getPilotPresentation(conversationId: number): Promise<PilotPresentationSnapshot> {
  const { data } = await http.get<PilotPresentationSnapshot>(`/chat/conversations/${conversationId}/presentation`);
  if (data.conversation_id !== conversationId) throw new Error('presentation_owner_mismatch');
  return data;
}
export async function getProductPresentation(operationId: string): Promise<ActionPresentationV1> {
  const { data } = await http.get<ActionPresentationV1>(`/product-actions/${encodeURIComponent(operationId)}/presentation`);
  if (data.operation_id !== operationId || data.source_kind !== 'product_action') throw new Error('presentation_owner_mismatch');
  return data;
}
