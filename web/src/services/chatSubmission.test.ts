// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createChatSubmission, listPendingStarts, forgetPendingStart, rememberPendingStart, markPendingStartAccepted, forgetConversationStarts, pendingStartsSnapshot, subscribePendingStarts, settlePendingStartForExecution } from './chatSubmission';
import { streamChat } from './chat';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  for (const pending of listPendingStarts()) forgetPendingStart(pending.requestId);
  localStorage.clear();
});

describe('pending submissions shared by tabs', () => {
  // Interleave a second tab after the first tab has read, just before its write.
  function beforeNextMutation(otherTab: () => void) {
    let fired = false;
    for (const method of ['setItem', 'removeItem'] as const) {
      const original = Storage.prototype[method];
      vi.spyOn(Storage.prototype, method).mockImplementation(function (this: Storage, key: string, value?: string) {
        if (!fired) { fired = true; otherTab(); }
        return original.call(this, key, value!);
      });
    }
    return () => expect(fired).toBe(true);
  }

  it.each(['remember', 'accepted', 'forget', 'forgetConversation'] as const)('%s cannot overwrite a concurrent submission from another tab', (operation) => {
    const first = crypto.randomUUID();
    const second = crypto.randomUUID();
    if (operation !== 'remember') rememberPendingStart(first, 7);
    const assertInterleaved = beforeNextMutation(() => rememberPendingStart(second, 8));
    if (operation === 'remember') rememberPendingStart(first, 7);
    if (operation === 'accepted') markPendingStartAccepted(first, 7, 'turn-1');
    if (operation === 'forget') forgetPendingStart(first);
    if (operation === 'forgetConversation') forgetConversationStarts(7);
    assertInterleaved();
    expect(listPendingStarts()).toContainEqual({ requestId: second, conversationId: 8 });
    if (operation === 'remember' || operation === 'accepted') {
      expect(listPendingStarts()).toHaveLength(2);
      expect(listPendingStarts().find((item) => item.requestId === first)).toEqual({ requestId: first, conversationId: 7,
        ...(operation === 'accepted' ? { acceptedConversationId: 7, turnId: 'turn-1' } : {}) });
    } else expect(listPendingStarts()).toHaveLength(1);
  });

  it('retains legacy entries without rewriting the shared array and suppresses forgotten ones after reload', async () => {
    const first = crypto.randomUUID();
    const second = crypto.randomUUID();
    const legacy = JSON.stringify([{ requestId: first, conversationId: 7 }]);
    localStorage.setItem('offerpilot.pending_starts.v1', legacy);
    rememberPendingStart(second, 8);
    markPendingStartAccepted(first, 9, 'turn-1');
    expect(listPendingStarts()).toHaveLength(2);
    forgetConversationStarts(9);
    expect(listPendingStarts()).toEqual([{ requestId: second, conversationId: 8 }]);
    expect(localStorage.getItem('offerpilot.pending_starts.v1')).toBe(legacy);
    vi.resetModules();
    const reloaded = await import('./chatSubmission');
    expect(reloaded.listPendingStarts()).toEqual([{ requestId: second, conversationId: 8 }]);
  });

  it('notifies local and other-tab readers and keeps snapshots stable across storage key order', () => {
    const first = crypto.randomUUID();
    const second = crypto.randomUUID();
    const listener = vi.fn();
    const unsubscribe = subscribePendingStarts(listener);
    rememberPendingStart(first, 7);
    rememberPendingStart(second, 8);
    expect(listener).toHaveBeenCalledTimes(2);
    const snapshot = pendingStartsSnapshot();
    const key = localStorage.key(0)!;
    const value = localStorage.getItem(key)!;
    localStorage.removeItem(key);
    localStorage.setItem(key, value);
    window.dispatchEvent(new StorageEvent('storage', { key, newValue: value }));
    expect(listener).toHaveBeenCalledTimes(3);
    expect(pendingStartsSnapshot()).toBe(snapshot);
    unsubscribe();
    window.dispatchEvent(new StorageEvent('storage', { key }));
    expect(listener).toHaveBeenCalledTimes(3);
  });

  it('preserves recovery in this tab if storage writes fail', () => {
    const requestId = crypto.randomUUID();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    rememberPendingStart(requestId, 7);
    markPendingStartAccepted(requestId, 9, 'turn-1');
    expect(listPendingStarts()).toEqual([{ requestId, conversationId: 7, acceptedConversationId: 9, turnId: 'turn-1' }]);
    forgetPendingStart(requestId);
    expect(listPendingStarts()).toEqual([]);
  });
});

