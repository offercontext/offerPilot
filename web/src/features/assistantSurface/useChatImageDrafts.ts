import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { uploadChatImage, type ChatImageReference, type ImageUploadScope } from '@/services/chatImages';

export interface ChatImageDraft {
  key: string;
  scopeKey: string;
  scope: ImageUploadScope;
  name: string;
  file?: File;
  previewUrl?: string;
  reference?: ChatImageReference;
  status: 'uploading' | 'ready' | 'error';
  error?: string;
}

/** Memory-only drafts owned by the conversation controller, shared by both shells. */
export function useChatImageDrafts() {
  const [drafts, setDrafts] = useState<ChatImageDraft[]>([]);
  const current = useRef(drafts);
  const alive = useRef(true);
  const update = useCallback((items: ChatImageDraft[]) => { current.current = items; setDrafts(items); }, []);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      current.current.forEach((item) => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl); });
    };
  }, []);
  const remove = useCallback((keys: string[]) => {
    current.current.filter((item) => keys.includes(item.key)).forEach((item) => {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    });
    update(current.current.filter((item) => !keys.includes(item.key)));
  }, [update]);
  const upload = useCallback(async (draft: ChatImageDraft) => {
    if (!draft.file) return;
    update(current.current.map((item) => item.key === draft.key ? { ...item, status: 'uploading', error: undefined } : item));
    try {
      const reference = await uploadChatImage(draft.file, draft.scope);
      if (alive.current) update(current.current.map((item) => item.key === draft.key ? { ...item, reference, status: 'ready' } : item));
    } catch (error) {
      const data = (error as { response?: { data?: { error?: string } } }).response?.data;
      if (alive.current) update(current.current.map((item) => item.key === draft.key
        ? { ...item, status: 'error', error: data?.error ?? '上传失败，请重试或移除图片。' } : item));
    }
  }, [update]);
  const add = useCallback((files: File[], scope: ImageUploadScope, scopeKey: string): string | undefined => {
    const existing = current.current.filter((item) => item.scopeKey === scopeKey);
    if (existing.length + files.length > 4) return '每条消息最多添加 4 张图片。';
    if (files.some((file) => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type))) return '仅支持 PNG、JPEG 和静态 WebP。';
    if (files.some((file) => file.size > 10 * 1024 * 1024)) return '单张图片不能超过 10 MiB，请裁剪或拆分。';
    const total = existing.reduce((sum, item) => sum + (item.reference?.size_bytes ?? item.file?.size ?? 0), 0)
      + files.reduce((sum, file) => sum + file.size, 0);
    if (total > 20 * 1024 * 1024) return '每条消息的图片总大小不能超过 20 MiB。';
    const added: ChatImageDraft[] = files.map((file) => ({ key: crypto.randomUUID(), scopeKey, scope,
      name: file.name || '粘贴的图片', file, previewUrl: URL.createObjectURL(file), status: 'uploading' }));
    update([...current.current, ...added]);
    added.forEach((item) => { void upload(item); });
  }, [update, upload]);
  const reference = useCallback((image: ChatImageReference, scope: ImageUploadScope, scopeKey: string): string | undefined => {
    const existing = current.current.filter((item) => item.scopeKey === scopeKey);
    if (existing.some((item) => item.reference?.id === image.id)) return;
    if (existing.length >= 4) return '每条消息最多添加 4 张图片。';
    if (existing.reduce((sum, item) => sum + (item.reference?.size_bytes ?? item.file?.size ?? 0), 0) + image.size_bytes > 20 * 1024 * 1024) return '图片总大小不能超过 20 MiB。';
    update([...current.current, { key: crypto.randomUUID(), scopeKey, scope, name: '引用的图片', reference: image, status: 'ready' }]);
  }, [update]);
  return useMemo(() => ({ drafts, add, remove, retry: upload, reference }), [drafts, add, remove, upload, reference]);
}
