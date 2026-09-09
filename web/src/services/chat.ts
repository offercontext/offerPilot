import type {
  ChatMessage,
  ChatResponse,
  ChatExecutionResponse,
  PilotTurnState,
  PilotExecution,
  PilotInterruptResult,
  ChatStreamEvent,
  Conversation,
  PilotActionRequest,
  PilotContextAttachment,
  PilotPageContext,
} from '@/types/chat';
import { authHeaders } from './authToken';
import { createApiClient } from './http';
import { confirmRuntimeTurn, RuntimeEndedError, submitRuntimeTurn } from './pilotRuntime';
import type { PilotTimelinePage } from '@/features/actionPresentation/contracts';
import { forgetConversationStarts, forgetPendingStart, listPendingStarts, markPendingStartAccepted, rememberPendingStart, settlePendingStartForExecution } from './chatSubmission';

const http = createApiClient({ baseURL: '/api', timeout: 130000 });
export const SETTINGS_QUERY_KEY = ['settings'] as const;

export async function getPilotExecution(conversationId: number): Promise<PilotExecution | null> {
  const { data } = await http.get<{ execution: PilotExecution | null }>(`/chat/conversations/${conversationId}/execution`);
  return data.execution;
}

export async function interruptPilotExecution(target: PilotExecution, commandId: string): Promise<PilotInterruptResult> {
  const { data } = await http.post<PilotInterruptResult>(`/chat/turns/${target.turn_id}/interrupt`, {
    command_id: commandId,
    expected_generation: target.execution_generation,
  });
  return data;
}

export interface ChatContextInput {
  context_type?: 'workspace' | 'application' | 'global' | string;
  context_ref?: string | number;
  mode?: string;
  page_context?: PilotPageContext;
  attachments?: PilotContextAttachment[];
  pilot_action?: PilotActionRequest;
}

export interface ChatRequestOptions {
  signal?: AbortSignal;
  requestId?: string;
  onAccepted?: (identity: { conversationId: number; turnId: string; executionGeneration?: number; protocol?: 'pilot-runtime-v1' }) => void;
}

export interface ChatStreamRequestOptions extends ChatRequestOptions {
  onEvent?: (event: ChatStreamEvent) => void;
  onSnapshot?: (page: PilotTimelinePage) => void | Promise<void>;
}

export class ChatStreamError extends Error {
  code?: string;
  retryable?: boolean;
  status?: number;
  acceptedTurn: boolean;

  constructor(message: string, code?: string, retryable?: boolean, status?: number, acceptedTurn = false) {
    super(message);
    this.name = 'ChatStreamError';
    this.code = code;
    this.retryable = retryable;
    this.status = status;
    this.acceptedTurn = acceptedTurn;
  }
}

export type ConfirmationInput =
  | {
      approved: true;
      operation_id?: string;
      confirmation_token: string;
      edited_args?: Record<string, unknown>;
      rejection_feedback?: never;
    }
  | {
      approved: false;
      operation_id?: string;
      confirmation_token: string;
      rejection_feedback?: string;
      edited_args?: never;
    };

export type ConfirmationRequest = ConfirmationInput | boolean;

function confirmationPayload(input: ConfirmationRequest): ConfirmationInput | { approved: boolean } {
  return typeof input === 'boolean' ? { approved: input } : input;
}

export async function sendChat(
  message: string,
  conversationId?: number,
  context?: ChatContextInput,
  options?: ChatRequestOptions,
): Promise<ChatResponse> {
  const requestId = options?.requestId ?? crypto.randomUUID();
  const wasPending = listPendingStarts().some((item) => item.requestId === requestId);
  rememberPendingStart(requestId, conversationId ?? 0);
  const { data } = await http.post<ChatResponse>(
    '/chat',
    {
      message,
      request_id: requestId,
      conversation_id: conversationId ?? 0,
      ...(context?.context_type ? { context_type: context.context_type } : {}),
      ...(context?.context_ref !== undefined ? { context_ref: String(context.context_ref) } : {}),
      ...(context?.mode ? { mode: context.mode } : {}),
      ...(context?.page_context !== undefined ? { page_context: context.page_context } : {}),
      ...(context?.attachments !== undefined ? { attachments: context.attachments } : {}),
      ...(context?.pilot_action !== undefined ? { pilot_action: context.pilot_action } : {}),
    },
    { signal: options?.signal },
  ).catch((error: unknown) => {
    forgetRejectedStart(requestId, wasPending, error);
    throw error;
  });
  settlePendingStart(requestId, data);
  return data;
}

