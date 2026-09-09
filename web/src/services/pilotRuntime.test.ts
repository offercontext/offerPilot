import { afterEach, expect, it, vi } from 'vitest';
import { getRuntimeRequestExecution, RuntimeEndedError, submitRuntimeTurn } from './pilotRuntime';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const accepted = {
  protocol_version: 'pilot-runtime-v1', turn_id: 'turn-one', conversation_id: 7,
  execution_generation: 1, state: 'running',
};
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
const events = (frames: unknown[]) => new Response(frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join(''), {
  headers: { 'content-type': 'text/event-stream' },
});
const completed = { event: 'completed', event_seq: 1, turn_id: 'turn-one', conversation_id: 7, execution_generation: 1,
  data: { response: { type: 'message', conversation_id: 7, message: '保存的回复' } } };

it.each(['queued', 'accepted'])('recovers original %s admission as an active execution', async (state) => {
  globalThis.fetch = vi.fn().mockResolvedValueOnce(json({ ...accepted, request_id: 'original-request', state }));
  expect(await getRuntimeRequestExecution('original-request')).toMatchObject({ turn_id: 'turn-one', state: 'running', protocol: 'pilot-runtime-v1' });
});

it('submits once and observes using GET after acceptance', async () => {
  const onAccepted = vi.fn();
  const fetcher = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, events: [], event_cursor: 'cursor-0' }))
    .mockResolvedValueOnce(events([completed]));
  globalThis.fetch = fetcher;
  const response = await submitRuntimeTurn({ request_id: 'request-one', message: 'hi' }, { onAccepted });
  expect(response).toMatchObject({ type: 'message', message: '保存的回复', turn_id: 'turn-one' });
  expect(onAccepted).toHaveBeenCalledWith({ conversationId: 7, turnId: 'turn-one', executionGeneration: 1, protocol: 'pilot-runtime-v1' });
  expect(fetcher.mock.calls.map((call) => call[1]?.method ?? 'GET')).toEqual(['POST', 'GET', 'GET']);
  expect(fetcher.mock.calls[2][0]).toContain('/turn-one/events?after=cursor-0');
});

it('applies the durable snapshot before opening the transient event stream', async () => {
  const order: string[] = [];
  const durable = { schema_version: 1, conversation_id: 7, mode: 'snapshot' as const, high_watermark: 3,
    items: [], cursor: 'p2-hwm-3', next_cursor: null };
  const fetcher = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, events: [], durable, snapshot_cursor: 'runtime-hwm-0' }))
    .mockImplementationOnce(async () => { order.push('events'); return events([completed]); });
  globalThis.fetch = fetcher;
  await submitRuntimeTurn({ message: 'hi' }, { onSnapshot: async (page) => {
    expect(page).toEqual(durable); order.push('snapshot');
  } });
  expect(order).toEqual(['snapshot', 'events']);
});

it('reconnects by reading saved state and never repeats the submission', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, events: [], event_cursor: 'cursor-0' }))
    .mockResolvedValueOnce(events([]))
    .mockResolvedValueOnce(json({ ...accepted, state: 'completed', terminal: { response: completed.data.response } }));
  globalThis.fetch = fetcher;
  expect(await submitRuntimeTurn({ request_id: 'request-one', message: 'hi' })).toMatchObject({ message: '保存的回复' });
  expect(fetcher.mock.calls.filter((call) => call[1]?.method === 'POST')).toHaveLength(1);
});

it('rejects events from another generation without publishing their content', async () => {
  const onEvent = vi.fn();
  const fetcher = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, events: [], event_cursor: 'cursor-0' }))
    .mockResolvedValueOnce(events([{ ...completed, execution_generation: 2 }]))
    .mockResolvedValueOnce(json({ ...accepted, state: 'completed', terminal: { response: completed.data.response } }));
  globalThis.fetch = fetcher;
  await submitRuntimeTurn({ request_id: 'request-one', message: 'hi' }, { onEvent });
  expect(onEvent).not.toHaveBeenCalled();
});

