import { createApiClient } from '@/services/http';
import type { ActionPresentationV1, PilotPresentationSnapshot, PilotTimelinePage } from './contracts';
import { applyTimelinePage, timelinePresentation } from './timeline';

const http = createApiClient({ baseURL: '/api', timeout: 15000 });
export async function getPilotPresentation(conversationId: number, previous?: PilotPresentationSnapshot | null): Promise<PilotPresentationSnapshot> {
  let cache = previous?.conversation_id === conversationId ? previous.timeline ?? null : null;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let working = cache;
    let cursor = working?.cursor;
    try {
      for (let pageNumber = 0; pageNumber < 100; pageNumber += 1) {
        const { data } = await http.get<PilotTimelinePage>(`/chat/conversations/${conversationId}/timeline`, { params: { cursor, limit: 200 } });
        if (data.conversation_id !== conversationId) throw new Error('presentation_owner_mismatch');
        working = applyTimelinePage(working, data);
        if (!data.next_cursor) return timelinePresentation(working);
        cursor = data.next_cursor;
      }
      throw new Error('timeline_pagination_limit');
    } catch (error) {
      const code = (error as { response?: { data?: { error_code?: string } } })?.response?.data?.error_code;
      if (attempt === 0 && code === 'timeline_resync_required') {
        cache = null;
        continue;
      }
      throw error;
    }
  }
  throw new Error('timeline_resync_required');
}
export async function getProductPresentation(operationId: string): Promise<ActionPresentationV1> {
  const { data } = await http.get<ActionPresentationV1>(`/product-actions/${encodeURIComponent(operationId)}/presentation`);
  if (data.operation_id !== operationId || data.source_kind !== 'product_action') throw new Error('presentation_owner_mismatch');
  return data;
}

/** Complete the Runtime's exact P2 snapshot before following its high watermark. */
export async function getPilotPresentationFromPage(first: PilotTimelinePage): Promise<PilotPresentationSnapshot> {
  let working = applyTimelinePage(null, first);
  let next = first.next_cursor;
  for (let pageNumber = 0; next; pageNumber += 1) {
    if (pageNumber >= 99) throw new Error('timeline_pagination_limit');
    const { data } = await http.get<PilotTimelinePage>(`/chat/conversations/${first.conversation_id}/timeline`, {
      params: { cursor: next, limit: 200 },
    });
    if (data.conversation_id !== first.conversation_id || data.high_watermark !== first.high_watermark) throw new Error('timeline_snapshot_boundary_mismatch');
    working = applyTimelinePage(working, data);
    next = data.next_cursor;
  }
  return getPilotPresentation(first.conversation_id, timelinePresentation(working));
}