export async function confirmAction(
  conversationId: number,
  input: ConfirmationRequest,
  options?: ChatRequestOptions,
): Promise<ChatExecutionResponse> {
  const { data } = await http.post<ChatExecutionResponse>(
    '/chat/confirm',
    {
      conversation_id: conversationId,
      ...confirmationPayload(input),
    },
    { signal: options?.signal },
  );
  return data;
}

export async function undoLastWrite(
  conversationId: number,
  parentOperationId?: string,
  options?: ChatRequestOptions,
): Promise<Extract<ChatResponse, { type: 'message' }>> {
  const { data } = await http.post<Extract<ChatResponse, { type: 'message' }>>(
    '/chat/undo-last-write',
    {
      conversation_id: conversationId,
      ...(parentOperationId ? { parent_operation_id: parentOperationId } : {}),
    },
    { signal: options?.signal },
  );
  return data;
}

export function createSseParser(onEvent: (event: ChatStreamEvent) => void) {
  let buffer = '';

  function parseFrame(frame: string) {
    const dataLines: string[] = [];
    let eventName = '';
    for (const line of frame.split('\n')) {
      if (line.startsWith(':')) continue;
      if (line.startsWith('event:')) {
        eventName = line.slice('event:'.length).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice('data:'.length).trim());
      }
    }
    if (!dataLines.length) return;
    const parsed = JSON.parse(dataLines.join('\n')) as ChatStreamEvent;
    onEvent({ ...parsed, event: parsed.event || eventName });
  }

  return {
    push(chunk: string) {
      buffer += chunk;
      buffer = buffer.replace(/\r\n/g, '\n');
      let frameEnd = buffer.indexOf('\n\n');
      while (frameEnd >= 0) {
        const frame = buffer.slice(0, frameEnd).trim();
        buffer = buffer.slice(frameEnd + 2);
        if (frame) parseFrame(frame);
        frameEnd = buffer.indexOf('\n\n');
      }
    },
    flush() {
      const frame = buffer.trim();
      buffer = '';
      if (frame) parseFrame(frame);
    },
  };
}

export async function legacyStreamChat(
  message: string,
  conversationId?: number,
  context?: ChatContextInput,
  options?: ChatStreamRequestOptions,
): Promise<ChatResponse> {
  const requestId = options?.requestId ?? crypto.randomUUID();
  const wasPending = listPendingStarts().some((item) => item.requestId === requestId);
  rememberPendingStart(requestId, conversationId ?? 0);
  const response = await postChatStream(
    '/api/chat/stream',
    {
      message,
      request_id: requestId,
      conversation_id: conversationId ?? 0,
      ...(context?.context_type ? { context_type: context.context_type } : {}),
      ...(context?.context_ref !== undefined ? { context_ref: String(context.context_ref) } : {}),
      ...(context?.mode ? { mode: context.mode } : {}),
      ...(context?.page_context !== undefined ? { page_context: context.page_context } : {}),
      ...(context?.attachments !== undefined ? { attachments: context.attachments } : {}),
      ...(context?.pilot_action !== undefined ? { pilot_action: context.pilot_action } : {}),
    },
    options,
  ).catch((error: unknown) => {
    forgetRejectedStart(requestId, wasPending, error);
    throw error;
  });
  settlePendingStart(requestId, response);
  return response;
}

function settlePendingStart(requestId: string, response: ChatResponse): void {
  if (response.type === 'turn_recovered' && ['accepted', 'started'].includes(response.turn?.state)) {
    markPendingStartAccepted(requestId, response.conversation_id, response.turn_id);
  } else {
    forgetPendingStart(requestId);
  }
}

function forgetRejectedStart(requestId: string, wasPending: boolean, error: unknown): void {
  const response = (error as { response?: { status?: number; data?: { turn_id?: unknown } } })?.response;
  const status = error instanceof ChatStreamError ? error.status : response?.status ?? (error as { status?: number })?.status;
  const accepted = error instanceof ChatStreamError ? error.acceptedTurn
    : (error as { acceptedTurn?: boolean })?.acceptedTurn === true || typeof response?.data?.turn_id === 'string';
  const capacityRejectedBeforeAdmission = status === 429
    && (error as { code?: string })?.code === 'runtime_capacity_exhausted';
  // A 5xx may follow a committed start (including a lost upstream response).
  // A rejected retry also cannot disprove an earlier unknown admission.
  if (!accepted && (status === 410 || capacityRejectedBeforeAdmission || (!wasPending && status !== undefined && [400, 401, 403, 404, 422].includes(status)))) {
    forgetPendingStart(requestId);
  }
}

export async function legacyStreamConfirmAction(
  conversationId: number,
  input: ConfirmationRequest,
  options?: ChatStreamRequestOptions,
): Promise<ChatExecutionResponse> {
  const response = await postChatStream(
    '/api/chat/confirm/stream',
    {
      conversation_id: conversationId,
      ...confirmationPayload(input),
    },
    options,
  );
  if (response.type === 'turn_recovered') throw new Error('confirmation_response_mismatch');
  return response;
}

