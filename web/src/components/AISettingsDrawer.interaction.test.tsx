// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Settings } from '@/services/chat';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ getSettings: vi.fn(), updateSettings: vi.fn(), testProviderConnection: vi.fn() }));
const imageApi = vi.hoisted(() => ({ testProviderImage: vi.fn() }));
vi.mock('@/services/chatImages', () => imageApi);
vi.mock('@/services/chat', () => ({ ...api, SETTINGS_QUERY_KEY: ['settings'] }));
import AISettingsDrawer from './AISettingsDrawer';

const settings: Settings = {
  version: 'test', data_dir: '', chat_auto_approve_writes: false, active_provider_id: 'one', fallback_provider_ids: ['two'], base_url: 'https://example.test', model: 'model-one', has_api_key: true, runtime_mode: 'local', auth_enabled: false, has_auth_token: false, log_level: 'INFO',
  providers: ['one', 'two'].map((id) => ({ id, label: id, provider: 'openai', base_url: 'https://example.test', model: `model-${id}`, context_window: 16000, max_output_tokens: 4000, enabled: true, supports_json_schema: false, has_api_key: true })),
};
let root: Root;
let host: HTMLDivElement;
let qc: QueryClient;
const close = vi.fn();
const flush = async () => { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 35)); }); };
const button = (label: string) => [...host.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.replace(/\s+/g, '') === label.replace(/\s+/g, ''))!;
const edit = async (index: number) => { act(() => [...host.querySelectorAll<HTMLButtonElement>('button')].filter((b) => b.textContent?.trim() === '编辑供应商')[index].click()); await flush(); };
const input = (id: string) => host.querySelector<HTMLInputElement>(`#${id}`)!;
const change = (id: string, value: string) => act(() => { const el = input(id); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); });
const render = (open: boolean) => act(() => root.render(<QueryClientProvider client={qc}><AISettingsDrawer open={open} onClose={close} /></QueryClientProvider>));
const advanced = () => { const node = [...host.querySelectorAll('summary')].find((n) => n.textContent?.includes('多供应商')); if (node) act(() => node.click()); };
beforeEach(async () => {
  vi.clearAllMocks();
  window.matchMedia = vi.fn().mockReturnValue({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() });
  const original = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((el) => original(el));
  api.getSettings.mockResolvedValue(structuredClone(settings));
  api.updateSettings.mockResolvedValue(structuredClone(settings));
  api.testProviderConnection.mockResolvedValue({ ok: true, message: '连接成功' });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  render(true); await flush(); advanced();
});
afterEach(() => { act(() => root.unmount()); qc.clear(); host.remove(); vi.restoreAllMocks(); });

