// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Modal } from 'antd';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
const api = vi.hoisted(() => ({ listConfirmedMemory: vi.fn(), getConfirmedMemory: vi.fn(), changeConfirmedMemory: vi.fn() }));
vi.mock('@/services/confirmedMemory', () => api);
import MemoryProfile, { memoryGroup, localExpiry } from './MemoryProfile';
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement; let cache: QueryClient;
const item = { id: 'one', content: '希望从事 Agent 开发', state: 'active', current_version: 1, confirmed_at: '2026-11-15T00:00:00Z' };
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 60)); });
function button(label: string) { return [...document.querySelectorAll<HTMLButtonElement>('button')].find(el => el.textContent?.replace(/\s/g, '') === label.replace(/\s/g, ''))!; }
async function click(label: string) { act(() => button(label).click()); await flush(); }
function change(value: string) { act(() => { const input = document.querySelector<HTMLTextAreaElement>('#memory-content')!; Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }); }
beforeEach(async () => {
 vi.clearAllMocks(); window.matchMedia = vi.fn().mockReturnValue({ matches:false, addListener:vi.fn(), removeListener:vi.fn(), addEventListener:vi.fn(), removeEventListener:vi.fn() });
 const original = window.getComputedStyle.bind(window); vi.spyOn(window, 'getComputedStyle').mockImplementation(el => original(el));
 api.listConfirmedMemory.mockResolvedValue([item]); api.getConfirmedMemory.mockResolvedValue({ ...item, versions: [] });
 host = document.createElement('div'); document.body.append(host); root = createRoot(host); cache = new QueryClient({ defaultOptions:{ queries:{ retry:false, gcTime:0 } } });
 act(() => root.render(<QueryClientProvider client={cache}><MemoryProfile /></QueryClientProvider>)); await flush();
 act(() => host.querySelector<HTMLButtonElement>('[aria-pressed="false"]')!.click()); await flush();
});
afterEach(async () => { await act(async () => { Modal.destroyAll(); root.unmount(); }); cache.clear(); host.remove(); vi.restoreAllMocks(); });
it('cancels without a write and reopens the saved content', async () => {
 await click('编辑'); change('未保存的更正'); await click('取消'); expect(api.changeConfirmedMemory).not.toHaveBeenCalled(); await click('编辑'); expect(document.querySelector<HTMLTextAreaElement>('#memory-content')!.value).toBe(item.content);
});
it('locks duplicate submits and immediately replaces stale cached text', async () => {
 let resolve!: (value: unknown) => void; api.changeConfirmedMemory.mockImplementation(() => new Promise(r => { resolve = r; }));
 await click('编辑'); change('更正后的目标'); act(() => { button('确认保存').click(); button('确认保存').click(); }); await flush(); expect(api.changeConfirmedMemory).toHaveBeenCalledTimes(1);
 api.listConfirmedMemory.mockResolvedValue([{ ...item, content:'更正后的目标', current_version:2 }]); await act(async () => resolve({ ...item, content:'更正后的目标', current_version:2 })); await flush();
 expect(host.textContent).toContain('更正后的目标'); expect(host.textContent).not.toContain(item.content); expect(api.changeConfirmedMemory.mock.calls[0][1]).toMatchObject({ confirmed:true, expected_version:1, action:'confirm' });
});
it('reuses the original mutation identity after an uncertain response', async () => {
 api.changeConfirmedMemory.mockRejectedValue(new Error('lost response')); await click('编辑'); change('更正'); await click('确认保存'); const first = api.changeConfirmedMemory.mock.calls[0][1]; await click('确认保存'); expect(api.changeConfirmedMemory.mock.calls[1][1]).toEqual(first);
});
it('requires deletion confirmation and clears detail and cached history', async () => {
 cache.setQueryData(['confirmed-memory-history','one'], { ...item, versions:[{content:'旧正文'}] }); await click('删除'); expect(api.changeConfirmedMemory).not.toHaveBeenCalled(); api.changeConfirmedMemory.mockResolvedValue({ id:'one', state:'deleted', current_version:2 }); api.listConfirmedMemory.mockResolvedValue([]); await click('确认删除'); expect(host.textContent).not.toContain(item.content); expect(cache.getQueryData(['confirmed-memory-history','one'])).toBeUndefined();
});
it('refreshes selected content from the authoritative query', async () => {
 api.listConfirmedMemory.mockResolvedValue([{...item, state:'withdrawn', current_version:2}]); await click('刷新'); expect(button('重新确认')).toBeDefined(); expect(button('撤回')).toBeUndefined();
});

it('does not expose a cached previous version after external update and history read failure', async () => {
 cache.setQueryData(['confirmed-memory-history','one',1], { ...item, versions:[{version:1,content:'过期历史正文',confirmed_at:item.confirmed_at}] });
 api.listConfirmedMemory.mockResolvedValue([{...item,current_version:2,content:'其他窗口更正'}]); await click('刷新'); api.getConfirmedMemory.mockRejectedValue(new Error('offline')); await click('查看版本');
 expect(document.body.textContent).toContain('版本记录读取失败'); expect(document.body.textContent).not.toContain('过期历史正文');
});

