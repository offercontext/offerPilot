// @vitest-environment jsdom
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getConversation, getPilotExecution, listConversations } from '@/services/chat';
import { getRuntimeRequestExecution, observeRuntimeTurn } from '@/services/pilotRuntime';
import {
  AssistantSurfaceProvider,
  useAssistantSurface,
  usePilotConversationController,
} from './AssistantSurfaceProvider';

vi.mock('@/services/chat', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/chat')>(),
  getPilotExecution: vi.fn().mockResolvedValue(null),
  getConversation: vi.fn().mockResolvedValue([]),
  listConversations: vi.fn().mockResolvedValue([]),
}));

vi.mock('@/features/actionPresentation/service', () => ({
  getPilotPresentation: vi.fn().mockRejectedValue(new Error('timeline unavailable in controller fixture')),
}));

vi.mock('@/services/pilotRuntime', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/pilotRuntime')>(),
  getRuntimeRequestExecution: vi.fn().mockResolvedValue(null),
  observeRuntimeTurn: vi.fn().mockImplementation(() => new Promise(() => undefined)),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement | undefined;

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  vi.useRealTimers();
  vi.mocked(getPilotExecution).mockReset().mockResolvedValue(null);
  vi.mocked(observeRuntimeTurn).mockClear();
  vi.mocked(getRuntimeRequestExecution).mockReset().mockResolvedValue(null);
});