it('aborting observation never sends interrupt or confirm', async () => {
  const controller = new AbortController();
  const fetcher = vi.fn().mockResolvedValueOnce(json(accepted)).mockImplementationOnce(async () => {
    controller.abort(); throw new DOMException('aborted', 'AbortError');
  });
  globalThis.fetch = fetcher;
  await expect(submitRuntimeTurn({ request_id: 'request-one', message: 'hi' }, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
  expect(fetcher.mock.calls.filter((call) => call[1]?.method === 'POST')).toHaveLength(1);
});

it('rejects conflicting nested admission identity', async () => {
  globalThis.fetch = vi.fn().mockResolvedValueOnce(json({ ...accepted, execution: { ...accepted, conversation_id: 8 } }));
  await expect(submitRuntimeTurn({ message: 'hi' })).rejects.toThrow('runtime_identity_conflict');
});

it('does not return a terminal response from a mismatched snapshot generation', async () => {
  globalThis.fetch = vi.fn().mockResolvedValueOnce(json(accepted)).mockResolvedValueOnce(json({
    ...accepted, execution_generation: 2, terminal: { response: completed.data.response },
  }));
  await expect(submitRuntimeTurn({ message: 'hi' })).rejects.toBeInstanceOf(RuntimeEndedError);
});

it('validates terminal response generation before exposing its event', async () => {
  const onEvent = vi.fn();
  globalThis.fetch = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, events: [], event_cursor: 'cursor-0' }))
    .mockResolvedValueOnce(events([{ ...completed, data: { response: { ...completed.data.response, execution_generation: 2 } } }]))
    .mockResolvedValueOnce(json({ ...accepted, state: 'result_unknown' }));
  await expect(submitRuntimeTurn({ message: 'hi' }, { onEvent })).rejects.toBeInstanceOf(RuntimeEndedError);
  expect(onEvent).not.toHaveBeenCalled();
});

it('resynchronizes kind-based control frames through a read of the original task', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, events: [], event_cursor: 'cursor-0' }))
    .mockResolvedValueOnce(events([{ ...accepted, kind: 'resync_required', event_seq: 0, data: {} }]))
    .mockResolvedValueOnce(json({ ...accepted, state: 'completed', terminal: { response: completed.data.response } }));
  globalThis.fetch = fetcher;
  expect(await submitRuntimeTurn({ message: 'hi' })).toMatchObject({ message: '保存的回复' });
  expect(fetcher.mock.calls.filter((call) => call[1]?.method === 'POST')).toHaveLength(1);
});

it.each([401, 403, 404, 410])('preserves observation HTTP %s and stops retrying', async (status) => {
  const fetcher = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, events: [], event_cursor: 'cursor-0' }))
    .mockResolvedValueOnce(new Response('{}', { status }));
  globalThis.fetch = fetcher;
  await expect(submitRuntimeTurn({ message: 'hi' })).rejects.toMatchObject({ status });
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it('presents saved unknown status without an execution retry', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, state: 'result_unknown', events: [] }))
    .mockResolvedValueOnce(json({ ...accepted, state: 'result_unknown' }));
  globalThis.fetch = fetcher;
  await expect(submitRuntimeTurn({ message: 'hi' })).rejects.toMatchObject({ code: 'runtime_result_unknown', state: 'result_unknown' });
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it('flushes a split UTF-8 terminal frame at EOF without a trailing blank line', async () => {
  const encoded = new TextEncoder().encode(`data: ${JSON.stringify(completed)}`);
  const stream = new ReadableStream({ start(controller) {
    for (let index = 0; index < encoded.length; index += 1) controller.enqueue(encoded.slice(index, index + 1));
    controller.close();
  } });
  globalThis.fetch = vi.fn().mockResolvedValueOnce(json(accepted))
    .mockResolvedValueOnce(json({ ...accepted, events: [], event_cursor: 'cursor-0' }))
    .mockResolvedValueOnce(new Response(stream));
  expect(await submitRuntimeTurn({ message: 'hi' })).toMatchObject({ message: '保存的回复' });
});
