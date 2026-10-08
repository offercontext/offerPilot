// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChatPanel from '@/components/ChatPanel';
import { PilotAttachmentProvider } from '@/features/pilot/PilotAttachmentContext';
import type { PilotTimelinePage, PilotTurnItemV1 } from '@/features/actionPresentation/contracts';
import type { ChatMessage, ChatResponse, ChatStreamEvent, Conversation, PendingAction, PilotExecution } from '@/types/chat';
import type { DesktopHaruSnapshot } from './desktopHaru';
import type { ActiveConversationRequest, PilotConversationController } from './usePilotConversationController';
import { AssistantSurfaceProvider, usePilotConversationController } from './AssistantSurfaceProvider';

// Mounted ChatPanel callbacks, controller/execution/presentation hooks, timeline
// projection, Haru owner, runtime transport and SSE parser are all real. Only
// HTTP and the desktop bridge are fake; no copied or evaluated product source.
const io = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), fetch: vi.fn(),
  publish: vi.fn<(snapshot: DesktopHaruSnapshot) => void>() }));
vi.mock('@/services/http', () => ({ createApiClient: () => ({ get: io.get, post: io.post }) }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const identity = { turn_id: 'recovered-stream-turn', conversation_id: 7, execution_generation: 3 };
const running: PilotExecution = { ...identity, state: 'running', protocol: 'pilot-runtime-v1' };
const chunks = ['第一段：边界值。', '第二段：临界点。', '第三段：验证结果。'];
const finalText = chunks.join('');
const question = 'synthetic request';
const response: ChatResponse = { type: 'message', ...identity, message: finalText };
const never = <T,>() => new Promise<T>(() => undefined);
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
type Stream = { controller: ReadableStreamDefaultController<Uint8Array>; signal?: AbortSignal; cancelled: boolean };
let root: Root;
let container: HTMLDivElement;
let queryClient: QueryClient;
let owner: PilotConversationController;
let mode: 'normal' | 'recovery';
let runtimeIdentity: typeof identity;
let snapshotPages: ChatStreamEvent[][] | null;
let snapshotGate: ReturnType<typeof deferred<void>> | null;
let snapshotFlags: { history_truncated?: boolean; progress_gap?: boolean };
let completed: boolean;
let requestId: string;
let streams: Stream[];
let events: ChatStreamEvent[];
let savedText: string | undefined;
let pendingAction: PendingAction | null;
let completion: Promise<unknown>;
let lease: ActiveConversationRequest;
let executionPoll: ReturnType<typeof deferred<{ data: { execution: PilotExecution | null } }>>;

function Probe() { owner = usePilotConversationController(); return null; }
function summary(id = 7): Conversation {
  return { id, title: `Conversation ${id}`, context_type: 'workspace', context_ref: '',
    created_at: '', updated_at: '', pending_action: id === 7 ? pendingAction : null };
}
function messages(id = 7): ChatMessage[] {
  if (id !== 7) return [{ id: 20, conversation_id: id, role: 'assistant', content: '另一段对话', created_at: '' }];
  return [{ id: 1, conversation_id: id, role: 'user', content: question, created_at: '' },
    ...(savedText === undefined ? [] : [{ id: 2, conversation_id: id, role: 'assistant' as const, content: savedText, created_at: '' }])];
}
function timeline(id = 7): PilotTimelinePage {
  const items = messages(id).map((message, index) => {
    const payload: PilotTurnItemV1 = { schema_version: 1, item_id: `message:${message.id}`,
      kind: message.role === 'user' ? 'user_message' : 'assistant_message', message_id: message.id,
      operation_id: null, content: message.content, action: null };
    return { schema_version: 1, item_id: `turn:${identity.turn_id}:${payload.item_id}`, turn_id: identity.turn_id,
      conversation_id: id, item_type: payload.kind, source_refs: [], source_revision: String(savedText?.length ?? 0),
      revision: (savedText?.length ?? 0) + 1, display_revision: 1, ordinal: index + 1,
      change_seq: index + 1, payload_digest: message.content, deleted: false, payload };
  });
  return { schema_version: 1, conversation_id: id, mode: 'snapshot', high_watermark: items.length,
    items, next_cursor: null, cursor: `timeline-${id}-${items.length}` };
}
function latestHaru() { return io.publish.mock.calls[io.publish.mock.calls.length - 1][0]; }
function assistantContents() { return owner.displayTurns.filter(turn => turn.role === 'assistant').map(turn => turn.content); }
function assertIncrement(text: string) {
  expect(assistantContents()).toEqual([text]);
  expect(latestHaru().messages.filter(turn => turn.role === 'assistant').map(turn => turn.content)).toEqual([text]);
  const bubbles = container.querySelectorAll('[class*="bubbleAssistant"]');
  expect(bubbles).toHaveLength(1);
  expect(bubbles[0].textContent).toBe(text);
  expect(owner.loading).toBe(true);
  expect(latestHaru()).toMatchObject({ loading: true, canSend: false, canStop: true, taskState: 'running' });
}
function currentStream() { return streams[streams.length - 1]; }
async function emit(event: string, data: Record<string, unknown>, overrides: Partial<ChatStreamEvent> = {}, stream = currentStream()) {
  const nextSeq = Math.max(0, ...events.map(item => item.seq)) + 1;
  const frame: ChatStreamEvent = { ...runtimeIdentity, event, seq: nextSeq, data, ...overrides };
  if (frame.turn_id === runtimeIdentity.turn_id && frame.conversation_id === runtimeIdentity.conversation_id
    && frame.execution_generation === runtimeIdentity.execution_generation && event !== 'resync_required'
    && !events.some(item => item.seq === frame.seq)) events.push(frame);
  await act(async () => { stream.controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(frame)}\n\n`)); });
}
async function begin(which: typeof mode) {
  mode = which;
  await act(async () => {
    completion = owner.sendMessage(question);
    lease = owner.activeRequestRef.current!;
  });
  expect(lease).not.toBeNull();
  if (which === 'recovery') await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(owner.conversationId).toBe(7);
  expect(owner.loading).toBe(true);
  expect(streams.length).toBeGreaterThan(0);
  expect(lease.controller.signal.aborted).toBe(which === 'recovery');
}
async function finish() {
  savedText = finalText; completed = true;
  await emit('completed', { response });
  await act(async () => { await completion; });
  expect(owner.executionControl.execution?.state).toBe('completed');
  expect(owner.loading).toBe(false);
  expect(assistantContents()).toEqual([finalText]);
  expect(latestHaru().messages.filter(turn => turn.role === 'assistant').map(turn => turn.content)).toEqual([finalText]);
  expect(latestHaru()).toMatchObject({ loading: false, canStop: false });
  expect(io.fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1);
  expect(io.post).not.toHaveBeenCalled();
}

beforeEach(async () => {
  vi.resetAllMocks(); vi.useFakeTimers(); localStorage.clear();
  completed = false; requestId = ''; runtimeIdentity = { ...identity }; snapshotPages = null; snapshotGate = null; snapshotFlags = {}; streams = []; events = []; savedText = undefined; pendingAction = null;
  executionPoll = deferred();
  io.get.mockImplementation(async (url: string) => {
    if (url === '/settings') return { data: { has_api_key: true, chat_auto_approve_writes: false } };
    if (url === '/chat/conversations') return { data: [summary(), summary(8)] };
    const match = url.match(/^\/chat\/conversations\/(\d+)(.*)$/);
    if (match) {
      const id = Number(match[1]);
      if (!match[2]) return { data: messages(id) };
      if (match[2] === '/timeline') return { data: timeline(id) };
      if (match[2] === '/execution') return id === 7 ? executionPoll.promise : never();
      if (match[2] === '/readiness-context') return { data: { schema_version: 1, state: 'not_applicable', conversation_id: id,
        application_id: 0, target_event_id: null, resume_id: null, ordered_version_ids: [], selection_fingerprint: '', scope_revision: 0, revision: 0 } };
    }
    throw new Error(`Unexpected HTTP GET ${url}`);
  });
  io.fetch.mockImplementation(async (url: string, options: RequestInit = {}) => {
    if ((url.endsWith('/turns') || url.endsWith('/confirm')) && options.method === 'POST') {
      if (url.endsWith('/confirm')) pendingAction = null;
      requestId = JSON.parse(String(options.body)).request_id;
      if (mode === 'recovery') return new Promise((_resolve, reject) =>
        options.signal!.addEventListener('abort', () => reject(options.signal!.reason), { once: true }));
      return Response.json({ ...runtimeIdentity, protocol_version: 'pilot-runtime-v1', state: 'running' });
    }
    if (url.includes('/requests/')) return Response.json({ ...runtimeIdentity, protocol_version: 'pilot-runtime-v1', request_id: requestId, state: 'running' });
    if (url.includes('/snapshot')) {
      if (snapshotGate) await snapshotGate.promise;
      const pageIndex = Number(new URL(url, 'https://example.invalid').searchParams.get('cursor') ?? 0);
      const pageEvents = snapshotPages?.[pageIndex] ?? events;
      return Response.json({ ...runtimeIdentity, state: completed ? 'completed' : 'running', ...snapshotFlags,
        events: pageEvents, event_cursor: `event-${Math.max(0, ...events.map(item => item.seq))}`, high_watermark: Math.max(0, ...events.map(item => item.seq)),
        ...(snapshotPages && pageIndex + 1 < snapshotPages.length ? { next_cursor: String(pageIndex + 1) } : {}),
        // The production runtime API includes a durable timeline on every
        // snapshot page, including a user-only timeline before any reply saves.
        durable: timeline() });
    }
    if (url.includes('/events?after=')) {
      const stream: Stream = { controller: undefined!, signal: options.signal ?? undefined, cancelled: false };
      streams.push(stream);
      return new Response(new ReadableStream<Uint8Array>({ start(c) { stream.controller = c; }, cancel() { stream.cancelled = true; } }),
        { headers: { 'Content-Type': 'text/event-stream' } });
    }
    if (url.endsWith(`/turns/${identity.turn_id}`)) return Response.json({ ...runtimeIdentity, state: completed ? 'completed' : 'running',
      ...(completed ? { terminal: { response } } : {}) });
    throw new Error(`Unexpected runtime fetch ${url}`);
  });
  vi.stubGlobal('fetch', io.fetch);
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })));
  HTMLElement.prototype.scrollIntoView = vi.fn();
  window.offerpilotDesktop = { role: 'owner', publish: io.publish, reply: vi.fn(), onCommand: () => vi.fn(),
    disconnect: vi.fn(), windowAction: vi.fn().mockResolvedValue(true), getState: vi.fn(), onState: vi.fn() };
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  await act(async () => { root.render(<QueryClientProvider client={queryClient}><AntApp><PilotAttachmentProvider>
    <AssistantSurfaceProvider><Probe /><ChatPanel open variant="page" onClose={() => undefined} /></AssistantSurfaceProvider>
  </PilotAttachmentProvider></AntApp></QueryClientProvider>); });
});
afterEach(() => {
  act(() => root.unmount()); queryClient.clear(); container.remove(); delete window.offerpilotDesktop;
  localStorage.clear(); vi.useRealTimers(); vi.unstubAllGlobals();
});

describe('recovered runtime deltas through the real ChatPanel and Haru owner', () => {
  it.each(['normal', 'recovery'] as const)('%s displays every delta before durable completion, then one canonical reply', async which => {
    await begin(which);
    for (let index = 0; index < chunks.length; index += 1) {
      await emit('assistant_delta', { delta: chunks[index] });
      assertIncrement(chunks.slice(0, index + 1).join(''));
    }
    await finish();
  });


  it('deduplicates repeated sequence numbers and reconnect snapshot replay', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    assertIncrement(chunks[0]);
    await emit('assistant_delta', { delta: chunks[0] }, { seq: 1 });
    assertIncrement(chunks[0]);
    const first = currentStream();
    await act(async () => { first.controller.close(); });
    expect(currentStream()).not.toBe(first);
    // The replayed prefix is not authoritative display text. Start a fresh
    // temporary segment after the reconnect watermark, then use the complete
    // canonical response at completion.
    expect(assistantContents()).toEqual([]);
    await emit('assistant_delta', { delta: chunks[1] });
    assertIncrement(chunks[1]);
    await emit('assistant_delta', { delta: chunks[2] });
    assertIncrement(chunks.slice(1).join(''));
    await finish();
  });

  it('keeps one subscriber and its projection when polling returns the same running identity', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    const original = currentStream();
    const subscriberCount = streams.length;
    await act(async () => { executionPoll.resolve({ data: { execution: { ...running } } }); });
    expect(streams).toHaveLength(subscriberCount);
    expect(original.signal?.aborted).toBe(false);
    assertIncrement(chunks[0]);
    executionPoll = deferred();
    await act(async () => {
      executionPoll.resolve({ data: { execution: { ...running } } });
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(streams).toHaveLength(subscriberCount);
    assertIncrement(chunks[0]);
    await emit('assistant_delta', { delta: chunks[1] });
    assertIncrement(chunks.slice(0, 2).join(''));
    await finish();
  });

  it('reattaches after a transient GET outage exhausts a subscriber without restarting execution', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    assertIncrement(chunks[0]);
    const original = currentStream();
    const readRuntime = io.fetch.getMockImplementation()!;
    io.fetch.mockImplementation((url: string, options: RequestInit = {}) => options.method === 'GET'
      ? Promise.reject(new Error('synthetic temporary GET outage')) : readRuntime(url, options));
    await act(async () => { original.controller.error(new Error('synthetic stream disconnected')); });
    expect(assistantContents()).toEqual([]);
    expect(owner.loading).toBe(true);

    io.fetch.mockImplementation(readRuntime);
    await act(async () => {
      executionPoll.resolve({ data: { execution: { ...running } } });
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(currentStream()).not.toBe(original);
    expect(original.signal?.aborted).toBe(true);
    const replacement = currentStream();
    const subscriberCount = streams.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(currentStream()).toBe(replacement);
    expect(streams).toHaveLength(subscriberCount);
    expect(replacement.signal?.aborted).toBe(false);
    await emit('assistant_delta', { delta: chunks[1] });
    assertIncrement(chunks[1]);
    await finish();
  });

  it.each([401, 403, 404, 410])('does not automatically retry a detached GET subscriber after HTTP %s', async status => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    const original = currentStream();
    const readRuntime = io.fetch.getMockImplementation()!;
    io.fetch.mockImplementation((url: string, options: RequestInit = {}) => options.method === 'GET'
      ? Promise.resolve(Response.json({}, { status })) : readRuntime(url, options));
    await act(async () => { original.controller.error(new Error('synthetic stream disconnected')); });
    expect(assistantContents()).toEqual([]);
    expect(owner.loading).toBe(true);
    const readsAfterFailure = io.fetch.mock.calls.length;
    io.fetch.mockImplementation(readRuntime);
    await act(async () => {
      executionPoll.resolve({ data: { execution: { ...running } } });
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(currentStream()).toBe(original);
    expect(io.fetch).toHaveBeenCalledTimes(readsAfterFailure);
    expect(io.post).not.toHaveBeenCalled();
  });

  it.each(['unmount', 'select-conversation', 'stop'] as const)(
    'cancels a pending GET recovery retry after %s', async action => {
      await begin('recovery');
      await emit('assistant_delta', { delta: chunks[0] });
      const original = currentStream();
      const readRuntime = io.fetch.getMockImplementation()!;
      io.fetch.mockImplementation((url: string, options: RequestInit = {}) => options.method === 'GET'
        ? Promise.reject(new Error('synthetic temporary GET outage')) : readRuntime(url, options));
      await act(async () => { original.controller.error(new Error('synthetic stream disconnected')); });
      io.fetch.mockImplementation(readRuntime);
      if (action === 'unmount') await act(async () => { root.unmount(); });
      else if (action === 'select-conversation') await act(async () => { await owner.selectConversation(8); });
      else {
        savedText = chunks[0];
        io.post.mockImplementation(async (_url: string, body: { command_id: string }) => ({ data: {
          command_id: body.command_id, turn_id: identity.turn_id,
          execution_generation: identity.execution_generation, status: 'stopped',
        } }));
        await act(async () => {
          container.querySelector<HTMLButtonElement>('button[aria-label="停止当前回复"]')!.click();
        });
        expect(owner.executionControl.execution?.state).toBe('stopped');
        expect(assistantContents()).toEqual([chunks[0]]);
      }
      expect(original.signal?.aborted).toBe(true);
      const readsAfterDismissal = io.fetch.mock.calls.length;
      const subscriberCount = streams.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
      expect(streams).toHaveLength(subscriberCount);
      expect(io.fetch).toHaveBeenCalledTimes(readsAfterDismissal);
      expect(io.fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1);
      expect(io.post).toHaveBeenCalledTimes(action === 'stop' ? 1 : 0);
    },
  );

  it.each([
    { turn_id: 'other-turn' },
    { execution_generation: identity.execution_generation - 1 },
    { conversation_id: 8 },
  ])('ignores SSE frames with another runtime identity: %j', async wrongIdentity => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    await emit('assistant_delta', { delta: 'wrong-owner' }, { ...wrongIdentity, seq: 2 });
    assertIncrement(chunks[0]);
    await emit('assistant_delta', { delta: chunks[1] });
    assertIncrement(chunks.slice(0, 2).join(''));
    await finish();
  });

  it('replaces recovered prose with the canonical snapshot without a duplicate assistant bubble', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    assertIncrement(chunks[0]);
    savedText = chunks[0];
    const first = currentStream();
    await act(async () => { first.controller.close(); });
    expect(currentStream()).not.toBe(first);
    expect(owner.presentationSnapshot?.items.some(item => item.content === chunks[0])).toBe(true);
    assertIncrement(chunks[0]);
    // The saved assistant message seals this delta segment before a terminal
    // event. Its presentation must replace the temporary read-only projection.
    savedText = finalText;
    await emit('assistant_message', { message: finalText });
    assertIncrement(finalText);
    await finish();
  });

  it('does not let an aborted subscriber repaint a newly selected conversation', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    assertIncrement(chunks[0]);
    const old = currentStream();
    await act(async () => { await owner.selectConversation(8); });
    expect(old.signal?.aborted).toBe(true);
    expect(owner.conversationId).toBe(8);
    expect(assistantContents()).toEqual(['另一段对话']);
    // Deliberately deliver a buffered frame after abort. Real transport parsing
    // still runs, so the visible ownership guard must reject its projection.
    if (!old.cancelled) await emit('assistant_delta', { delta: 'late-old-frame' }, {}, old);
    expect(assistantContents()).toEqual(['另一段对话']);
    expect(latestHaru()).toMatchObject({ conversationId: 8, loading: false });
    expect(latestHaru().messages.some(item => item.content.includes('late-old-frame'))).toBe(false);
    expect(io.fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1);
    expect(io.post).not.toHaveBeenCalled();
  });


  it.each(['completed', 'stopped', 'interrupted', 'waiting_confirmation'] as const)(
    'drops the temporary projection when durable polling proves %s', async state => {
      await begin('recovery');
      await emit('assistant_delta', { delta: chunks[0] });
      assertIncrement(chunks[0]);
      const old = currentStream();
      savedText = finalText;
      if (state === 'waiting_confirmation') pendingAction = {
        operation_id: 'pending-operation', tool_name: 'update_application', human: '请确认更新',
        confirmation_token: 'synthetic-confirmation-token', args: { status: 'interview' },
      };
      await act(async () => { executionPoll.resolve({ data: { execution: { ...running, state } } }); });
      expect(old.signal?.aborted).toBe(true);
      expect(owner.executionControl.execution?.state).toBe(state);
      expect(owner.loading).toBe(false);
      expect(assistantContents()).toEqual([finalText]);
      expect(latestHaru()).toMatchObject({ loading: false, canStop: false,
        hasPending: state === 'waiting_confirmation', canSend: state !== 'waiting_confirmation' });
      if (!old.cancelled) await emit('assistant_delta', { delta: 'late-terminal-frame' }, {}, old);
      expect(assistantContents()).toEqual([finalText]);
      expect(latestHaru().messages.some(item => item.content.includes('late-terminal-frame'))).toBe(false);
      expect(owner.pending).toEqual(pendingAction);
      expect(owner.autoApprove).toBe(false);
      expect(io.post).not.toHaveBeenCalled();
    },
  );

  it('stops the exact recovered generation from the real Stop button and keeps only saved prose', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    assertIncrement(chunks[0]);
    savedText = chunks[0];
    const old = currentStream();
    io.post.mockImplementation(async (url: string, body: { command_id: string; expected_generation: number }) => {
      expect(url).toBe(`/chat/turns/${identity.turn_id}/interrupt`);
      expect(body.expected_generation).toBe(identity.execution_generation);
      return { data: { command_id: body.command_id, turn_id: identity.turn_id,
        execution_generation: identity.execution_generation, status: 'stopped' } };
    });
    const stop = container.querySelector<HTMLButtonElement>('button[aria-label="停止当前回复"]');
    expect(stop?.disabled).toBe(false);
    await act(async () => { stop!.click(); });
    expect(io.post).toHaveBeenCalledTimes(1);
    expect(owner.executionControl.execution?.state).toBe('stopped');
    expect(old.signal?.aborted).toBe(true);
    expect(owner.loading).toBe(false);
    expect(assistantContents()).toEqual([chunks[0]]);
    if (!old.cancelled) await emit('assistant_delta', { delta: 'late-stopped-frame' }, {}, old);
    expect(assistantContents()).toEqual([chunks[0]]);
    expect(latestHaru()).toMatchObject({ loading: false, canStop: false, hasPending: false });
    expect(io.fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1);
  });


  it('does not replay a first snapshot delta over its already canonical assistant message', async () => {
    savedText = chunks[0];
    events = [{ ...identity, event: 'assistant_delta', seq: 1, data: { delta: chunks[0] } }];
    // The canonical HTTP read may finish before the runtime snapshot request.
    // Gate only external I/O so React commits that legitimate ordering.
    snapshotGate = deferred(); mode = 'recovery';
    await act(async () => { completion = owner.sendMessage(question); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(owner.presentationSnapshot?.items.some(item => item.content === chunks[0])).toBe(true);
    await act(async () => { snapshotGate!.resolve(); });
    expect(assistantContents()).toEqual([chunks[0]]);
    expect(latestHaru().messages.filter(item => item.role === 'assistant').map(item => item.content)).toEqual([chunks[0]]);
    expect(container.querySelectorAll('[class*="bubbleAssistant"]')).toHaveLength(1);
    await finish();
  });

  it('finishes all snapshot pages before projecting old deltas followed by a saved message', async () => {
    savedText = finalText;
    events = [
      { ...identity, event: 'assistant_delta', seq: 1, data: { delta: chunks[0] } },
      { ...identity, event: 'assistant_delta', seq: 2, data: { delta: chunks.slice(1).join('') } },
      { ...identity, event: 'assistant_message', seq: 3, data: { message: finalText } },
    ];
    snapshotPages = [events.slice(0, 1), events.slice(1, 2), events.slice(2)];
    await begin('recovery');
    expect(io.fetch.mock.calls.some(([url]) => String(url).includes('/snapshot?cursor=2'))).toBe(true);
    expect(assistantContents()).toEqual([finalText]);
    for (const [snapshot] of io.publish.mock.calls) {
      const assistant = snapshot.messages.filter(item => item.role === 'assistant');
      expect(assistant.length).toBeLessThanOrEqual(1);
      expect(assistant.some(item => item.content === chunks[0])).toBe(false);
    }
    await finish();
  });

  it('preserves earlier same-turn prose when HITL resumes as a new generation, despite old subscriber cleanup', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    const old = currentStream();
    savedText = '确认前已保存的说明';
    pendingAction = { operation_id: 'pending-operation', tool_name: 'update_application', human: '请确认更新',
      confirmation_token: 'synthetic-confirmation-token', args: { status: 'interview' } };
    await act(async () => { executionPoll.resolve({ data: { execution: { ...running, state: 'waiting_confirmation' } } }); });
    expect(owner.pending).not.toBeNull();
    runtimeIdentity = { ...identity, execution_generation: identity.execution_generation + 1 };
    events = [];
    await act(async () => { completion = owner.approvePending(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(owner.executionControl.execution?.execution_generation).toBe(runtimeIdentity.execution_generation);
    expect(owner.pending).toBeNull();
    await emit('assistant_delta', { delta: '确认后的新回复' });
    expect(assistantContents()).toEqual([savedText, '确认后的新回复']);
    expect(latestHaru().messages.filter(item => item.role === 'assistant').map(item => item.content)).toEqual([savedText, '确认后的新回复']);
    expect(container.querySelectorAll('[class*="bubbleAssistant"]')).toHaveLength(2);
    expect(old.signal?.aborted).toBe(true);
    if (!old.cancelled) await act(async () => { old.controller.close(); });
    expect(assistantContents()).toEqual([savedText, '确认后的新回复']);
    expect(owner.loading).toBe(true);
    expect(io.fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(2);
    expect(io.post).not.toHaveBeenCalled();
  });


  it.each(['sequence-gap', 'resync_required', 'history_truncated', 'progress_gap'] as const)(
    'discards the old prefix when recovery reports %s rather than splicing an incomplete tail', async reason => {
      await begin('recovery');
      await emit('assistant_delta', { delta: chunks[0] });
      assertIncrement(chunks[0]);
      if (reason === 'sequence-gap') {
        await emit('assistant_delta', { delta: '缺失后的尾部' }, { seq: 3 });
      } else if (reason === 'resync_required') {
        await emit('resync_required', {});
      } else {
        snapshotFlags = { [reason]: true };
        events = [{ ...identity, event: 'assistant_delta', seq: 3, data: { delta: '保留的尾部' } }];
        const first = currentStream();
        await act(async () => { first.controller.close(); });
      }
      expect(assistantContents()).toEqual([]);
      expect(latestHaru().messages.filter(item => item.role === 'assistant')).toEqual([]);
      expect(owner.loading).toBe(true);
      await emit('assistant_delta', { delta: '快照边界后的新片段' });
      assertIncrement('快照边界后的新片段');
      savedText = finalText;
      await emit('assistant_message', { message: finalText });
      expect(assistantContents()).toEqual([finalText]);
      await finish();
    },
  );

  it('clears an over-limit temporary reply while retaining running ownership and canonical completion', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: 'x'.repeat(524_288) });
    expect(assistantContents()[0]).toHaveLength(524_288);
    await emit('assistant_delta', { delta: 'y'.repeat(524_289) });
    expect(assistantContents()).toEqual([]);
    expect(latestHaru().messages.filter(item => item.role === 'assistant')).toEqual([]);
    expect(owner.loading).toBe(true);
    expect(owner.executionControl.canStop).toBe(true);
    await finish();
  });


  it('lets a new canonical message supersede a live prefix before assistant_message arrives', async () => {
    await begin('recovery');
    await emit('assistant_delta', { delta: chunks[0] });
    assertIncrement(chunks[0]);
    const original = currentStream();
    savedText = finalText;
    await emit('status', { phase: 'saving', label: '正在保存回复' });
    expect(currentStream()).toBe(original);
    expect(owner.presentationSnapshot?.items.some(item => item.content === finalText)).toBe(true);
    expect(assistantContents()).toEqual([finalText]);
    expect(latestHaru().messages.filter(item => item.role === 'assistant').map(item => item.content)).toEqual([finalText]);
    expect(container.querySelectorAll('[class*="bubbleAssistant"]')).toHaveLength(1);
    await emit('assistant_message', { message: finalText });
    expect(assistantContents()).toEqual([finalText]);
    await finish();
  });

  it.each(['ready', 'failed', 'ahead-of-snapshot'] as const)('does not duplicate canonical prose when its refresh wins the first delta race (initial read: %s)', async initialRead => {
    const readHttp = io.get.getMockImplementation()!;
    if (initialRead === 'failed') io.get.mockImplementation((url: string, ...args: unknown[]) => url === '/chat/conversations/7/timeline'
      ? Promise.reject(new Error('synthetic initial timeline unavailable')) : readHttp(url, ...args));
    if (initialRead === 'ahead-of-snapshot') {
      const readRuntime = io.fetch.getMockImplementation()!;
      io.fetch.mockImplementation(async (url: string, options: RequestInit = {}) => {
        const result = await readRuntime(url, options);
        // The runtime snapshot freezes a user-only P2 boundary. Its subsequent
        // timeline refresh may already contain the newly saved assistant.
        if (url.includes('/snapshot')) savedText = finalText;
        return result;
      });
    }
    await begin('recovery');
    io.get.mockImplementation(readHttp);
    savedText = finalText;
    await emit('status', { phase: 'running', label: '正在生成回复' });
    expect(assistantContents()).toEqual([finalText]);
    // The backend has already saved the reply while its earlier SSE frames
    // are still draining. A later rendered snapshot is not the segment's
    // starting boundary and must not authorize another temporary reply.
    await emit('assistant_delta', { delta: chunks[0] });
    expect(assistantContents()).toEqual([finalText]);
    expect(latestHaru().messages.filter(item => item.role === 'assistant').map(item => item.content)).toEqual([finalText]);
    expect(container.querySelectorAll('[class*="bubbleAssistant"]')).toHaveLength(1);
    await finish();
  });


  it('does not add recovered prose beside an uncertain prefix left by the original accepted subscriber', async () => {
    await begin('normal');
    await emit('assistant_delta', { delta: chunks[0] });
    assertIncrement(chunks[0]);
    expect(owner.turns.some(turn => turn.role === 'assistant' && turn.id?.startsWith('transient:'))).toBe(true);
    const original = currentStream();
    const originalSignal = lease.controller.signal;
    const readHttp = io.get.getMockImplementation()!;
    const readRuntime = io.fetch.getMockImplementation()!;
    io.get.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/chat/conversations/7' || url === '/chat/conversations/7/timeline') {
        return Promise.reject(new Error('synthetic canonical read unavailable'));
      }
      return readHttp(url, ...args);
    });
    io.fetch.mockImplementation((url: string, options: RequestInit = {}) => {
      // Exhaust only the original accepted request's GET retry budget. Its
      // replacement recovery subscriber gets a new signal and can reconnect.
      if (options.signal === originalSignal && options.method === 'GET') {
        return Promise.reject(new Error('synthetic original subscriber disconnected'));
      }
      return readRuntime(url, options);
    });
    await act(async () => { original.controller.error(new Error('synthetic stream disconnected')); });
    await act(async () => { await completion; });
    expect(owner.activeRequestRef.current).toBeNull();
    expect(currentStream()).not.toBe(original);
    expect(currentStream().signal).not.toBe(originalSignal);
    expect(owner.presentationFailed).toBe(true);
    expect(owner.presentationSnapshot).toBeNull();
    await emit('assistant_delta', { delta: chunks[1] });
    // With neither conversation nor timeline reads trusted, preserve the old
    // uncertain display. The independent GET progress must not double it.
    assertIncrement(chunks[0]);
    expect(owner.turns.filter(turn => turn.role === 'assistant').map(turn => turn.content)).toEqual([chunks[0]]);
    expect(io.fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1);

    io.get.mockImplementation(readHttp);
    await emit('status', { phase: 'running', label: '已恢复读取记录' });
    expect(owner.presentationFailed).toBe(false);
    expect(owner.presentationSnapshot?.conversation_id).toBe(identity.conversation_id);
    await emit('assistant_delta', { delta: chunks[2] });
    assertIncrement(chunks.slice(1).join(''));
    // The original in-memory prefix is still present, proving the canonical
    // projection took precedence without deleting history to hide duplication.
    expect(owner.turns.filter(turn => turn.role === 'assistant').map(turn => turn.content)).toEqual([chunks[0]]);
    await finish();
  });


  it('keeps live progress when a resumed HITL generation snapshot and its first delta are batched', async () => {
    // Reopen a turn whose confirmation has already resumed on the server. The
    // previous generation's saved prose is present, but the ordinary timeline
    // request has not yet returned a canonical snapshot to the React owner.
    savedText = '上代确认前已保存的说明';
    runtimeIdentity = { ...identity, execution_generation: identity.execution_generation + 1 };
    const readHttp = io.get.getMockImplementation()!;
    let timelineReads = 0;
    io.get.mockImplementation((url: string, ...args: unknown[]) => {
      if (url === '/chat/conversations/7/timeline' && ++timelineReads === 1) return never();
      return readHttp(url, ...args);
    });
    await act(async () => { await owner.selectConversation(7); });
    expect(owner.presentationSnapshot).toBeNull();
    snapshotGate = deferred();
    const readRuntime = io.fetch.getMockImplementation()!;
    const firstDelta = { ...runtimeIdentity, event: 'assistant_delta', seq: 1, data: { delta: '新一代第一段。' } };
    let snapshotAtFirstFrame: typeof owner.presentationSnapshot | undefined;
    io.fetch.mockImplementation(async (url: string, options: RequestInit = {}) => {
      const result = await readRuntime(url, options);
      if (url.includes('/events?after=') && events.length === 0) {
        // The first SSE frame is already buffered when the snapshot finishes.
        // Only external I/O is controlled; there is no intermediate act,
        // forced React commit, copied callback, or product timing override.
        snapshotAtFirstFrame = owner.presentationSnapshot;
        events.push(firstDelta);
        currentStream().controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(firstDelta)}\n\n`));
      }
      return result;
    });
    await act(async () => { executionPoll.resolve({ data: { execution: { ...running, ...runtimeIdentity } } }); });
    expect(owner.presentationSnapshot).toBeNull();
    await act(async () => { snapshotGate!.resolve(); });
    expect(snapshotAtFirstFrame).toBeNull();
    expect(owner.presentationSnapshot?.items.some(item => item.content === savedText)).toBe(true);
    expect(assistantContents()).toEqual([savedText, '新一代第一段。']);
    await emit('assistant_delta', { delta: '新一代第二段。' });
    expect(assistantContents()).toEqual([savedText, '新一代第一段。新一代第二段。']);
    await emit('assistant_delta', { delta: '新一代第三段。' });
    const expected = [savedText, '新一代第一段。新一代第二段。新一代第三段。'];
    expect(assistantContents()).toEqual(expected);
    expect(latestHaru().messages.filter(item => item.role === 'assistant').map(item => item.content)).toEqual(expected);
    expect(container.querySelectorAll('[class*="bubbleAssistant"]')).toHaveLength(2);
    expect(owner.executionControl.execution?.execution_generation).toBe(runtimeIdentity.execution_generation);
    expect(owner.loading).toBe(true);
    expect(io.fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(0);
    expect(io.post).not.toHaveBeenCalled();
  });
});