describe('AI settings draft lifecycle', () => {
  it('tests an unchanged saved provider when its older profile omits vision capability', async () => {
    act(() => button('测试连接').click()); await flush();
    expect(api.testProviderConnection).toHaveBeenCalledWith({ provider_id: 'one' });
  });

  it('keeps all provider drafts and permission choice across switches and saves once', async () => {
    change('model', 'edited-one'); change('api_key', 'test-draft-key');
    act(() => host.querySelector<HTMLButtonElement>('#chat_auto_approve_writes')!.click());
    await edit(1); change('model', 'edited-two'); await edit(0);
    expect(input('model').value).toBe('edited-one');
    expect(input('api_key').value).toBe('test-draft-key');
    expect(host.querySelector('#chat_auto_approve_writes')!.getAttribute('aria-checked')).toBe('true');
    expect(button('应用到 Provider 列表')).toBeUndefined();
    act(() => button('保存').click()); await flush();
    expect(api.updateSettings).toHaveBeenCalledTimes(1);
    expect(api.updateSettings.mock.calls[0][0]).toMatchObject({ chat_auto_approve_writes: true, fallback_provider_ids: ['two'], providers: [{ id: 'one', model: 'edited-one', api_key: 'test-draft-key' }, { id: 'two', model: 'edited-two' }] });
    expect(api.updateSettings.mock.calls[0][0].providers[1]).not.toHaveProperty('api_key');
  });
  it('tests edited drafts without saving, even after switching away and back', async () => {
    change('model', 'unsaved'); await edit(1); await edit(0);
    act(() => button('测试连接').click()); await flush();
    expect(api.testProviderConnection).toHaveBeenCalledWith({ provider: expect.objectContaining({ id: 'one', model: 'unsaved' }) });
    expect(api.updateSettings).not.toHaveBeenCalled();
  });
  it('does not overwrite drafts when settings refetch and discards them on cancel/reopen', async () => {
    change('model', 'keep-draft');
    act(() => qc.setQueryData(['settings'], { ...structuredClone(settings), version: 'refetched' })); await flush();
    expect(input('model').value).toBe('keep-draft');
    act(() => button('返回设置').click()); expect(close).toHaveBeenCalledTimes(1);
    render(false); render(true); await flush();
    expect(input('model').value).toBe('model-one');
    expect(api.updateSettings).not.toHaveBeenCalled();
  });
  it('retains incomplete fields and new-provider drafts without inventing values', async () => {
    change('model', ''); change('base_url', '');
    act(() => button('新增供应商').click()); await flush();
    change('label', 'Local draft'); change('api_key', 'unsaved-key');
    await edit(0);
    expect(input('model').value).toBe('');
    expect(input('base_url').value).toBe('');
    await edit(2);
    expect(input('label').value).toBe('Local draft');
    expect(input('api_key').value).toBe('unsaved-key');
    expect(api.updateSettings).not.toHaveBeenCalled();
  });
  it('ignores a late connection result for a different selected provider', async () => {
    let finish!: (result: { ok: boolean; message: string }) => void;
    api.testProviderConnection.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    act(() => button('测试连接').click()); await flush();
    await edit(1);
    await act(async () => finish({ ok: true, message: 'OLD_PROVIDER_RESULT' }));
    await flush();
    expect(host.textContent).not.toContain('OLD_PROVIDER_RESULT');
  });
  it('tests an unsaved JSON Schema capability instead of the saved profile', async () => {
    act(() => host.querySelector<HTMLButtonElement>('#supports_json_schema')!.click());
    act(() => button('测试连接').click()); await flush();
    expect(api.testProviderConnection).toHaveBeenCalledWith({ provider: expect.objectContaining({ id: 'one', supports_json_schema: true }) });
  });
  it('assigns distinct identities to repeated new-provider clicks', async () => {
    act(() => { button('新增供应商').click(); button('新增供应商').click(); });
    await flush();
    const rows = [...host.querySelectorAll('[data-testid="ai-provider-list"] .ant-list-item')];
    expect(rows).toHaveLength(4);
    await edit(2); const first = input('id').value;
    await edit(3); expect(input('id').value).not.toBe(first);
  });
  it('ignores a failed connection after the editor has closed', async () => {
    let fail!: (error: Error) => void;
    api.testProviderConnection.mockReturnValue(new Promise((_resolve, reject) => { fail = reject; }));
    act(() => button('测试连接').click()); await flush();
    render(false);
    await act(async () => fail(new Error('late failure'))); await flush();
    expect(document.body.textContent).not.toContain('连接失败');
  });
  it('keeps draft values after a failed save so the user can retry', async () => {
    api.updateSettings.mockRejectedValue(new Error('offline'));
    change('model', 'retry-draft');
    act(() => button('保存').click()); await flush();
    expect(close).not.toHaveBeenCalled();
    expect(input('model').value).toBe('retry-draft');
    api.updateSettings.mockResolvedValue(settings);
    act(() => button('保存').click()); await flush();
    expect(api.updateSettings.mock.calls[1][0].providers[0].model).toBe('retry-draft');
  });
  it('blocks saving invalid budgets on a non-current edited provider', async () => {
    change('context_window', '3000'); await edit(1);
    act(() => button('保存').click()); await flush();
    expect(api.updateSettings).not.toHaveBeenCalled();
  });
  it('invalidates vision capability in the retained draft when the model changes', async () => {
    act(() => host.querySelector<HTMLButtonElement>('#supports_vision')!.click());
    await edit(1); await edit(0);
    expect(host.querySelector('#supports_vision')!.getAttribute('aria-checked')).toBe('true');
    change('model', 'different-vision-model');
    await edit(1); await edit(0);
    expect(host.querySelector('#supports_vision')!.getAttribute('aria-checked')).toBe('false');
    act(() => button('保存').click()); await flush();
    expect(api.updateSettings.mock.calls[0][0].providers[0].supports_vision).toBe(false);
  });
  it('ignores a late failed image test after switching providers', async () => {
    let fail!: (error: Error) => void;
    imageApi.testProviderImage.mockReturnValue(new Promise((_resolve, reject) => { fail = reject; }));
    act(() => button('测试图片识别（会产生费用）').click()); await flush();
    await edit(1);
    await act(async () => fail(new Error('late image failure'))); await flush();
    expect(host.textContent).not.toContain('图片测试失败');
  });

});