export async function streamChat(
  message: string,
  conversationId?: number,
  context?: ChatContextInput,
  options?: ChatStreamRequestOptions,
): Promise<ChatResponse> {
  const requestId = options?.requestId ?? crypto.randomUUID();
  const wasPending = listPendingStarts().some((item) => item.requestId === requestId);
  rememberPendingStart(requestId, conversationId ?? 0);
  const response = await submitRuntimeTurn({
    message, request_id: requestId, conversation_id: conversationId ?? 0,
    ...context, ...(context?.context_ref !== undefined ? { context_ref: String(context.context_ref) } : {}),
  }, {
    ...options,
    onAccepted: (identity) => {
      markPendingStartAccepted(requestId, identity.conversationId, identity.turnId);
      options?.onAccepted?.(identity);
    },
  }).catch((error: unknown) => {
    if (error instanceof RuntimeEndedError) {
      settlePendingStartForExecution({ ...error.target, state: error.state }, requestId);
    }
    forgetRejectedStart(requestId, wasPending, error);
    throw error;
  });
  settlePendingStart(requestId, response);
  return response;
}

export async function streamConfirmAction(
  conversationId: number,
  input: ConfirmationRequest,
  options?: ChatStreamRequestOptions,
): Promise<ChatExecutionResponse> {
  const execution = await getPilotExecution(conversationId);
  if (!execution) throw new Error('当前操作的任务身份不可用，请刷新确认状态。');
  const response = await confirmRuntimeTurn(execution.turn_id, {
    request_id: options?.requestId ?? crypto.randomUUID(), conversation_id: conversationId,
    ...confirmationPayload(input),
  }, options);
  if (response.type === 'turn_recovered') throw new Error('confirmation_response_mismatch');
  return response;
}

async function postChatStream(
  url: string,
  body: Record<string, unknown>,
  options?: ChatStreamRequestOptions,
): Promise<ChatResponse> {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(body),
    signal: options?.signal,
  });
  const turnId = response.headers.get('X-Pilot-Turn-Id');
  const acceptedConversationId = Number(response.headers.get('X-Pilot-Conversation-Id'));
  const executionGeneration = Number(response.headers.get('X-Pilot-Execution-Generation'));
  if (turnId && Number.isSafeInteger(acceptedConversationId) && acceptedConversationId > 0) {
    if (typeof body.request_id === 'string') markPendingStartAccepted(body.request_id, acceptedConversationId, turnId);
    options?.onAccepted?.({ conversationId: acceptedConversationId, turnId,
      ...(Number.isSafeInteger(executionGeneration) && executionGeneration > 0 ? { executionGeneration } : {}),
    });
  }
  if (!response.ok) {
    throw await streamHttpError(response);
  }
  if (response.headers.get('content-type')?.includes('application/json')) {
    const data = await response.json() as ChatResponse;
    if (!['message', 'confirmation_required', 'turn_recovered'].includes(data.type)) throw new Error('chat_response_mismatch');
    return data;
  }
  if (!response.body) {
    throw new Error('对话连接中断，请稍后重试。');
  }

  let completed: ChatResponse | undefined;
  const decoder = new TextDecoder();
  const parser = createSseParser((event) => {
    options?.onEvent?.(event);
    if (event.event === 'completed') {
      const data = event.data as { response?: ChatResponse };
      completed = data.response;
    }
    if (event.event === 'error') {
      const data = event.data as { code?: string; message?: string; retryable?: boolean };
      throw new ChatStreamError(
        data.message || '对话失败，请稍后重试',
        data.code,
        data.retryable,
      );
    }
  });
  const reader = response.body.getReader();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.push(decoder.decode(value, { stream: true }));
  }
  parser.push(decoder.decode());
  parser.flush();
  if (!completed) {
    throw new Error('对话没有返回完整结果，请重试。');
  }
  return { ...completed, ...(turnId ? { turn_id: turnId } : {}), ...(typeof body.request_id === 'string' ? { request_id: body.request_id } : {}) };
}

async function streamHttpError(response: Response) {
  try {
    const payload = (await response.json()) as { error?: string; error_code?: string; retryable?: boolean; turn_id?: unknown };
    return new ChatStreamError(payload.error || `HTTP ${response.status}`, payload.error_code || `http_${response.status}`, payload.retryable, response.status, typeof payload.turn_id === 'string');
  } catch {
    return new ChatStreamError(`HTTP ${response.status}`, `http_${response.status}`, undefined, response.status);
  }
}

