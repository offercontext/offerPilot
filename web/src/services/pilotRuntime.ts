import type { ChatResponse, ChatStreamEvent, PilotExecution } from '@/types/chat';
import { authHeaders } from './authToken';
import type { PilotTimelinePage } from '@/features/actionPresentation/contracts';

const base = '/api/pilot/runtime/v1';
const protocol = 'pilot-runtime-v1' as const;
export interface RuntimeIdentity {
  turn_id: string;
  conversation_id: number;
  execution_generation: number;
}
export interface RuntimeObserveOptions {
  signal?: AbortSignal;
  onAccepted?: (identity: { conversationId: number; turnId: string; executionGeneration: number; protocol: typeof protocol }) => void;
  onEvent?: (event: ChatStreamEvent) => void;
  onSnapshot?: (page: PilotTimelinePage) => void | Promise<void>;
}
type RuntimeState = Partial<RuntimeIdentity> & {
  protocol_version?: string;
  request_id?: string;
  generation?: number;
  state?: PilotExecution['state'] | 'accepted' | 'queued';
  execution?: Partial<RuntimeIdentity> & { state?: PilotExecution['state'] | 'accepted' | 'queued' };
  terminal?: { response?: ChatResponse };
  events?: Array<ChatStreamEvent & { event_seq?: number }>;
  event_cursor?: string;
  snapshot_cursor?: string;
  high_watermark?: number;
  next_cursor?: string;
  durable?: PilotTimelinePage;
};

export class RuntimeSubscriptionError extends Error {
  readonly acceptedTurn = true;
  readonly code = 'runtime_subscription_detached';
  constructor(readonly target: RuntimeIdentity) {
    super('连接已断开，任务仍可能在运行。重新打开对话可查看已保存的结果。');
    this.name = 'RuntimeSubscriptionError';
  }
}

export class RuntimeEndedError extends Error {
  readonly acceptedTurn = true;
  readonly code: string;
  constructor(readonly target: RuntimeIdentity, readonly state: string) {
    super(({ failed: '任务执行失败，请查看已保存的记录。', stopped: '任务已停止，已提交的更改仍保留。',
      interrupted: '执行已中断，请查看已保存的记录。', result_unknown: '执行结果待核对，请查看已保存的记录。',
      completed: '任务已完成，请查看已保存的结果。', waiting_confirmation: '任务正在等待确认，请刷新确认状态。',
    } as Record<string, string>)[state] ?? '任务状态已变化，请重新读取已保存的记录。');
    this.name = 'RuntimeEndedError'; this.code = `runtime_${state}`;
  }
}

async function boundedText(response: Response, limit = 8_388_608): Promise<string> {
  if (!response.body) return '';
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let size = 0; let text = '';
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > limit) throw new Error('runtime_json_size_limit');
      text += decoder.decode(chunk.value, { stream: true });
    }
    return text + decoder.decode();
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

async function httpError(response: Response): Promise<Error> {
  let payload: { error?: string; error_code?: string } = {};
  try { payload = JSON.parse(await boundedText(response, 65_536)); } catch { /* Preserve the status even for a malformed body. */ }
  return Object.assign(new Error(payload.error || `HTTP ${response.status}`), { status: response.status, code: payload.error_code });
}

