// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const service = vi.hoisted(() => ({ list: vi.fn(), mutate: vi.fn() }));
vi.mock('@/services/knowledgeNoteLifecycle', () => ({ listManagedKnowledgeNotes: service.list, mutateKnowledgeNote: service.mutate }));
import KnowledgeNoteManager from './KnowledgeNoteManager';
const note = { id: 7, title: '已确认知识', version_id: 21, version_number: 2, archived: false, source_id: 31,
  source_status: 'source_changed', captured_at: '2026-10-01',
  content: { blocks: [{ block_id: 'b1', text: '已确认正文', evidence_refs: [{ fragment_id: 'f1', excerpt: '冻结依据' }], evidence: [{ id: 'e1', path: '/questions', excerpt: '冻结依据', line_start: 1, line_end: 2 }] }] },
  evidence: [{ id: 'e1', path: '/questions', excerpt: '冻结依据', line_start: 1, line_end: 2 }],
};
let root: Root; let host: HTMLDivElement; let client: QueryClient;
const flush = () => act(async () => { await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 20)); });
const click = (label: string) => act(() => [...document.querySelectorAll('button')].find((button) => button.textContent?.replace(/\s/g, '') === label)?.click());
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn() }) });
  service.list.mockReset().mockResolvedValue([note]); service.mutate.mockReset().mockResolvedValue(undefined);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); client.clear(); host.remove(); });
describe('KnowledgeNoteManager in reference knowledge', () => {
  it('shows confirmed notes directly and follows frozen source evidence without importing or saving', async () => {
    act(() => root.render(<QueryClientProvider client={client}><KnowledgeNoteManager embedded /></QueryClientProvider>));
    await flush();
    expect(host.textContent).toContain('已确认正文');
    expect(host.textContent).toContain('来源已更新，已确认内容仍保留');
    click('查看来源与依据'); await flush();
    expect(document.body.textContent).toContain('资料来源 #31');
    expect(document.body.textContent).toContain('/questions');
    expect(document.body.textContent).toContain('冻结依据');
    expect(service.mutate).not.toHaveBeenCalled();
    click('返回知识笔记'); await flush();
    expect(host.textContent).toContain('已确认正文');
  });
  it('keeps the original mutation identity after an uncertain save and blocks duplicate submissions', async () => {
    let reject!: (reason: unknown) => void;
    service.mutate.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
    act(() => root.render(<QueryClientProvider client={client}><KnowledgeNoteManager embedded /></QueryClientProvider>));
    await flush(); click('编辑'); await flush();
    click('确认保存新版本'); click('确认保存新版本');
    expect(service.mutate).toHaveBeenCalledTimes(1);
    const first = service.mutate.mock.calls[0][1];
    await act(async () => reject(new Error('network uncertain')));
    expect(document.body.textContent).toContain('操作未确认');
    click('确认保存新版本'); await flush();
    expect(service.mutate).toHaveBeenCalledTimes(2);
    expect(service.mutate.mock.calls[1][1]).toEqual(first);
  });
  it('does not save an edit on open, cancel or repeated entry', async () => {
    act(() => root.render(<QueryClientProvider client={client}><KnowledgeNoteManager embedded /></QueryClientProvider>));
    await flush(); click('编辑'); await flush();
    const titleInput = document.querySelector<HTMLInputElement>('input#knowledge-note-title');
    expect(titleInput).not.toBeNull();
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(titleInput, '未保存的修订');
      titleInput?.dispatchEvent(new Event('input', { bubbles: true }));
    });
    click('取消'); await flush(); click('编辑'); await flush();
    expect((document.querySelector('input#knowledge-note-title') as HTMLInputElement).value).toBe(note.title);
    expect(service.mutate).not.toHaveBeenCalled();
    click('确认保存新版本'); await flush();
    expect(service.mutate).toHaveBeenCalledWith(7, expect.objectContaining({ expected_version_id: 21, confirmed: true, action: 'revise', title: note.title }));
  });
});
