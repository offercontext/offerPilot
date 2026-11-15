import { describe, expect, it, vi, beforeEach } from 'vitest';
import { applyTimelinePage, timelinePresentation } from './timeline';
import { getPilotPresentation, getPilotPresentationFromPage } from './service';
import { mergePresentationTurns } from './model';
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
  it.each(['completed', '0:completed', '1:completed', '12:completed'])('hides only the completed boundary in a real turn-shaped page: %s', (sourceRevision) => {
    const boundary: PilotTimelineItem = {
      ...item(), item_id: 'turn:turn-1:run:turn-1', item_type: 'run_boundary',
      source_refs: ['turn:turn-1'], source_revision: sourceRevision,
      payload: { schema_version: 1, item_id: 'run:turn-1', kind: 'run_boundary',
        message_id: null, operation_id: null, content: '本轮记录已保存。', action: null },
    };
    const user: PilotTimelineItem = {
      ...item(), item_id: 'turn:turn-1:message:9', item_type: 'user_message',
      ordinal: 2, change_seq: 2, source_refs: ['message:9'],
      payload: { ...item().payload!, item_id: 'message:9', message_id: 9, kind: 'user_message', content: '独立任务' },
    };
    const reply = { ...item(), ordinal: 3, change_seq: 3 };
    // The backend inserts the boundary before the turn's user and assistant.
    const cache = applyTimelinePage(null, page([reply, boundary, user], 3));
    const before = structuredClone(cache);
    const presentation = timelinePresentation(cache);
    expect(mergePresentationTurns([], presentation).map(({ role, content }) => ({ role, content }))).toEqual([
      { role: 'user', content: '独立任务' }, { role: 'assistant', content: '版本 1' },
    ]);
    expect(presentation.timeline).toBe(cache);
    expect(cache).toEqual(before);
    expect(cache.items[boundary.item_id]).toEqual(boundary);
    expect(presentation.items.map(({ timeline_item_id }) => timeline_item_id)).toEqual([user.item_id, reply.item_id]);
  });

  it.each([
    'failed', 'interrupted', 'result_unknown', '0:failed', '1:interrupted', '12:result_unknown',
    '1:running', '1:waiting_confirmation', '1:stopped', '0:incomplete',
    '-1:completed', '-0:completed', '01:completed', '00:completed', '+1:completed',
    '1.0:completed', '1e1:completed', ':completed', '1:completed:extra', '1:completed-extra',
    '1:completed\n', '1:completed\r\n', '1:completed\u2028', '1\n:completed', ' completed', 'completed\n',
    ' 1:completed', '1:completed ', '1:Completed', '１:completed', '',
  ])('keeps uncertain or noncanonical boundary revision visible: %j', (sourceRevision) => {
    const boundary: PilotTimelineItem = {
      ...item(), item_id: 'turn:turn-1:run:turn-1', item_type: 'run_boundary', source_revision: sourceRevision,
      payload: { ...item().payload!, item_id: 'run:turn-1', kind: 'run_boundary', message_id: null, content: '本轮状态' },
    };
    expect(timelinePresentation(applyTimelinePage(null, page([boundary]))).items).toMatchObject([
      { kind: 'run_boundary', content: '本轮状态' },
    ]);
  });

  it.each((['user_message', 'assistant_message', 'action', 'error_info'] as const).flatMap(kind =>
    ['completed', '0:completed', '1:completed'].map(sourceRevision => ({ kind, sourceRevision })),
  ))('does not suppress $kind with completed-looking revision: $sourceRevision', ({ kind, sourceRevision }) => {
    const reply = { ...item(), item_type: kind, source_revision: sourceRevision, payload: { ...item().payload!, kind } };
    expect(timelinePresentation(applyTimelinePage(null, page([reply]))).items).toMatchObject([
      { kind, content: '版本 1' },
    ]);
  });

  it.each([undefined, null, 1])('does not suppress an untyped boundary revision: %j', (sourceRevision) => {
    const boundary: PilotTimelineItem = {
      ...item(), item_id: 'turn:turn-1:run:turn-1', item_type: 'run_boundary', source_revision: sourceRevision as unknown as string,
      payload: { ...item().payload!, item_id: 'run:turn-1', kind: 'run_boundary', message_id: null },
    };
    expect(timelinePresentation(applyTimelinePage(null, page([boundary]))).items).toHaveLength(1);
  });

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
