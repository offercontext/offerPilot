// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatStreamRequestOptions } from '@/services/chat';
import { listPendingStarts, rememberPendingStart } from '@/services/chatSubmission';
import { RuntimeEndedError, type RuntimeObserveOptions } from '@/services/pilotRuntime';
import type { ChatMessage, Conversation, PendingAction, PilotExecution, PilotInterruptResult } from '@/types/chat';
import type { UITurn } from '@/components/ChatPanel/model';
import type { DesktopHaruCommand, DesktopHaruSnapshot } from './desktopHaru';
import { useDesktopHaruOwner } from './useDesktopHaruOwner';
import { usePilotConversationControllerState, type ActiveConversationRequest } from './usePilotConversationController';

// Controller, execution polling and Haru projection are real. Only services and
// the unrelated presentation projection are substituted; no source extraction.
const mocks = vi.hoisted(() => ({
  readExecution: vi.fn<typeof import('@/services/chat').getPilotExecution>(),
  interrupt: vi.fn<typeof import('@/services/chat').interruptPilotExecution>(),
  messages: vi.fn<typeof import('@/services/chat').getConversation>(),
  summaries: vi.fn<typeof import('@/services/chat').listConversations>(),
  streamChat: vi.fn<typeof import('@/services/chat').streamChat>(),
  streamConfirm: vi.fn<typeof import('@/services/chat').streamConfirmAction>(),
  lookup: vi.fn<typeof import('@/services/pilotRuntime').getRuntimeRequestExecution>(),
  observe: vi.fn<typeof import('@/services/pilotRuntime').observeRuntimeTurn>(),
  refresh: vi.fn(), acceptSnapshot: vi.fn(),
  publish: vi.fn<(snapshot: DesktopHaruSnapshot) => void>(),
  reply: vi.fn(), openPending: vi.fn(), send: vi.fn(async () => 'sent' as const),
}));
vi.mock('@/services/chat', () => ({
  getPilotExecution: mocks.readExecution, interruptPilotExecution: mocks.interrupt,
  getConversation: mocks.messages, listConversations: mocks.summaries,
  streamChat: mocks.streamChat, streamConfirmAction: mocks.streamConfirm,
}));
vi.mock('@/services/pilotRuntime', async importOriginal => ({
  ...await importOriginal<typeof import('@/services/pilotRuntime')>(),
  getRuntimeRequestExecution: mocks.lookup, observeRuntimeTurn: mocks.observe,
}));
vi.mock('@/features/actionPresentation/usePilotPresentation', () => ({
  usePilotPresentation: (_id: number | undefined, turns: UITurn[]) => ({
    displayTurns: turns, refreshPresentation: mocks.refresh, acceptRuntimeSnapshot: mocks.acceptSnapshot,
    presentationFailed: false, presentationRefreshing: false,
  }),
}));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const requestId = '12345678-1234-4123-8123-123456789abc';
const nextRequestId = '22345678-1234-4123-8123-123456789abc';
const running: PilotExecution = {
  turn_id: 'recovered-turn', conversation_id: 7, execution_generation: 3,
  state: 'running', protocol: 'pilot-runtime-v1', submission_request_id: requestId,
};
const pending: PendingAction = {
  operation_id: 'pending-operation', tool_name: 'update_application', human: '请确认更新',
  confirmation_token: 'synthetic-confirmation-token', args: { status: 'interview' },
};
const savedMessage: ChatMessage = {
  id: 1, conversation_id: 7, role: 'assistant', content: '已保存的回复', created_at: '',
};
function summary(action: PendingAction | null = null): Conversation {
  return { id: 7, title: 'Recovered conversation', context_type: 'workspace', context_ref: '',
    created_at: '', updated_at: '', pending_action: action };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
const never = <T,>() => new Promise<T>(() => undefined);
let root: Root;
let controller: ReturnType<typeof usePilotConversationControllerState>;
let command: (value: DesktopHaruCommand) => void;
let streamCompletion: ReturnType<typeof import('@/services/chat').streamChat>;
function Harness() {
  controller = usePilotConversationControllerState();
  useDesktopHaruOwner(controller, mocks.openPending);
  return null;
}
function latest() { return mocks.publish.mock.calls[mocks.publish.mock.calls.length - 1][0]; }
function streamOptions() { return mocks.streamChat.mock.calls[mocks.streamChat.mock.calls.length - 1][3] as ChatStreamRequestOptions; }
function observeOptions() { return mocks.observe.mock.calls[mocks.observe.mock.calls.length - 1][1] as RuntimeObserveOptions; }
async function tickLookup() {
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
}
function beginRequest(id = requestId) {
  let request!: ActiveConversationRequest;
  act(() => {
    request = controller.beginActiveRequest('chat', controller.conversationId)!;
    expect(request).not.toBeNull();
    const generation = ++controller.visibleRequestGenerationRef.current;
    streamCompletion = controller.streamChatRequest(request, 'synthetic request', controller.conversationId, {}, {
      requestId: id,
      // The consumer binds the accepted conversation as ChatPanel does. The real
      // controller owns acceptance, polling, request release and aborts.
      onAccepted: identity => {
        if (generation === controller.visibleRequestGenerationRef.current) controller.setConversationId(identity.conversationId);
      },
    });
  });
  return request;
}
async function recoverRunning() {
  const request = beginRequest();
  mocks.lookup.mockResolvedValueOnce(running);
  await tickLookup();
  return request;
}

beforeEach(async () => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  localStorage.clear();
  mocks.readExecution.mockImplementation(() => never());
  mocks.streamChat.mockImplementation(() => never());
  mocks.observe.mockImplementation(() => never());
  mocks.lookup.mockResolvedValue(null);
  mocks.messages.mockResolvedValue([savedMessage]);
  mocks.summaries.mockResolvedValue([summary()]);
  mocks.refresh.mockResolvedValue(undefined);
  mocks.send.mockResolvedValue('sent');
  window.offerpilotDesktop = {
    role: 'owner', publish: mocks.publish, reply: mocks.reply,
    onCommand: handler => { command = handler; return vi.fn(); }, disconnect: vi.fn(),
    windowAction: vi.fn().mockResolvedValue(true), getState: vi.fn(), onState: vi.fn(),
  };
  root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<Harness />); });
  controller.bindActions({}, {
    sendMessage: mocks.send, selectConversation: async () => undefined, startNewChat: () => false,
    retryLastMessage: vi.fn(), clearLastFailure: vi.fn(), handleConfirm: vi.fn(), retryConfirmAction: vi.fn(),
    refreshConfirmationStatus: async () => undefined, clearActiveContext: async () => undefined,
  });
});
afterEach(() => {
  act(() => root.unmount());
  delete window.offerpilotDesktop;
  localStorage.clear();
  vi.useRealTimers();
});