describe('logical chat submission identity', () => {
  it('settles only proven terminal execution markers', () => {
    const completedRequest = crypto.randomUUID();
    const unknownRequest = crypto.randomUUID();
    rememberPendingStart(completedRequest, 0);
    markPendingStartAccepted(completedRequest, 12, 'turn-completed');
    rememberPendingStart(unknownRequest, 0);
    markPendingStartAccepted(unknownRequest, 12, 'turn-unknown');

    settlePendingStartForExecution({
      conversation_id: 12,
      turn_id: 'turn-completed',
      state: 'completed',
    });
    expect(listPendingStarts().map((item) => item.requestId)).toEqual([unknownRequest]);

    settlePendingStartForExecution({
      conversation_id: 12,
      turn_id: 'turn-unknown',
      state: 'result_unknown',
    });
    expect(listPendingStarts().map((item) => item.requestId)).toEqual([unknownRequest]);
    settlePendingStartForExecution({
      conversation_id: 99,
      turn_id: 'turn-unknown',
      state: 'completed',
    });
    expect(listPendingStarts().map((item) => item.requestId)).toEqual([unknownRequest]);
  });

  it('uses the server admission request proof when the accepted turn identity was lost', () => {
    const requestId = crypto.randomUUID();
    const mismatchedPairRequest = crypto.randomUUID();
    rememberPendingStart(requestId, 0);
    rememberPendingStart(mismatchedPairRequest, 0);
    markPendingStartAccepted(mismatchedPairRequest, 99, 'other-turn');

    settlePendingStartForExecution({
      conversation_id: 12,
      turn_id: 'turn-completed',
      state: 'completed',
      submission_request_id: requestId,
    });
    expect(listPendingStarts().map((item) => item.requestId)).toEqual([mismatchedPairRequest]);

    const invalidProof = crypto.randomUUID();
    rememberPendingStart(invalidProof, 0);
    settlePendingStartForExecution({
      conversation_id: 12,
      turn_id: 'turn-unknown',
      state: 'completed',
      submission_request_id: 'not-a-uuid',
    });
    expect(listPendingStarts().map((item) => item.requestId)).toEqual(
      expect.arrayContaining([mismatchedPairRequest, invalidProof]),
    );
    expect(listPendingStarts()).toHaveLength(2);
  });

  it('retains the same key and frozen context after an unknown transport result', async () => {
    const context = { context_type: 'application', context_ref: '7', attachments: [{ kind: 'resume' as const, id: '3', label: '简历' }] };
    const submission = createChatSubmission('私密问题', undefined, context);
    context.attachments[0].id = '9';
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        protocol_version: 'pilot-runtime-v1', conversation_id: 12, turn_id: 'turn-1',
        execution_generation: 1, state: 'completed',
        terminal: { response: { type: 'turn_recovered', conversation_id: 12, turn_id: 'turn-1' } },
      }), { headers: { 'content-type': 'application/json' } }));
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

  it('clears a proven capacity rejection before admission but retains an unrelated 429', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: 'queue full', error_code: 'runtime_capacity_exhausted' }), { status: 429 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: 'upstream limit' }), { status: 429 })));
    await expect(streamChat('尚未接纳', 0, {})).rejects.toThrow();
    expect(listPendingStarts()).toEqual([]);
    await expect(streamChat('接纳结果未知', 0, {})).rejects.toThrow();
    expect(listPendingStarts()).toHaveLength(1);
  });
});