async function readJson(url: string, options?: RuntimeObserveOptions, body?: Record<string, unknown>): Promise<RuntimeState> {
  const response = await fetch(url, {
    method: body ? 'POST' : 'GET', signal: options?.signal,
    headers: { ...authHeaders(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) throw await httpError(response);
  return JSON.parse(await boundedText(response)) as RuntimeState;
}

function identity(value: RuntimeState): RuntimeIdentity {
  for (const key of ['turn_id', 'conversation_id', 'execution_generation'] as const) {
    if (value[key] !== undefined && value.execution?.[key] !== undefined && value[key] !== value.execution[key]) throw new Error('runtime_identity_conflict');
  }
  const candidate = { ...value, ...value.execution };
  if (candidate.execution_generation !== undefined && value.generation !== undefined && candidate.execution_generation !== value.generation) throw new Error('runtime_identity_conflict');
  candidate.execution_generation ??= value.generation;
  if (typeof candidate.turn_id !== 'string' || !candidate.turn_id
    || !Number.isSafeInteger(candidate.conversation_id) || candidate.conversation_id! < 1
    || !Number.isSafeInteger(candidate.execution_generation) || candidate.execution_generation! < 1) {
    throw new Error('runtime_identity_mismatch');
  }
  return candidate as RuntimeIdentity;
}

function requireTarget(state: RuntimeState, target: RuntimeIdentity): void {
  const value = identity(state);
  if (value.turn_id !== target.turn_id || value.conversation_id !== target.conversation_id
    || value.execution_generation !== target.execution_generation) throw new RuntimeEndedError(target, 'result_unknown');
}

function result(state: RuntimeState, target: RuntimeIdentity): ChatResponse | undefined {
  const response = state.terminal?.response;
  if (!response) {
    const stateName = state.execution?.state ?? state.state;
    if (stateName && ['failed', 'stopped', 'interrupted', 'result_unknown', 'completed', 'waiting_confirmation'].includes(stateName)) throw new RuntimeEndedError(target, stateName);
    return undefined;
  }
  if (!['message', 'confirmation_required', 'turn_recovered'].includes(response.type)) throw new RuntimeEndedError(target, 'result_unknown');
  if (response.conversation_id !== target.conversation_id || (response.turn_id !== undefined && response.turn_id !== target.turn_id)
    || (response.execution_generation !== undefined && response.execution_generation !== target.execution_generation)) {
    throw new Error('runtime_response_scope_mismatch');
  }
  return { ...response, turn_id: target.turn_id, execution_generation: target.execution_generation };
}

/** Resolve only the original admission; never infer ownership from latest turn. */
export async function getRuntimeRequestExecution(requestId: string, signal?: AbortSignal): Promise<PilotExecution | null> {
  let value: RuntimeState;
  try { value = await readJson(`${base}/requests/${encodeURIComponent(requestId)}`, { signal }); }
  catch (error) { if ((error as { status?: number }).status === 404) return null; throw error; }
  if (value.protocol_version !== protocol || value.request_id !== requestId) throw new Error('runtime_request_mismatch');
  const target = identity(value);
  const rawState = value.execution?.state ?? value.state;
  const state = rawState === 'queued' || rawState === 'accepted' ? 'running' : rawState;
  if (!state || !['running', 'completed', 'waiting_confirmation', 'failed', 'stopped', 'interrupted', 'result_unknown'].includes(state)) throw new Error('runtime_state_invalid');
  return { ...target, state, protocol };
}

export async function submitRuntimeTurn(body: Record<string, unknown>, options?: RuntimeObserveOptions): Promise<ChatResponse> {
  return submit(`${base}/turns`, body, options);
}

export async function confirmRuntimeTurn(turnId: string, body: Record<string, unknown>, options?: RuntimeObserveOptions): Promise<ChatResponse> {
  return submit(`${base}/turns/${encodeURIComponent(turnId)}/confirm`, body, options);
}

async function submit(url: string, body: Record<string, unknown>, options?: RuntimeObserveOptions): Promise<ChatResponse> {
  const accepted = await readJson(url, options, body);
  options?.signal?.throwIfAborted();
  if (accepted.protocol_version !== protocol) throw new Error('runtime_protocol_mismatch');
  const target = identity(accepted);
  options?.onAccepted?.({ conversationId: target.conversation_id, turnId: target.turn_id,
    executionGeneration: target.execution_generation, protocol });
  return result(accepted, target) ?? observeRuntimeTurn(target, options);
}

/** All recovery below is GET-only. Closing a subscriber never interrupts its task. */
export async function observeRuntimeTurn(target: RuntimeIdentity, options?: RuntimeObserveOptions): Promise<ChatResponse> {
  const url = `${base}/turns/${encodeURIComponent(target.turn_id)}`;
  let high = 0;
  let completed: ChatResponse | undefined;
  const consume = (raw: ChatStreamEvent & { event_seq?: number; kind?: string }, snapshotEvent = false) => {
    if (completed) return;
    if (raw.turn_id !== target.turn_id || raw.conversation_id !== target.conversation_id
      || raw.execution_generation !== target.execution_generation) return;
    const kind = raw.event ?? raw.kind;
    if (kind === 'resync_required') {
      options?.onEvent?.({ ...raw, event: 'status', seq: raw.event_seq ?? raw.seq ?? high,
        data: { phase: 'resync', label: '部分实时进度已省略，正在恢复已保存记录。' } });
      throw new Error('runtime_resync_required');
    }
    if (kind === 'subscribed' || kind === 'heartbeat') return;
    const seq = raw.event_seq ?? raw.seq;
    if (!Number.isSafeInteger(seq) || seq <= high) return;
    if (seq > high + 1 && !snapshotEvent) throw new Error('runtime_event_gap');
    high = seq;
    if (!kind) throw new Error('runtime_event_kind_missing');
    const event = { ...raw, event: kind, seq };
    if (event.event === 'completed') completed = result({ terminal: event.data as { response?: ChatResponse } }, target);
    options?.onEvent?.(event);
  };
  for (let attempt = 0; attempt < 4; attempt += 1) {
    options?.signal?.throwIfAborted();
    try {
      if (attempt > 0) {
        const saved = await readJson(url, options);
        requireTarget(saved, target);
        const response = result(saved, target);
        if (response) return response;
      }
      let snapshot = await readJson(`${url}/snapshot`, options);
      requireTarget(snapshot, target);
      if (snapshot.terminal?.response) {
        const response = result(snapshot, target);
        if (response) return response;
      }
      for (let page = 0; ; page += 1) {
        requireTarget(snapshot, target);
        if (snapshot.durable) {
          if (snapshot.durable.conversation_id !== target.conversation_id) throw new Error('runtime_snapshot_scope_mismatch');
          await options?.onSnapshot?.(snapshot.durable);
        }
        snapshot.events?.forEach((event) => consume(event, true));
        if (completed) return completed;
        if (!snapshot.next_cursor) break;
        if (page >= 99) throw new Error('runtime_snapshot_page_limit');
        snapshot = await readJson(`${url}/snapshot?cursor=${encodeURIComponent(snapshot.next_cursor)}`, options);
      }
      if (Number.isSafeInteger(snapshot.high_watermark)) high = Math.max(high, snapshot.high_watermark!);
      if (snapshot.state && ['failed', 'stopped', 'interrupted', 'result_unknown', 'completed', 'waiting_confirmation'].includes(snapshot.state)) {
        const saved = await readJson(url, options);
        requireTarget(saved, target);
        const response = result(saved, target);
        if (response) return response;
        throw new RuntimeEndedError(target, snapshot.state);
      }
      const cursor = snapshot.event_cursor ?? snapshot.snapshot_cursor;
      if (!cursor) throw new Error('runtime_snapshot_cursor_missing');
      const responseStream = await fetch(`${url}/events?after=${encodeURIComponent(cursor)}`, {
        method: 'GET', headers: authHeaders(), signal: options?.signal,
      });
      if (!responseStream.ok) throw await httpError(responseStream);
      if (!responseStream.body) throw new Error('runtime_observe_unavailable');
      const reader = responseStream.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      const parseFrames = (flush = false) => {
        if (buffer.length > 1_048_576) throw new Error('runtime_frame_limit');
        if (flush && buffer.trim()) buffer += '\n\n';
        let end: number;
        while ((end = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
          const data = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
          if (data) consume(JSON.parse(data));
        }
      };
      try {
        while (!completed) {
          const chunk = await reader.read();
          if (chunk.done) { buffer += decoder.decode(); parseFrames(true); break; }
          buffer = (buffer + decoder.decode(chunk.value, { stream: true })).replace(/\r\n/g, '\n');
          parseFrames();
        }
      } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
      if (completed) return completed;
    } catch (error) {
      options?.signal?.throwIfAborted();
      if (error instanceof RuntimeSubscriptionError || error instanceof RuntimeEndedError
        || [401, 403, 404, 410].includes((error as { status?: number }).status ?? 0)) throw error;
    }
  }
  throw new RuntimeSubscriptionError(target);
}
