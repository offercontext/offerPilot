// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useChatImageDrafts } from './useChatImageDrafts';
import { uploadChatImage, type ChatImageReference } from '@/services/chatImages';

vi.mock('@/services/chatImages', () => ({ uploadChatImage: vi.fn() }));
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let owner: ReturnType<typeof useChatImageDrafts>;
let root: Root;
let container: HTMLDivElement;
const ref: ChatImageReference = { id: 'img_1', sha256: 'a', mime_type: 'image/png', size_bytes: 4, width: 2, height: 2, normalization_version: 'image-normalize-v1' };
function Host() { owner = useChatImageDrafts(); return null; }
beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => 'blob:local');
  URL.revokeObjectURL = vi.fn();
  container = document.createElement('div');
  root = createRoot(container);
  act(() => root.render(<Host />));
});
afterEach(() => { act(() => root.unmount()); });

it('keeps failed drafts for retry, isolates scopes and consumes only accepted images', async () => {
  const upload = vi.mocked(uploadChatImage);
  upload.mockRejectedValueOnce(new Error('offline')).mockResolvedValue(ref);
  await act(async () => { owner.add([new File(['test'], 'shot.png', { type: 'image/png' })], {}, 'new:workspace'); });
  expect(owner.drafts[0].status).toBe('error');
  await act(async () => { await owner.retry(owner.drafts[0]); });
  expect(owner.drafts[0].reference).toEqual(ref);
  const accepted = owner.drafts[0].key;
  act(() => { owner.reference({ ...ref, id: 'img_2' }, { conversation_id: 2 }, 'conversation:2'); });
  act(() => owner.remove([accepted]));
  expect(owner.drafts.map((item) => item.scopeKey)).toEqual(['conversation:2']);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local');
});

it('does not resurrect an image removed while its upload was pending', async () => {
  let resolve!: (value: ChatImageReference) => void;
  vi.mocked(uploadChatImage).mockReturnValue(new Promise((done) => { resolve = done; }));
  act(() => { owner.add([new File(['test'], 'shot.png', { type: 'image/png' })], {}, 'new'); });
  act(() => owner.remove([owner.drafts[0].key]));
  await act(async () => resolve(ref));
  expect(owner.drafts).toEqual([]);
});

it('checks the combined selection limit before creating URLs or uploads', () => {
  const files = Array.from({ length: 5 }, () => new File(['test'], 'shot.png', { type: 'image/png' }));
  expect(owner.add(files, {}, 'new')).toContain('4');
  expect(uploadChatImage).not.toHaveBeenCalled();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});
