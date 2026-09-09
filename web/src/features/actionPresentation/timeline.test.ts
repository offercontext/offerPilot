import { describe, expect, it, vi, beforeEach } from 'vitest';
import { applyTimelinePage, timelinePresentation } from './timeline';
import { getPilotPresentation, getPilotPresentationFromPage } from './service';
import type { PilotTimelineItem, PilotTimelinePage } from './contracts';

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@/services/http', () => ({ createApiClient: () => api }));

function item(revision = 1, deleted = false): PilotTimelineItem {
  return {
    schema_version: 1, conversation_id: 7, turn_id: 'turn-1', item_id: 'turn:turn-1:message:10',
    item_type: 'assistant_message', source_refs: ['message:10'], source_revision: `source-${revision}`,
    revision, display_revision: revision, ordinal: 1, change_seq: revision,
    payload_digest: deleted ? '' : `digest-${revision}`, deleted,
    payload: deleted ? null : { schema_version: 1, item_id: 'message:10', message_id: 10,
      operation_id: null, kind: 'assistant_message', content: `版本 ${revision}`, action: null },
  };
}

function page(items: PilotTimelineItem[], high = 1, mode: 'snapshot' | 'changes' = 'snapshot'): PilotTimelinePage {
  return { schema_version: 1, conversation_id: 7, mode, items, high_watermark: high,
    cursor: `checkpoint-${high}`, next_cursor: null };
}

beforeEach(() => api.get.mockReset());

describe('durable timeline merge', () => {
  it('completes a runtime snapshot at its original watermark before reading later changes', async () => {
    api.get.mockResolvedValueOnce({ data: page([], 1) })
      .mockResolvedValueOnce({ data: page([item(2)], 2, 'changes') });
    const recovered = await getPilotPresentationFromPage({ ...page([item()]), next_cursor: 'snapshot-page-2' });
    expect(api.get.mock.calls.map((call) => call[1].params.cursor)).toEqual(['snapshot-page-2', 'checkpoint-1']);
    expect(recovered.items[0].content).toBe('版本 2');
  });

  it('refuses snapshot pages from a different consistency boundary', async () => {
    api.get.mockResolvedValueOnce({ data: page([], 2) });
    await expect(getPilotPresentationFromPage({ ...page([item()]), next_cursor: 'snapshot-page-2' }))
      .rejects.toThrow('timeline_snapshot_boundary_mismatch');
  });
  it('uses monotonic revision and retains tombstones against delayed older updates', () => {
    const first = applyTimelinePage(null, page([item()]));
    const newer = applyTimelinePage(first, page([item(3)], 3, 'changes'));
    const delayed = applyTimelinePage(newer, page([item(2)], 3, 'changes'));
    expect(timelinePresentation(delayed).items[0].content).toBe('版本 3');
    const deleted = applyTimelinePage(delayed, page([item(4, true)], 4, 'changes'));
    const after = applyTimelinePage(deleted, page([item(2)], 4, 'changes'));
    expect(timelinePresentation(after).items).toEqual([]);
    expect(after.items[item().item_id].revision).toBe(4);
  });

  it('does not replace a newer timeline with an older snapshot', () => {
    const newer = applyTimelinePage(null, page([item(3)], 3));
    expect(applyTimelinePage(newer, page([item()], 1))).toBe(newer);
  });

  it('rejects cross-conversation items and unsupported versions', () => {
    expect(() => applyTimelinePage(null, page([{ ...item(), conversation_id: 8 }]))).toThrow();
    expect(() => applyTimelinePage(null, { ...page([item()]), schema_version: 2 })).toThrow();
  });

  it('fetches all pages before publishing a cursor and then requests only changes', async () => {
    api.get.mockResolvedValueOnce({ data: { ...page([item()]), next_cursor: 'page-2' } })
      .mockResolvedValueOnce({ data: page([], 1) })
      .mockResolvedValueOnce({ data: page([item(2)], 2, 'changes') });
    const first = await getPilotPresentation(7);
    expect(api.get.mock.calls[1][1].params.cursor).toBe('page-2');
    const second = await getPilotPresentation(7, first);
    expect(api.get.mock.calls[2][1].params.cursor).toBe('checkpoint-1');
    expect(second.items[0].content).toBe('版本 2');
    expect(second.items[0].timeline_item_id).toBe(item().item_id);
  });

  it('replaces cached content after the server requests resynchronization', async () => {
    const previous = timelinePresentation(applyTimelinePage(null, page([item()])));
    api.get.mockRejectedValueOnce({ response: { data: { error_code: 'timeline_resync_required' } } })
      .mockResolvedValueOnce({ data: page([], 2) });
    const recovered = await getPilotPresentation(7, previous);
    expect(recovered.items).toEqual([]);
    expect(api.get.mock.calls[1][1].params.cursor).toBeUndefined();
  });

  it('does not publish partial pages or mutate the previous cache on a failed read', async () => {
    const previous = timelinePresentation(applyTimelinePage(null, page([item()])));
    api.get.mockResolvedValueOnce({ data: { ...page([item(2)], 2, 'changes'), next_cursor: 'page-2' } })
      .mockRejectedValueOnce(new Error('offline'));
    await expect(getPilotPresentation(7, previous)).rejects.toThrow('offline');
    expect(previous.items[0].content).toBe('版本 1');
    expect(previous.timeline?.cursor).toBe('checkpoint-1');
  });
});