export async function listConversations(includeArchived = false): Promise<Conversation[]> {
  const { data } = await http.get<Conversation[]>('/chat/conversations', {
    params: includeArchived ? { include_archived: true } : undefined,
  });
  return data ?? [];
}

export async function getConversation(id: number): Promise<ChatMessage[]> {
  const { data } = await http.get<ChatMessage[]>(`/chat/conversations/${id}`);
  return data ?? [];
}

export async function deleteConversation(id: number): Promise<void> {
  await http.delete(`/chat/conversations/${id}`);
  forgetConversationStarts(id);
}

export async function getPilotRequest(requestId: string): Promise<PilotTurnState> {
  const { data } = await http.get<PilotTurnState>(`/chat/requests/${encodeURIComponent(requestId)}`);
  return data;
}

export async function findPilotTurn(requestId: string): Promise<PilotTurnState> {
  const { data } = await http.get<PilotTurnState>(`/chat/requests/${encodeURIComponent(requestId)}`);
  return data;
}

export interface UpdateConversationPayload {
  title?: string;
  pinned?: boolean;
  archived?: boolean;
  context_type?: string;
  context_ref?: string;
}

export async function updateConversation(
  id: number,
  payload: UpdateConversationPayload,
): Promise<Conversation> {
  const { data } = await http.patch<Conversation>(`/chat/conversations/${id}`, payload);
  return data;
}

export interface Settings {
  version: string;
  data_dir: string;
  chat_auto_approve_writes: boolean;
  active_provider_id: string;
  fallback_provider_ids: string[];
  providers: AIProviderProfile[];
  base_url: string;
  model: string;
  has_api_key: boolean;
  runtime_mode: 'local' | 'server';
  auth_enabled: boolean;
  has_auth_token: boolean;
  log_level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
}

export interface LogEntry {
  level: string;
  message: string;
}

export interface LogsPage {
  entries: LogEntry[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface AIProviderProfile {
  id: string;
  label: string;
  provider: string;
  base_url: string;
  model: string;
  enabled: boolean;
  supports_json_schema: boolean;
  context_window: number;
  max_output_tokens: number;
  has_api_key: boolean;
}

export interface UpdateSettingsPayload {
  chat_auto_approve_writes: boolean;
  active_provider_id?: string;
  fallback_provider_ids?: string[];
  providers?: Array<Omit<AIProviderProfile, 'has_api_key'> & { api_key?: string }>;
  base_url?: string;
  model?: string;
  api_key?: string;
}

export interface ProviderConnectionTestPayload {
  provider_id?: string;
  provider?: Omit<AIProviderProfile, 'has_api_key'> & { api_key?: string };
}

export interface ProviderConnectionTestResult {
  ok: boolean;
  provider_id?: string;
  model?: string;
  latency_ms?: number;
  message?: string;
  error?: string;
}

export interface SettingsBackup {
  version: number;
  exported_at: string;
  runtime_mode: Settings['runtime_mode'];
  auth_enabled: boolean;
  has_auth_token: boolean;
  log_level: Settings['log_level'];
  chat_auto_approve_writes: boolean;
  active_provider_id: string;
  fallback_provider_ids: string[];
  providers: AIProviderProfile[];
}

export async function getSettings(): Promise<Settings> {
  const { data } = await http.get<Settings>('/settings');
  return data;
}

export async function updateSettings(payload: UpdateSettingsPayload): Promise<Settings> {
  const { data } = await http.put<Settings>('/settings', payload);
  return data;
}

export async function testProviderConnection(
  payload: ProviderConnectionTestPayload,
): Promise<ProviderConnectionTestResult> {
  const { data } = await http.post<ProviderConnectionTestResult>('/settings/providers/test', payload);
  return data;
}

export async function getSettingsBackup(): Promise<SettingsBackup> {
  const { data } = await http.get<SettingsBackup>('/settings/backup');
  return data;
}

export async function exportBackup(path = '/backups/export'): Promise<void> {
  const { data } = await http.get<Blob>(path, { responseType: 'blob' });
  const archive = data instanceof Blob ? data : new Blob([data], { type: 'application/zip' });
  const url = URL.createObjectURL(archive);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'offerpilot-backup.zip';
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function getLogs(limit = 20, offset = 0, level = ''): Promise<LogsPage> {
  const { data } = await http.get<LogsPage>('/logs', {
    params: { limit, offset, ...(level ? { level } : {}) },
  });
  return {
    entries: data.entries ?? [],
    total: data.total ?? 0,
    limit: data.limit ?? limit,
    offset: data.offset ?? offset,
    has_more: data.has_more ?? false,
  };
}

export async function updateAutoApprove(value: boolean): Promise<Settings> {
  const current = await getSettings();
  return updateSettings({
    chat_auto_approve_writes: value,
    base_url: current.base_url,
    model: current.model,
  });
}