describe('durable running recovery through the real controller and Haru owner', () => {
  it('keeps the public surface busy after a lost accepted frame transfers to GET observation', async () => {
    rememberPendingStart(requestId, 0);
    const request = await recoverRunning();
    expect(mocks.lookup).toHaveBeenCalledWith(requestId, expect.any(AbortSignal));
    expect(request.controller.signal.aborted).toBe(true);
    expect(controller.activeRequestRef.current).toBeNull();
    expect(controller.conversationId).toBe(7);
    expect(controller.executionControl.execution).toEqual(running);
    expect(mocks.observe).toHaveBeenCalledTimes(1);
    expect(mocks.observe).toHaveBeenCalledWith(running, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(observeOptions().signal?.aborted).toBe(false);
    expect(controller.loading).toBe(true);
    expect(controller.taskState).toBe('running');
    expect(latest()).toMatchObject({ conversationId: 7, loading: true, taskState: 'running', canStop: true, canSend: false });
    expect(mocks.publish.mock.calls.filter(([snapshot]) => snapshot.conversationId === 7)
      .every(([snapshot]) => snapshot.loading && snapshot.taskState === 'running')).toBe(true);
    expect(await controller.sendMessage('must not resubmit')).toBe('ignored');
    expect(mocks.send).not.toHaveBeenCalled();
    expect(mocks.streamChat).toHaveBeenCalledTimes(1);
    expect(mocks.interrupt).not.toHaveBeenCalled();
    expect(listPendingStarts()).toEqual([{ requestId, conversationId: 0 }]);
  });

  it('does not publish idle when accepted identity and local subscriber release are batched', async () => {
    const request = beginRequest();
    await act(async () => {
      await Promise.resolve();
      streamOptions().onAccepted?.({ conversationId: 7, turnId: running.turn_id,
        executionGeneration: running.execution_generation, protocol: running.protocol });
      controller.finishActiveRequest(request);
    });
    expect(controller.activeRequestRef.current).toBeNull();
    expect(controller.loading).toBe(true);
    expect(controller.taskState).toBe('running');
    expect(latest()).toMatchObject({ conversationId: 7, loading: true, taskState: 'running', canStop: true, canSend: false });
    expect(mocks.observe).toHaveBeenCalledTimes(1);
    expect(mocks.lookup).not.toHaveBeenCalled();
  });

  it.each(['completed', 'stopped', 'interrupted', 'waiting_confirmation'] as const)(
    'releases recovered busy state after durable polling reports %s', async state => {
      const poll = deferred<PilotExecution>();
      mocks.readExecution.mockReturnValueOnce(poll.promise);
      rememberPendingStart(requestId, 0);
      await recoverRunning();
      const subscription = observeOptions().signal!;
      if (state === 'waiting_confirmation') mocks.summaries.mockResolvedValue([summary(pending)]);
      await act(async () => { poll.resolve({ ...running, state }); });
      expect(controller.executionControl.execution?.state).toBe(state);
      expect(controller.loading).toBe(false);
      expect(controller.executionControl.canStop).toBe(false);
      expect(subscription.aborted).toBe(true);
      expect(controller.turns).toEqual(expect.arrayContaining([expect.objectContaining({ content: savedMessage.content })]));
      expect(listPendingStarts()).toEqual([]);
      expect(latest()).toMatchObject({ loading: false, canStop: false,
        taskState: state === 'waiting_confirmation' ? 'waiting_confirmation' : 'idle',
        hasPending: state === 'waiting_confirmation', canSend: state !== 'waiting_confirmation' });
    },
  );

  it.each(['completed', 'waiting_confirmation'] as const)(
    'converges a lookup that is already %s without inventing a running observation', async state => {
      if (state === 'waiting_confirmation') mocks.summaries.mockResolvedValue([summary(pending)]);
      const request = beginRequest();
      mocks.lookup.mockResolvedValueOnce({ ...running, state });
      await tickLookup();
      expect(request.controller.signal.aborted).toBe(true);
      expect(controller.activeRequestRef.current).toBeNull();
      expect(controller.loading).toBe(false);
      expect(controller.executionControl.canStop).toBe(false);
      expect(mocks.observe).not.toHaveBeenCalled();
      expect(latest()).toMatchObject({ conversationId: 7, loading: false, canStop: false,
        taskState: state === 'waiting_confirmation' ? 'waiting_confirmation' : 'idle' });
    },
  );

  it('stops the recovered exact turn and generation through the real Haru command', async () => {
    const stop = deferred<PilotInterruptResult>();
    mocks.interrupt.mockReturnValueOnce(stop.promise);
    await recoverRunning();
    const subscription = observeOptions().signal!;
    act(() => { command({ id: 1, version: latest().version, action: 'stop' }); });
    expect(mocks.reply).toHaveBeenCalledWith(1, { ok: true });
    expect(mocks.interrupt).toHaveBeenCalledTimes(1);
    const [target, commandId] = mocks.interrupt.mock.calls[0];
    expect(target).toEqual(running);
    expect(latest()).toMatchObject({ loading: true, taskState: 'running', stopping: true });
    await act(async () => {
      stop.resolve({ command_id: commandId, turn_id: target.turn_id,
        execution_generation: target.execution_generation, status: 'stopped' });
    });
    expect(subscription.aborted).toBe(true);
    expect(controller.executionControl.execution?.state).toBe('stopped');
    expect(latest()).toMatchObject({ loading: false, taskState: 'idle', canStop: false, stopping: false });
    expect(mocks.streamConfirm).not.toHaveBeenCalled();
  });

  it('preserves HITL after recovery and never turns a Haru stop into rejection', async () => {
    mocks.summaries.mockResolvedValue([summary(pending)]);
    beginRequest();
    mocks.lookup.mockResolvedValueOnce({ ...running, state: 'waiting_confirmation' });
    await tickLookup();
    expect(controller.pending).toEqual(pending);
    expect(controller.autoApprove).toBe(false);
    expect(latest()).toMatchObject({ loading: false, taskState: 'waiting_confirmation', hasPending: true, canSend: false, canStop: false });
    act(() => { command({ id: 1, version: latest().version, action: 'stop' }); });
    expect(mocks.reply).toHaveBeenCalledWith(1, { ok: false, reason: 'busy' });
    act(() => { command({ id: 2, version: latest().version, action: 'open-pending' }); });
    expect(mocks.openPending).toHaveBeenCalledOnce();
    expect(controller.pending).toEqual(pending);
    expect(mocks.interrupt).not.toHaveBeenCalled();
    expect(mocks.streamConfirm).not.toHaveBeenCalled();
  });

  it('ignores a late request lookup after a newer request owns the visible conversation', async () => {
    const lookup = deferred<PilotExecution>();
    mocks.lookup.mockReturnValueOnce(lookup.promise);
    const old = beginRequest();
    await tickLookup();
    act(() => { controller.finishActiveRequest(old); controller.setConversationId(9); });
    const next = beginRequest(nextRequestId);
    const nextExecution = { turn_id: 'new-turn', conversation_id: 9, execution_generation: 4,
      state: 'running' as const, protocol: 'pilot-runtime-v1' as const };
    act(() => { streamOptions().onAccepted?.({ conversationId: 9, turnId: nextExecution.turn_id,
      executionGeneration: nextExecution.execution_generation, protocol: nextExecution.protocol }); });
    await act(async () => { lookup.resolve(running); });
    expect(controller.conversationId).toBe(9);
    expect(controller.activeRequestRef.current).toBe(next);
    expect(next.controller.signal.aborted).toBe(false);
    expect(next.execution).toEqual(nextExecution);
    expect(controller.executionControl.execution).toEqual(nextExecution);
    expect(latest()).toMatchObject({ conversationId: 9, loading: true, taskState: 'running' });
    expect(mocks.observe).not.toHaveBeenCalled();
    expect(mocks.interrupt).not.toHaveBeenCalled();
  });

  it('does not make another conversation busy when an old durable read arrives late', async () => {
    const oldPoll = deferred<PilotExecution>();
    const nextPoll = deferred<PilotExecution | null>();
    mocks.readExecution.mockReturnValueOnce(oldPoll.promise).mockReturnValueOnce(nextPoll.promise);
    await recoverRunning();
    const subscription = observeOptions().signal!;
    act(() => { controller.visibleRequestGenerationRef.current += 1; controller.setConversationId(9); });
    await act(async () => { nextPoll.resolve(null); oldPoll.resolve(running); });
    expect(subscription.aborted).toBe(true);
    expect(controller.conversationId).toBe(9);
    expect(controller.executionControl.execution).toBeNull();
    expect(latest()).toMatchObject({ conversationId: 9, loading: false, taskState: 'idle', canStop: false });
    expect(mocks.observe).toHaveBeenCalledTimes(1);
    expect(mocks.interrupt).not.toHaveBeenCalled();
  });

  it('settles a direct terminal response even when later durable polling stays offline', async () => {
    const response = deferred<Awaited<ReturnType<typeof import('@/services/chat').streamChat>>>();
    mocks.streamChat.mockReturnValueOnce(response.promise);
    mocks.readExecution.mockRejectedValue(new Error('synthetic offline poll'));
    const request = beginRequest();
    await act(async () => {
      streamOptions().onAccepted?.({ conversationId: 7, turnId: running.turn_id,
        executionGeneration: running.execution_generation, protocol: running.protocol });
      response.resolve({ type: 'message', conversation_id: 7, turn_id: running.turn_id,
        execution_generation: running.execution_generation, message: 'completed directly' });
      await streamCompletion;
      controller.finishActiveRequest(request);
    });
    expect(controller.executionControl.execution?.state).toBe('completed');
    expect(latest()).toMatchObject({ conversationId: 7, loading: false, taskState: 'idle', canStop: false, canSend: true });
    await tickLookup();
    expect(controller.loading).toBe(false);
    expect(mocks.lookup).not.toHaveBeenCalled();
  });

  it('settles the recovered GET terminal without relying on a successful execution poll', async () => {
    const response = deferred<Awaited<ReturnType<typeof import('@/services/pilotRuntime').observeRuntimeTurn>>>();
    mocks.observe.mockReturnValueOnce(response.promise);
    mocks.readExecution.mockRejectedValue(new Error('synthetic offline poll'));
    await recoverRunning();
    await act(async () => {
      response.resolve({ type: 'message', conversation_id: 7, turn_id: running.turn_id,
        execution_generation: running.execution_generation, message: 'completed through GET' });
    });
    expect(controller.executionControl.execution?.state).toBe('completed');
    expect(latest()).toMatchObject({ conversationId: 7, loading: false, taskState: 'idle', canStop: false, canSend: true });
    await tickLookup();
    expect(controller.loading).toBe(false);
    expect(mocks.streamChat).toHaveBeenCalledTimes(1);
  });

  it('does not let the old subscriber terminal or finally clear a newer generation', async () => {
    const oldResponse = deferred<Awaited<ReturnType<typeof import('@/services/chat').streamChat>>>();
    mocks.streamChat.mockReturnValueOnce(oldResponse.promise);
    const old = await recoverRunning();
    const oldCompletion = streamCompletion;
    const next = beginRequest(nextRequestId);
    act(() => {
      streamOptions().onAccepted?.({ conversationId: 7, turnId: running.turn_id,
        executionGeneration: 4, protocol: running.protocol });
    });
    await act(async () => {
      oldResponse.resolve({ type: 'message', conversation_id: 7, turn_id: running.turn_id,
        execution_generation: 3, message: 'old generation completed' });
      await oldCompletion;
      expect(controller.finishActiveRequest(old)).toBe(false);
    });
    expect(controller.activeRequestRef.current).toBe(next);
    expect(next.controller.signal.aborted).toBe(false);
    expect(controller.executionControl.execution).toMatchObject({ conversation_id: 7, turn_id: running.turn_id,
      execution_generation: 4, state: 'running' });
    expect(latest()).toMatchObject({ conversationId: 7, loading: true, taskState: 'running', canStop: true });
    expect(mocks.interrupt).not.toHaveBeenCalled();
  });


  it.each(['completed', 'waiting_confirmation'] as const)(
    'settles an exact recovered RuntimeEndedError (%s) while polling stays offline', async state => {
      mocks.readExecution.mockRejectedValue(new Error('synthetic offline poll'));
      mocks.observe.mockRejectedValueOnce(new RuntimeEndedError(running, state));
      if (state === 'waiting_confirmation') mocks.summaries.mockResolvedValue([summary(pending)]);
      await recoverRunning();
      expect(controller.executionControl.execution?.state).toBe(state);
      expect(controller.loading).toBe(false);
      expect(controller.pending).toEqual(state === 'waiting_confirmation' ? pending : null);
      expect(latest()).toMatchObject({ conversationId: 7, loading: false, canStop: false,
        taskState: state === 'waiting_confirmation' ? 'waiting_confirmation' : 'idle' });
      expect(mocks.streamConfirm).not.toHaveBeenCalled();
    },
  );

  it('does not apply a terminal error from a different execution generation', async () => {
    mocks.observe.mockRejectedValueOnce(new RuntimeEndedError({ ...running, execution_generation: 4 }, 'completed'));
    await recoverRunning();
    expect(controller.executionControl.execution).toEqual(running);
    expect(latest()).toMatchObject({ conversationId: 7, loading: true, taskState: 'running', canStop: true });
    expect(mocks.messages).not.toHaveBeenCalled();
  });

  it('does not let an earlier running poll resurrect a trusted recovered terminal', async () => {
    const poll = deferred<PilotExecution>();
    const response = deferred<Awaited<ReturnType<typeof import('@/services/pilotRuntime').observeRuntimeTurn>>>();
    mocks.readExecution.mockReturnValueOnce(poll.promise);
    mocks.observe.mockReturnValueOnce(response.promise);
    await recoverRunning();
    await act(async () => {
      response.resolve({ type: 'message', conversation_id: 7, turn_id: running.turn_id,
        execution_generation: running.execution_generation, message: 'durably completed' });
    });
    await act(async () => { poll.resolve(running); });
    expect(controller.executionControl.execution?.state).toBe('completed');
    expect(latest()).toMatchObject({ conversationId: 7, loading: false, taskState: 'idle', canStop: false });
  });

  it('ignores an old accepted callback and finally after request ownership moves', async () => {
    const old = beginRequest();
    const oldOptions = streamOptions();
    act(() => { controller.finishActiveRequest(old); controller.setConversationId(9); });
    const next = beginRequest(nextRequestId);
    act(() => {
      streamOptions().onAccepted?.({ conversationId: 9, turnId: 'new-turn', executionGeneration: 4, protocol: running.protocol });
      oldOptions.onAccepted?.({ conversationId: 7, turnId: running.turn_id, executionGeneration: 3, protocol: running.protocol });
      expect(controller.finishActiveRequest(old)).toBe(false);
    });
    expect(controller.activeRequestRef.current).toBe(next);
    expect(controller.conversationId).toBe(9);
    expect(controller.executionControl.execution).toMatchObject({ conversation_id: 9, turn_id: 'new-turn', execution_generation: 4 });
    expect(latest()).toMatchObject({ conversationId: 9, loading: true, taskState: 'running' });
    expect(mocks.interrupt).not.toHaveBeenCalled();
  });


  it.each([
    ['network error', new Error('synthetic network error')],
    ['subscriber abort', new DOMException('subscriber closed', 'AbortError')],
    ['unproven terminal identity', new RuntimeEndedError(running, 'result_unknown')],
  ])('keeps a recovered execution running after %s', async (_label, error) => {
    mocks.observe.mockRejectedValueOnce(error);
    await recoverRunning();
    expect(controller.executionControl.execution).toEqual(running);
    expect(latest()).toMatchObject({ conversationId: 7, loading: true, taskState: 'running', canStop: true });
    expect(mocks.messages).not.toHaveBeenCalled();
    expect(mocks.interrupt).not.toHaveBeenCalled();
  });

  it('aborts only the recovered subscription on unmount without stopping the durable task', async () => {
    await recoverRunning();
    const subscription = observeOptions().signal!;
    act(() => { root.render(null); });
    expect(subscription.aborted).toBe(true);
    const reads = mocks.readExecution.mock.calls.length;
    await tickLookup();
    expect(mocks.readExecution).toHaveBeenCalledTimes(reads);
    expect(mocks.interrupt).not.toHaveBeenCalled();
    expect(mocks.streamConfirm).not.toHaveBeenCalled();
  });


  it.each([
    ['new turn', { ...running, turn_id: 'next-durable-turn' }],
    ['new generation', { ...running, execution_generation: 4 }],
  ])('rejects a later same-identity running poll after terminal, but observes a %s', async (_label, nextExecution) => {
    const response = deferred<Awaited<ReturnType<typeof import('@/services/pilotRuntime').observeRuntimeTurn>>>();
    mocks.readExecution.mockResolvedValue(running);
    mocks.observe.mockReturnValueOnce(response.promise);
    await recoverRunning();
    await act(async () => {
      response.resolve({ type: 'message', conversation_id: 7, turn_id: running.turn_id,
        execution_generation: running.execution_generation, message: 'durably completed' });
    });
    expect(controller.executionControl.execution?.state).toBe('completed');
    const observationsAtTerminal = mocks.observe.mock.calls.length;
    const pollsAtTerminal = mocks.readExecution.mock.calls.length;

    // Unlike an old in-flight read, this request starts after terminal settlement.
    await tickLookup();
    expect(mocks.readExecution).toHaveBeenCalledTimes(pollsAtTerminal + 1);
    expect(controller.executionControl.execution?.state).toBe('completed');
    expect(latest()).toMatchObject({ conversationId: 7, loading: false, taskState: 'idle', canStop: false });
    expect(mocks.observe).toHaveBeenCalledTimes(observationsAtTerminal);

    // Terminal knowledge belongs to one exact identity, not the conversation.
    mocks.readExecution.mockResolvedValue(nextExecution);
    await tickLookup();
    expect(controller.executionControl.execution).toEqual(nextExecution);
    expect(latest()).toMatchObject({ conversationId: 7, loading: true, taskState: 'running', canStop: true });
    expect(mocks.observe).toHaveBeenLastCalledWith(nextExecution, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(observeOptions().signal?.aborted).toBe(false);
    expect(mocks.interrupt).not.toHaveBeenCalled();
  });


  it('allows explicit HITL continuation in the next generation without losing the pending token', async () => {
    const response = deferred<Awaited<ReturnType<typeof import('@/services/pilotRuntime').observeRuntimeTurn>>>();
    mocks.observe.mockReturnValueOnce(response.promise);
    mocks.summaries.mockResolvedValue([summary(pending)]);
    await recoverRunning();
    await act(async () => { response.resolve({ type: 'confirmation_required', conversation_id: 7,
      turn_id: running.turn_id, execution_generation: 3, pending_action: pending }); });
    expect(controller.executionControl.execution?.state).toBe('waiting_confirmation');
    expect(controller.pending).toEqual(pending);
    expect(mocks.streamConfirm).not.toHaveBeenCalled();

    mocks.streamConfirm.mockImplementation(() => never());
    act(() => {
      const lease = controller.beginActiveRequest('confirmation', 7, pending.confirmation_token)!;
      expect(lease).not.toBeNull();
      void controller.streamConfirmationRequest(lease, 7, { approved: true,
        operation_id: pending.operation_id, confirmation_token: pending.confirmation_token }, { requestId: nextRequestId });
      const options = mocks.streamConfirm.mock.calls[0][2]!;
      options.onAccepted?.({ conversationId: 7, turnId: running.turn_id, executionGeneration: 4, protocol: running.protocol });
    });
    expect(controller.executionControl.execution).toMatchObject({ turn_id: running.turn_id, conversation_id: 7, execution_generation: 4, state: 'running' });
    expect(controller.loading).toBe(true);
    expect(controller.pending?.confirmation_token).toBe(pending.confirmation_token);
    expect(mocks.streamConfirm).toHaveBeenCalledTimes(1);
    expect(mocks.streamConfirm.mock.calls[0][1]).toMatchObject({ approved: true, confirmation_token: pending.confirmation_token });
    expect(mocks.interrupt).not.toHaveBeenCalled();
  });

});
