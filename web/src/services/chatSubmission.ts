import type { ChatContextInput } from './chat';

const STORAGE_KEY = 'offerpilot.pending_starts.v1';
const EVENT_NAME = 'offerpilot-pending-starts';
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
let fallback = '[]';

export interface ChatSubmission {
  requestId: string;
  message: string;
  conversationId?: number;
  context: ChatContextInput;
}

export interface PendingStart {
  requestId: string;
  conversationId: number;
  acceptedConversationId?: number;
  turnId?: string;
}

export function createChatSubmission(message: string, conversationId: number | undefined, context: ChatContextInput): ChatSubmission {
  return { requestId: crypto.randomUUID(), message, conversationId, context: JSON.parse(JSON.stringify(context)) as ChatContextInput };
}

export function pendingStartsSnapshot(): string {
  try { return typeof window === 'undefined' ? fallback : window.localStorage.getItem(STORAGE_KEY) ?? '[]'; }
  catch { return fallback; }
}

export function listPendingStarts(): PendingStart[] {
  try {
    const value = JSON.parse(pendingStartsSnapshot()) as unknown;
    return Array.isArray(value) ? value.filter((item): item is PendingStart => item && UUID_V4.test(item.requestId)
      && Number.isSafeInteger(item.conversationId) && item.conversationId >= 0) : [];
  } catch { return []; }
}

function savePendingStarts(items: PendingStart[]): void {
  // This record intentionally contains no message, attachments, request body,
  // model output, token or approval credentials.
  fallback = JSON.stringify(items);
  try { if (typeof window !== 'undefined') window.localStorage.setItem(STORAGE_KEY, fallback); } catch { /* Memory recovery remains available. */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(EVENT_NAME));
}

export function rememberPendingStart(requestId: string, conversationId: number): void {
  if (!UUID_V4.test(requestId)) throw new Error('invalid_request_id');
  const items = listPendingStarts();
  if (!items.some((item) => item.requestId === requestId)) savePendingStarts([...items, { requestId, conversationId }]);
}

export function markPendingStartAccepted(requestId: string, conversationId: number, turnId: string): void {
  savePendingStarts(listPendingStarts().map((item) => item.requestId === requestId
    ? { ...item, acceptedConversationId: conversationId, turnId } : item));
}

export function forgetPendingStart(requestId: string): void {
  savePendingStarts(listPendingStarts().filter((item) => item.requestId !== requestId));
}

export function forgetConversationStarts(conversationId: number): void {
  savePendingStarts(listPendingStarts().filter((item) => item.conversationId !== conversationId && item.acceptedConversationId !== conversationId));
}

export function subscribePendingStarts(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener(EVENT_NAME, listener);
  window.addEventListener('storage', listener);
  return () => { window.removeEventListener(EVENT_NAME, listener); window.removeEventListener('storage', listener); };
}
