// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createChatSubmission, listPendingStarts, forgetPendingStart } from './chatSubmission';
import { streamChat } from './chat';

afterEach(() => {
  vi.unstubAllGlobals();
  for (const pending of listPendingStarts()) forgetPendingStart(pending.requestId);
});

describe('logical chat submission identity', () => {
  it('retains the same key and frozen context after an unknown transport result', async () => {
    const context = { context_type: 'application', context_ref: '7', attachments: [{ kind: 'resume' as const, id: '3', label: '简历' }] };
    const submission = createChatSubmission('私密问题', undefined, context);
    context.attachments[0].id = '9';
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ type: 'turn_recovered', conversation_id: 12, turn_id: 'turn-1' }), { headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetcher);
    await expect(streamChat(submission.message, submission.conversationId, submission.context, { requestId: submission.requestId })).rejects.toThrow();
    expect(listPendingStarts().map((pending) => pending.requestId)).toContain(submission.requestId);
    expect(JSON.stringify(localStorage)).not.toContain('私密问题');
    const recovered = await streamChat(submission.message, submission.conversationId, submission.context, { requestId: submission.requestId });
    expect(recovered.type).toBe('turn_recovered');
    const bodies = fetcher.mock.calls.map((call) => JSON.parse(call[1].body));
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[1].attachments[0].id).toBe('3');
    expect(listPendingStarts()).toEqual([]);
  });

  it('gives a deliberate new submission a new key even for identical text', () => {
    const first = createChatSubmission('相同问题', 7, {});
    const second = createChatSubmission('相同问题', 7, {});
    expect(first.requestId).not.toBe(second.requestId);
    expect(first.requestId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it('clears a new validation rejection but retains an ambiguous server failure', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: 'invalid request' }), { status: 422, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: 'response lost' }), { status: 503, headers: { 'content-type': 'application/json' } })));
    await expect(streamChat('无效请求', 0, {})).rejects.toThrow();
    expect(listPendingStarts()).toEqual([]);
    await expect(streamChat('结果未知', 0, {})).rejects.toThrow();
    expect(listPendingStarts()).toHaveLength(1);
  });
});