it('never guesses legacy classification from content', () => {
 expect(memoryGroup(item as never)).toBe(5);
 expect(host.querySelector('aside')?.textContent).toContain('未分类');
 expect(host.querySelector('aside')?.textContent).toContain('未指定有效期');
 expect(memoryGroup({ ...item, metadata: { category:'skill', valid_until:null, evidence_status:'user_confirmed' } } as never)).toBe(2);
});
function field(id: string, value: string) { act(() => {
 const input = document.querySelector<HTMLInputElement | HTMLSelectElement>('#' + id)!;
 const prototype = input.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
 Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(input, value);
 input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles:true }));
}); }
it('uses a new mutation identity for changed metadata after an uncertain response', async () => {
 api.changeConfirmedMemory.mockRejectedValue(new Error('lost response'));
 await click('编辑'); await click('确认保存');
 const original = api.changeConfirmedMemory.mock.calls[0][1];
 expect(original.metadata).toEqual({ category:'unclassified', valid_until:null });
 field('memory-category','goal'); field('memory-valid-until','2027-01-02T15:30');
 await click('确认保存'); const updated = api.changeConfirmedMemory.mock.calls[1][1];
 expect(updated.mutation_id).not.toBe(original.mutation_id);
 expect(updated.metadata).toEqual({ category:'goal', valid_until:new Date('2027-01-02T15:30').toISOString() });
 await click('确认保存'); expect(api.changeConfirmedMemory.mock.calls[2][1]).toEqual(updated);
});
it('keeps expired memory visible and permits explicitly clearing its expiry', async () => {
 const expired = { ...item, metadata:{category:'goal',valid_until:'2020-01-01T12:34:56.123Z',evidence_status:'user_confirmed'}, temporal_status:'expired' };
 api.listConfirmedMemory.mockResolvedValue([expired]); await click('刷新');
 expect(host.querySelector('aside')?.textContent).toContain('已到期，不再召回');
 expect(host.textContent).toContain(item.content);
 api.changeConfirmedMemory.mockRejectedValue(new Error('lost response')); await click('编辑'); await click('确认保存');
 expect(api.changeConfirmedMemory.mock.calls[0][1].metadata.valid_until).toBe(expired.metadata.valid_until);
 field('memory-valid-until',''); await click('确认保存');
 expect(api.changeConfirmedMemory.mock.calls[1][1].metadata).toEqual({category:'goal',valid_until:null});
});
it('cancels category and expiry edits without changing the saved metadata', async () => {
 await click('编辑'); field('memory-category','skill'); field('memory-valid-until','2027-01-02T15:30'); await click('取消');
 expect(api.changeConfirmedMemory).not.toHaveBeenCalled(); await click('编辑');
 expect(document.querySelector<HTMLSelectElement>('#memory-category')!.value).toBe('unclassified');
 expect(document.querySelector<HTMLInputElement>('#memory-valid-until')!.value).toBe('');
 expect(localExpiry(null)).toBe('');
});
it('does not send metadata for withdrawal', async () => {
 api.changeConfirmedMemory.mockResolvedValue({...item,state:'withdrawn',current_version:2});
 await click('撤回'); await click('确认撤回');
 expect(api.changeConfirmedMemory.mock.calls[0][1]).not.toHaveProperty('metadata');
});

it('does not carry cancelled expiry input into withdrawal', async () => {
 await click('编辑'); field('memory-valid-until','invalid-date'); await click('取消');
 api.changeConfirmedMemory.mockResolvedValue({...item,state:'withdrawn',current_version:2});
 await click('撤回'); await click('确认撤回');
 expect(api.changeConfirmedMemory).toHaveBeenCalledTimes(1);
 expect(api.changeConfirmedMemory.mock.calls[0][1]).not.toHaveProperty('metadata');
});

it.each(['周三下午方便联系', '目标是求职，项目经历包含技能练习，近期准备复习'])('shows legacy content as unclassified without any write: %s', async content => {
 const legacy = { ...item, content };
 api.listConfirmedMemory.mockResolvedValue([legacy]); await click('刷新');
 expect(memoryGroup(legacy as never)).toBe(5);
 const detail = host.querySelector('aside')!;
 expect(detail.querySelector('.ant-tag')?.textContent).toBe('未分类');
 const node = [...host.querySelectorAll<HTMLButtonElement>('button[aria-pressed]')].find(el => el.textContent?.includes(content))!;
 expect(node.closest('section')?.querySelector('h2')?.textContent).toContain('未分类');
 await click('编辑');
 expect(document.querySelector<HTMLSelectElement>('#memory-category')!.value).toBe('unclassified');
 await click('取消');
 expect(api.changeConfirmedMemory).not.toHaveBeenCalled();
});

it('uses the user correction rather than competing keywords after save and refresh', async () => {
 const content = '目标是求职，项目经历包含技能练习，近期准备复习';
 const legacy = { ...item, content };
 api.listConfirmedMemory.mockResolvedValue([legacy]); await click('刷新'); await click('编辑');
 field('memory-category', 'experience');
 const corrected = { ...legacy, current_version:2, metadata:{category:'experience',valid_until:null,evidence_status:'user_confirmed'}, temporal_status:'unknown' };
 api.changeConfirmedMemory.mockResolvedValue(corrected); api.listConfirmedMemory.mockResolvedValue([corrected]);
 await click('确认保存'); await click('刷新');
 expect(api.changeConfirmedMemory.mock.calls[0][1]).toMatchObject({content, expected_version:1, metadata:{category:'experience',valid_until:null}});
 expect(host.querySelector('aside .ant-tag')?.textContent).toBe('经历');
 const node = [...host.querySelectorAll<HTMLButtonElement>('button[aria-pressed]')].find(el => el.textContent?.includes(content))!;
 expect(node.closest('section')?.querySelector('h2')?.textContent).toContain('经历');
 await click('编辑'); expect(document.querySelector<HTMLSelectElement>('#memory-category')!.value).toBe('experience');
});