describe('AssistantSurfaceProvider', () => {
  it.each([true, false])('releases a hanging POST only with exact admission proof (proof=%s)', async (proof) => {
    vi.useFakeTimers();
    let controller!: ReturnType<typeof usePilotConversationController>;
    let surface!: ReturnType<typeof useAssistantSurface>;
    function Consumer() { controller = usePilotConversationController(); surface = useAssistantSurface(); return null; }
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    await act(async () => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));
    await act(async () => { surface.openHaru(); controller.setConversationId(7); });
    const target = { turn_id: 'hanging-post', conversation_id: 7, execution_generation: 2,
      state: 'running' as const, protocol: 'pilot-runtime-v1' as const };
    vi.mocked(getPilotExecution).mockResolvedValue(target);
    vi.mocked(getRuntimeRequestExecution).mockResolvedValue(proof ? target : null);
    let request!: NonNullable<ReturnType<typeof controller.beginActiveRequest>>;
    act(() => {
      request = controller.beginActiveRequest('chat', 7)!;
      request.protocol = 'pilot-runtime-v1'; request.requestId = 'original-request';
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(getRuntimeRequestExecution).toHaveBeenCalledWith('original-request', expect.any(AbortSignal));
    expect(request.controller.signal.aborted).toBe(proof);
    expect(controller.activeRequestRef.current).toBe(proof ? null : request);
    if (proof) expect(observeRuntimeTurn).toHaveBeenCalledWith(target, expect.anything());
    else expect(observeRuntimeTurn).not.toHaveBeenCalled();
  });
  it.each(['completed', 'stopped', 'interrupted'] as const)('recovers a %s task even when its original event stream never finishes', async (terminalState) => {
    vi.useFakeTimers();
    let controller!: ReturnType<typeof usePilotConversationController>;
    let surface!: ReturnType<typeof useAssistantSurface>;
    function Consumer() { controller = usePilotConversationController(); surface = useAssistantSurface(); return null; }
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    await act(async () => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));
    await act(async () => { surface.openHaru(); controller.setConversationId(7); });
    const target = { turn_id: 'stalled-stream', conversation_id: 7, execution_generation: 1,
      state: 'running' as const, protocol: 'pilot-runtime-v1' as const };
    let request!: NonNullable<ReturnType<typeof controller.beginActiveRequest>>;
    act(() => {
      request = controller.beginActiveRequest('chat', 7)!; request.execution = target;
      controller.executionControl.acceptExecution(target);
    });
    vi.mocked(getPilotExecution).mockResolvedValue({ ...target, state: terminalState });
    vi.mocked(getConversation).mockResolvedValue([{ id: 20, conversation_id: 7, role: 'assistant', content: '已保存的完整结果', created_at: '2026-09-09T00:00:00Z' }]);
    vi.mocked(listConversations).mockResolvedValue([]);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(request.controller.signal.aborted).toBe(true);
    expect(controller.activeRequestRef.current).toBeNull();
    expect(controller.turns.some((turn) => turn.content === '已保存的完整结果')).toBe(true);
  });
  it('detaches a v1 admission when closed before the server has returned its identity', async () => {
    let controller!: ReturnType<typeof usePilotConversationController>;
    let surface!: ReturnType<typeof useAssistantSurface>;
    function Consumer() { controller = usePilotConversationController(); surface = useAssistantSurface(); return null; }
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    await act(async () => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));
    act(() => surface.openHaru());
    let request!: NonNullable<ReturnType<typeof controller.beginActiveRequest>>;
    act(() => { request = controller.beginActiveRequest('chat')!; request.protocol = 'pilot-runtime-v1'; });
    act(() => surface.closeSurface());
    expect(request.controller.signal.aborted).toBe(true);
    expect(controller.activeRequestRef.current).toBeNull();
    expect(observeRuntimeTurn).not.toHaveBeenCalled();
  });
  it('closes only the v1 subscription and reattaches the same task when opened', async () => {
    let controller!: ReturnType<typeof usePilotConversationController>;
    let surface!: ReturnType<typeof useAssistantSurface>;
    function Consumer() { controller = usePilotConversationController(); surface = useAssistantSurface(); return null; }
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    await act(async () => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));
    await act(async () => { surface.openHaru(); controller.setConversationId(7); });
    let request!: NonNullable<ReturnType<typeof controller.beginActiveRequest>>;
    act(() => { request = controller.beginActiveRequest('chat', 7)!; });
    const target = { turn_id: 'runtime-turn', conversation_id: 7, execution_generation: 1, state: 'running' as const, protocol: 'pilot-runtime-v1' as const };
    request.execution = target;
    act(() => controller.executionControl.acceptExecution(target));
    act(() => surface.closeSurface());
    expect(request.controller.signal.aborted).toBe(true);
    expect(controller.executionControl.execution).toEqual(target);
    expect(observeRuntimeTurn).not.toHaveBeenCalled();
    act(() => surface.openHaru());
    expect(observeRuntimeTurn).toHaveBeenCalledWith(target, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    const signal = vi.mocked(observeRuntimeTurn).mock.calls[0][1]!.signal!;
    act(() => surface.closeSurface());
    expect(signal.aborted).toBe(true);
    expect(controller.executionControl.execution?.state).toBe('running');
  });

  it.each([false, true])('loads saved results after detached completion without changing a newer conversation (changed=%s)', async (changed) => {
    vi.useFakeTimers();
    vi.mocked(getConversation).mockReset();
    let controller!: ReturnType<typeof usePilotConversationController>;
    function Consumer() { controller = usePilotConversationController(); return null; }
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    await act(async () => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));
    const execution = { turn_id: 'detached-turn', conversation_id: 7, execution_generation: 1, state: 'running' as const };
    vi.mocked(getPilotExecution).mockResolvedValueOnce(execution).mockResolvedValue({ ...execution, state: 'completed' });
    await act(async () => controller.setConversationId(7));
    let resolve!: (messages: Awaited<ReturnType<typeof getConversation>>) => void;
    vi.mocked(getConversation).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    vi.mocked(listConversations).mockResolvedValueOnce([]);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(getConversation).toHaveBeenCalledWith(7);
    if (changed) act(() => { controller.setConversationId(8); controller.visibleRequestGenerationRef.current += 1; });
    await act(async () => resolve([{ id: 9, role: 'assistant', content: '脱离页面后保存的结果' }] as Awaited<ReturnType<typeof getConversation>>));
    expect(controller.turns.some((turn) => turn.content === '脱离页面后保存的结果')).toBe(!changed);
    expect(controller.activeRequestRef.current).toBeNull();
  });

  it.each([false, true])('recovers a returned conversation after background completion (changed=%s)', async (changed) => {
    vi.mocked(getConversation).mockReset();
    let controller!: ReturnType<typeof usePilotConversationController>;
    function Consumer() { controller = usePilotConversationController(); return null; }
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));
    act(() => controller.setConversationId(7));
    let resolve!: (messages: Awaited<ReturnType<typeof getConversation>>) => void;
    vi.mocked(getConversation).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    vi.mocked(listConversations).mockResolvedValueOnce([]);
    let request!: NonNullable<ReturnType<typeof controller.beginActiveRequest>>;
    act(() => { request = controller.beginActiveRequest('chat', 7)!; });
    Object.assign(request, { visibleGeneration: 1 });
    controller.visibleRequestGenerationRef.current = 3;
    await act(async () => { controller.finishActiveRequest(request); });
    expect(getConversation).toHaveBeenCalledWith(7);
    if (changed) act(() => { controller.setConversationId(8); controller.visibleRequestGenerationRef.current += 1; });
    await act(async () => resolve([{ id: 9, role: 'assistant', content: '后台完成结果' }] as Awaited<ReturnType<typeof getConversation>>));
    expect(controller.turns.some((turn) => turn.content === '后台完成结果')).toBe(!changed);
  });

  it('gives Haru and Pilot the same conversation controller instance', () => {
    const seen: unknown[] = [];

    function Consumer() {
      const controller = usePilotConversationController();
      useEffect(() => { seen.push(controller); }, [controller]);
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(
      <AssistantSurfaceProvider>
        <Consumer />
        <Consumer />
      </AssistantSurfaceProvider>,
    ));

    expect(seen).toHaveLength(2);
    expect(seen[0]).toBe(seen[1]);
  });

  it('changes presentation without replacing the controller', () => {
    const controllers: unknown[] = [];

    function Consumer() {
      const controller = usePilotConversationController();
      const surface = useAssistantSurface();
      controllers.push(controller);
      return <button type="button" onClick={surface.openPilot}>{surface.surface}</button>;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));
    act(() => host?.querySelector('button')?.click());

    expect(host.querySelector('button')?.textContent).toBe('pilot_workspace');
    expect(controllers[controllers.length - 1]).toBe(controllers[0]);
  });

  it('leases at most one active request across presentation changes', () => {
    let controller: ReturnType<typeof usePilotConversationController> | undefined;

    function Consumer() {
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    let first: ReturnType<NonNullable<typeof controller>['beginActiveRequest']> | undefined;
    let duplicate: ReturnType<NonNullable<typeof controller>['beginActiveRequest']> | undefined;
    act(() => {
      first = controller?.beginActiveRequest('chat');
      duplicate = controller?.beginActiveRequest('chat');
    });
    expect(first).not.toBeNull();
    expect(duplicate).toBeNull();
    act(() => controller?.stopActiveRequest({ silent: true }));
    expect(controller?.activeRequestRef.current).toBeNull();
  });

  it('pins a conversation context and restores it when that conversation becomes active again', () => {
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    const pinned = { view: 'applications-list' as const, label: '投递列表' };
    act(() => controller?.pinConversationContext(7, pinned));
    expect(controller?.pinnedContext).toEqual(pinned);
    act(() => controller?.activateConversationContext(undefined));
    expect(controller?.pinnedContext).toBeUndefined();
    act(() => controller?.activateConversationContext(7));
    expect(controller?.pinnedContext).toEqual(pinned);
  });

  it('hydrates a persisted conversation identity before comparing it with the current page', () => {
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    act(() => {
      controller?.setConversations([{
        id: 7,
        title: '腾讯投递',
        context_type: 'application',
        context_ref: '42',
        context_label: '腾讯 · 后端开发工程师',
        created_at: '2026-08-23T00:00:00Z',
        updated_at: '2026-08-23T00:00:00Z',
      }]);
    });
    act(() => {
      controller?.activateConversationContext(7);
      controller?.setFollowingContext({
        view: 'applications-list',
        label: '美团投递',
        entity: { kind: 'application', id: '43', label: '美团 · 后端开发工程师' },
      });
    });

    expect(controller?.pinnedContext).toMatchObject({
      view: 'applications-list',
      entity: { kind: 'application', id: '42' },
    });
    expect(controller?.contextChangeNotice).toMatchObject({
      currentConversationLabel: '腾讯 · 后端开发工程师',
      currentPageLabel: '美团 · 后端开发工程师',
    });
  });

  it('releases a ChatPanel action binding without leaving a stale closure behind', async () => {
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    const owner = {};
    const sendMessage = vi.fn(async () => 'sent' as const);
    act(() => controller?.bindActions(owner, {
      sendMessage,
      selectConversation: async () => undefined,
      startNewChat: () => true,
      retryLastMessage: () => undefined,
      clearLastFailure: () => undefined,
      handleConfirm: async () => undefined,
      retryConfirmAction: () => undefined,
      refreshConfirmationStatus: async () => undefined,
      clearActiveContext: async () => undefined,
    }));
    await expect(controller?.sendMessage('hello')).resolves.toBe('sent');
    act(() => {
      controller?.setConversationId(7);
      controller?.executionControl.acceptExecution({ turn_id: 'remote-running', conversation_id: 7, execution_generation: 1, state: 'running' });
    });
    await expect(controller?.sendMessage('second start')).resolves.toBe('ignored');
    act(() => controller?.releaseActions(owner));
    await expect(controller?.sendMessage('hello')).resolves.toBe('ignored');
    expect(sendMessage).toHaveBeenCalledTimes(1);
  });

  it('owns one background completion notice per request and opens its original conversation', () => {
    let surface: ReturnType<typeof useAssistantSurface> | undefined;
    function Consumer() {
      surface = useAssistantSurface();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    act(() => surface?.reportTaskState('running', 42));
    act(() => surface?.reportReplyLifecycle({
      status: 'success',
      conversationId: 42,
      background: true,
    }));
    expect(surface?.completionNotice).toEqual({ status: 'completed', conversationId: 42 });

    act(() => surface?.dismissNotice());
    act(() => surface?.reportReplyLifecycle({
      status: 'success',
      conversationId: 42,
      background: true,
    }));
    expect(surface?.completionNotice).toBeNull();

    act(() => surface?.reportTaskState('running', 42));
    act(() => surface?.reportReplyLifecycle({
      status: 'success',
      conversationId: 42,
      background: true,
    }));
    act(() => surface?.openCompletionNotice());
    expect(surface?.surface).toBe('haru_chat');
    expect(surface?.conversationRequest?.conversationId).toBe(42);
    expect(surface?.completionNotice).toBeNull();
  });

  it('clears non-lifecycle work while preserving a terminal reply lifecycle', () => {
    let surface: ReturnType<typeof useAssistantSurface> | undefined;
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      surface = useAssistantSurface();
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    let confirmationRequest: ReturnType<NonNullable<typeof controller>['beginActiveRequest']> = null;
    act(() => { confirmationRequest = controller!.beginActiveRequest('confirmation', 42); });
    expect(surface?.taskState).toBe('running');
    act(() => { controller!.finishActiveRequest(confirmationRequest!); });
    expect(surface?.taskState).toBe('idle');

    let chatRequest: ReturnType<NonNullable<typeof controller>['beginActiveRequest']> = null;
    act(() => { chatRequest = controller!.beginActiveRequest('chat', 42); });
    act(() => surface?.reportReplyLifecycle({
      status: 'success',
      conversationId: 42,
      background: true,
    }));
    act(() => { controller!.finishActiveRequest(chatRequest!); });
    expect(surface?.taskState).toBe('idle');
    expect(surface?.completionNotice).toEqual({ status: 'completed', conversationId: 42 });
  });

  it('notifies once when confirmation work finishes in the background', async () => {
    let surface: ReturnType<typeof useAssistantSurface> | undefined;
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      surface = useAssistantSurface();
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    let request: ReturnType<NonNullable<typeof controller>['beginActiveRequest']> = null;
    await act(async () => {
      controller?.setConversationId(42);
      request = controller?.beginActiveRequest('confirmation', 42) ?? null;
      controller?.setConfirmPhase('success');
      if (request) controller?.finishActiveRequest(request);
      await Promise.resolve();
    });

    expect(surface?.taskState).toBe('idle');
    expect(surface?.completionNotice).toEqual({ status: 'completed', conversationId: 42 });

    await act(async () => {
      surface?.dismissNotice();
      controller?.setConfirmPhase('success');
      await Promise.resolve();
    });
    expect(surface?.completionNotice).toBeNull();

    await act(async () => {
      surface?.openPilot();
      controller?.setConfirmPhase('saving');
      request = controller?.beginActiveRequest('confirmation', 42) ?? null;
      await Promise.resolve();
    });
    await act(async () => {
      controller?.setConfirmPhase('success');
      if (request) controller?.finishActiveRequest(request);
      await Promise.resolve();
    });
    expect(surface?.taskState).toBe('idle');
    expect(surface?.completionNotice).toBeNull();
  });

  it('reports a background confirmation or undo failure through the shared lifecycle', async () => {
    let surface: ReturnType<typeof useAssistantSurface> | undefined;
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      surface = useAssistantSurface();
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    let request: ReturnType<NonNullable<typeof controller>['beginActiveRequest']> = null;
    await act(async () => {
      controller?.setConversationId(73);
      request = controller?.beginActiveRequest('confirmation', 73) ?? null;
      controller?.setConfirmPhase('error');
      if (request) controller?.finishActiveRequest(request);
      await Promise.resolve();
    });

    expect(surface?.taskState).toBe('idle');
    expect(surface?.completionNotice).toEqual({ status: 'failed', conversationId: 73 });
  });

  it('reports confirmation and undo terminal states before returning to idle', () => {
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    const completedSequence: string[] = [];
    act(() => {
      controller?.bindTaskStateReporter((state) => completedSequence.push(state));
      const request = controller?.beginActiveRequest('confirmation', 42);
      controller?.setConfirmPhase('success');
      if (request) controller?.finishActiveRequest(request);
    });
    expect(completedSequence).toEqual(['running', 'completed', 'idle']);

    const failedSequence: string[] = [];
    act(() => {
      controller?.bindTaskStateReporter((state) => failedSequence.push(state));
      const request = controller?.beginActiveRequest('undo', 73);
      controller?.setConfirmPhase('error');
      if (request) controller?.finishActiveRequest(request);
    });
    expect(failedSequence).toEqual(['running', 'failed', 'idle']);
  });

  it('compares closed context identity instead of labels and switches without clearing content', () => {
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    const pinned = {
      view: 'applications-list' as const,
      label: '腾讯旧标签',
      entity: { kind: 'application' as const, id: '7', label: '相同显示文案' },
    };
    act(() => {
      controller?.setConversationId(99);
      controller?.setTurns([{ role: 'assistant', content: '保留消息' }]);
      controller?.setAttachments([{ kind: 'resume', id: '3', label: '保留附件' }]);
      controller?.pinConversationContext(99, pinned);
      controller?.setFollowingContext({ ...pinned, label: '腾讯新标签' });
    });
    expect(controller?.contextChangeNotice).toBeNull();

    act(() => controller?.setFollowingContext({
      ...pinned,
      entity: { ...pinned.entity, id: '8' },
    }));
    expect(controller?.contextChangeNotice).toMatchObject({
      currentConversationLabel: '相同显示文案',
      currentPageLabel: '相同显示文案',
    });

    let switched = false;
    act(() => { switched = controller?.switchToFollowingContext() ?? false; });
    expect(switched).toBe(true);
    expect(controller?.pinnedContext?.entity?.id).toBe('8');
    expect(controller?.turns).toEqual([{ role: 'assistant', content: '保留消息' }]);
    expect(controller?.attachments).toEqual([{ kind: 'resume', id: '3', label: '保留附件' }]);
    expect(controller?.contextChangeNotice).toBeNull();
  });

  it('does not replace pinned or frozen request context while running or waiting for confirmation', () => {
    let controller: ReturnType<typeof usePilotConversationController> | undefined;
    function Consumer() {
      controller = usePilotConversationController();
      return null;
    }

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => root?.render(<AssistantSurfaceProvider><Consumer /></AssistantSurfaceProvider>));

    const original = {
      view: 'applications-list' as const,
      label: '腾讯',
      entity: { kind: 'application' as const, id: '7', label: '腾讯' },
    };
    const following = {
      view: 'applications-list' as const,
      label: '美团',
      entity: { kind: 'application' as const, id: '8', label: '美团' },
    };
    act(() => {
      controller?.setConversationId(99);
      controller?.pinConversationContext(99, original);
      controller?.setFollowingContext(following);
      controller?.setRequestContextSnapshot(original);
      controller?.setLoading(true);
    });
    expect(controller?.switchToFollowingContext()).toBe(false);
    expect(controller?.pinnedContext).toEqual(original);
    expect(controller?.requestContextSnapshot).toEqual(original);

    act(() => {
      controller?.setLoading(false);
      controller?.setPending({
        tool_name: 'update_application',
        human: '更新投递',
        confirmation_token: 'token',
        args: {},
      });
    });
    expect(controller?.switchToFollowingContext()).toBe(false);
    expect(controller?.pinnedContext).toEqual(original);
    expect(controller?.requestContextSnapshot).toEqual(original);
  });
});
