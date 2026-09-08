import type { ChatContextInput } from './chat';

const STORAGE_KEY = 'offerpilot.pending_starts.v1';
const RECORD_PREFIX = 'offerpilot.pending_starts.v2.';
const EVENT_NAME = 'offerpilot-pending-starts';
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const fallback = new Map<string, PendingStart | null>();

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
  return JSON.stringify(listPendingStarts());
}

function parse(value: string | null): unknown {
  try { return JSON.parse(value ?? 'null') as unknown; } catch { return null; }
}

function pendingStart(value: unknown): PendingStart | undefined {
  if (!value || typeof value !== 'object') return;
  const item = value as Partial<PendingStart>;
  if (typeof item.requestId !== 'string' || !UUID_V4.test(item.requestId)
    || !Number.isSafeInteger(item.conversationId) || item.conversationId! < 0) return;
  // Only recovery identity is allowed into storage, including when reading v1.
  return { requestId: item.requestId, conversationId: item.conversationId!,
    ...(Number.isSafeInteger(item.acceptedConversationId) && item.acceptedConversationId! >= 0
      ? { acceptedConversationId: item.acceptedConversationId } : {}),
    ...(typeof item.turnId === 'string' ? { turnId: item.turnId } : {}) };
}

export function listPendingStarts(): PendingStart[] {
  const items = new Map<string, PendingStart | null>();
  try {
    if (typeof window !== 'undefined') {
      const storage = window.localStorage;
      const legacy = parse(storage.getItem(STORAGE_KEY));
      if (Array.isArray(legacy)) for (const value of legacy) {
        const item = pendingStart(value);
        if (item) items.set(item.requestId, item);
      }
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (!key?.startsWith(RECORD_PREFIX)) continue;
        const id = key.slice(RECORD_PREFIX.length);
        if (!UUID_V4.test(id)) continue;
        const raw = storage.getItem(key);
        const item = pendingStart(parse(raw));
        if (raw === 'null') items.set(id, null);
        else if (item?.requestId === id) items.set(id, item);
      }
    }
  } catch { /* Storage may be blocked; keep recovery available in this tab. */ }
  for (const [id, item] of fallback) items.set(id, item);
  return [...items.values()].filter((item): item is PendingStart => item !== null)
    .sort((left, right) => left.requestId.localeCompare(right.requestId));
}

function savePendingStart(requestId: string, item: PendingStart | null): void {
  // A mutation touches only its request key, never another tab's submissions.
  try {
    if (typeof window === 'undefined') throw new Error('storage_unavailable');
    const storage = window.localStorage;
    const legacy = parse(storage.getItem(STORAGE_KEY));
    const hasLegacy = Array.isArray(legacy) && legacy.some((value) => pendingStart(value)?.requestId === requestId);
    // Keep v1 read-only. A per-request tombstone suppresses its forgotten entry.
    if (item || hasLegacy) storage.setItem(RECORD_PREFIX + requestId, JSON.stringify(item));
    else storage.removeItem(RECORD_PREFIX + requestId);
    fallback.delete(requestId);
  } catch { fallback.set(requestId, item); }
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(EVENT_NAME));
}

export function rememberPendingStart(requestId: string, conversationId: number): void {
  if (!UUID_V4.test(requestId)) throw new Error('invalid_request_id');
  const items = listPendingStarts();
  if (!items.some((item) => item.requestId === requestId)) savePendingStart(requestId, { requestId, conversationId });
}

export function markPendingStartAccepted(requestId: string, conversationId: number, turnId: string): void {
  const item = listPendingStarts().find((pending) => pending.requestId === requestId);
  if (item) savePendingStart(requestId, { ...item, acceptedConversationId: conversationId, turnId });
}

export function forgetPendingStart(requestId: string): void {
  if (UUID_V4.test(requestId)) savePendingStart(requestId, null);
}

export function forgetConversationStarts(conversationId: number): void {
  for (const item of listPendingStarts()) {
    if (item.conversationId === conversationId || item.acceptedConversationId === conversationId) forgetPendingStart(item.requestId);
  }
}

export function subscribePendingStarts(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener(EVENT_NAME, listener);
  window.addEventListener('storage', listener);
  return () => { window.removeEventListener(EVENT_NAME, listener); window.removeEventListener('storage', listener); };
}
