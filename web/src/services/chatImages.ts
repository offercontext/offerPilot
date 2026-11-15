import { createApiClient } from './http';
import type { ProviderConnectionTestPayload, ProviderConnectionTestResult } from './chat';

export interface ChatImageReference {
  id: string;
  sha256: string;
  mime_type: string;
  size_bytes: number;
  width: number;
  height: number;
  normalization_version: string;
}

export interface ImageCapability {
  enabled: boolean;
  reason: string;
  provider_id: string;
  model: string;
  max_images: number;
  max_file_bytes: number;
  max_total_bytes: number;
  accepted_mime_types: string[];
}

export interface ImageUploadScope {
  conversation_id?: number;
  context_type?: string;
  context_ref?: string | number;
}

export function chatImageScope(conversationId: number | undefined, context: ImageUploadScope) {
  const scope = { conversation_id: conversationId, context_type: context.context_type, context_ref: context.context_ref };
  const scopeKey = conversationId === undefined
    ? `new:${scope.context_type ?? 'workspace'}:${scope.context_ref ?? ''}` : `conversation:${conversationId}`;
  return { scope, scopeKey };
}

const http = createApiClient({ baseURL: '/api', timeout: 45000 });
export async function getImageCapability(): Promise<ImageCapability> {
  return (await http.get<ImageCapability>('/chat/image-capability')).data;
}
export async function uploadChatImage(file: File, scope: ImageUploadScope): Promise<ChatImageReference> {
  const form = new FormData();
  form.append('file', file);
  if (scope.conversation_id !== undefined) form.append('conversation_id', String(scope.conversation_id));
  else {
    form.append('context_type', scope.context_type ?? 'workspace');
    form.append('context_ref', String(scope.context_ref ?? ''));
  }
  return (await http.post<ChatImageReference>('/chat/attachments', form)).data;
}
export async function readChatImage(id: string): Promise<Blob> {
  return (await http.get<Blob>(`/chat/attachments/${encodeURIComponent(id)}`, { responseType: 'blob' })).data;
}
export async function testProviderImage(payload: ProviderConnectionTestPayload): Promise<ProviderConnectionTestResult> {
  return (await http.post<ProviderConnectionTestResult>('/settings/providers/test-image', payload)).data;
}
