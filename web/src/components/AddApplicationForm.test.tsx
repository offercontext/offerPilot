// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AddApplicationForm from './AddApplicationForm';
import { checkApplicationDuplicates, createApplicationWithJd, getApplicationCreationScope } from '@/services/applications';
import { loadPendingCreations, savePendingCreation } from '@/services/applicationCreationRecovery';
import type { ApplicationCreationInput, ApplicationCreationResult } from '@/types/application';

vi.mock('@/services/applications', () => ({
  checkApplicationDuplicates: vi.fn(), createApplicationWithJd: vi.fn(), getApplicationCreationScope: vi.fn(),
}));
const request: ApplicationCreationInput = {
  company_name: '公司 A', position_name: '工程师', status: 'pending', job_url: '', notes: '', closed_reason: '',
  idempotency_key: 'original-request-0001', initial_jd: null,
};
const result = { ...request, id: 7, jd_version_id: null } as unknown as ApplicationCreationResult;
let root: Root;
let client: QueryClient;
const created = vi.fn();
const closed = vi.fn();
const nativeComputedStyle = window.getComputedStyle.bind(window);
async function flush() { await act(async () => { await new Promise((r) => setTimeout(r, 30)); }); }
async function render(open = true) {
  await act(async () => root.render(<QueryClientProvider client={client}><AddApplicationForm open={open} onClose={closed} onCreated={created} /></QueryClientProvider>));
  await flush();
}
function button(label: string) {
  const found = [...document.querySelectorAll('button')].find((b) => b.textContent?.replace(/\s/g, '') === label.replace(/\s/g, ''));
  if (!found) throw new Error(`Missing button ${label}: ${document.body.textContent}`);
  return found;
}
async function click(label: string) { await act(async () => button(label).click()); await flush(); }
function input(id: string, value: string) {
  const element = document.getElementById(id) as HTMLInputElement;
  if (!element) throw new Error(`Missing field ${id}`);
  act(() => {
    Object.getOwnPropertyDescriptor(element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function select(id: string, label: string) {
  await act(async () => document.getElementById(id)!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
  await flush();
  const option = [...document.querySelectorAll<HTMLElement>('.ant-select-item-option')].find((o) => o.textContent === label);
  if (!option) throw new Error(`Missing option ${label}`);
  await act(async () => option.click()); await flush();
}
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); document.body.replaceChildren();
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => nativeComputedStyle(element));
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
  vi.mocked(getApplicationCreationScope).mockResolvedValue('workspace-a');
  vi.mocked(checkApplicationDuplicates).mockResolvedValue({ items: [], has_more: false });
  vi.mocked(createApplicationWithJd).mockResolvedValue(result);
  root = createRoot(document.body.appendChild(document.createElement('div')));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => { act(() => root.unmount()); client.clear(); vi.restoreAllMocks(); });

describe('single Application intake', () => {
  it('draft, review and cancel never write business data', async () => {
    await render(); input('company_name', '公司 A'); input('position_name', '工程师');
    await click('核对并检查重复');
    expect(document.body.textContent).toContain('核对后确认保存');
    expect(createApplicationWithJd).not.toHaveBeenCalled();
    expect(loadPendingCreations('workspace-a')).toEqual([]);
    await click('返回修改'); await click('取消'); expect(closed).toHaveBeenCalled();
  });
  it('requires nonblank names', async () => {
    await render(); input('company_name', '  '); input('position_name', '工程师');
    await click('核对并检查重复');
    expect(checkApplicationDuplicates).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('请输入公司名称');
  });
  it('preserves raw JD and independent URLs, writes once and navigates with result', async () => {
    await render(); input('company_name', '公司 A'); input('position_name', '工程师'); input('job_url', 'https://job.invalid/a?q=1');
    await select('jd_mode', '现在填写／粘贴 JD');
    input('jd_text', '  15–25K·14薪\n200元/天 ✨\n'); input('source_url', 'https://source.invalid/b');
    await click('核对并检查重复');
    await act(async () => { button('确认保存').click(); button('确认保存').click(); }); await flush();
    expect(createApplicationWithJd).toHaveBeenCalledTimes(1);
    expect(vi.mocked(createApplicationWithJd).mock.calls[0][0]).toMatchObject({ status: 'pending', job_url: 'https://job.invalid/a?q=1', initial_jd: { jd_text: '  15–25K·14薪\n200元/天 ✨\n', source_url: 'https://source.invalid/b' } });
    expect(created).toHaveBeenCalledWith(result);
    expect(client.getQueryData(['applications'])).toEqual([result]);
    expect(loadPendingCreations('workspace-a')).toEqual([]);
  });
  it('duplicate check failure requires explicit continue', async () => {
    vi.mocked(checkApplicationDuplicates).mockRejectedValue(new Error('offline'));
    await render(); input('company_name', '公司 A'); input('position_name', '工程师'); await click('核对并检查重复');
    expect(document.body.textContent).toContain('未能完成检查');
    expect(document.body.textContent).not.toContain('未发现符合规则');
    expect(createApplicationWithJd).not.toHaveBeenCalled(); await click('仍然创建');
    expect(createApplicationWithJd).toHaveBeenCalledTimes(1);
  });
  it('opens a duplicate even when the Applications cache did not contain it', async () => {
    vi.mocked(checkApplicationDuplicates).mockResolvedValue({ items: [{ ...result, match_reason: 'exact_name' }], has_more: false });
    client.setQueryData(['applications'], []);
    await render(); input('company_name', '公司 A'); input('position_name', '工程师'); await click('核对并检查重复');
    await click('打开已有记录');
    expect(created).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));
    expect(client.getQueryData(['applications'])).toEqual([expect.objectContaining({ id: 7 })]);
    expect(createApplicationWithJd).not.toHaveBeenCalled();
  });
  it('restores original request and key after timeout and remount', async () => {
    vi.mocked(createApplicationWithJd).mockRejectedValueOnce(new Error('timeout'));
    await render(); input('company_name', '公司 A'); input('position_name', '工程师'); await click('核对并检查重复'); await click('确认保存');
    const original = vi.mocked(createApplicationWithJd).mock.calls[0][0];
    expect(document.body.textContent).toContain('创建结果未知');
    act(() => root.unmount()); root = createRoot(document.body.appendChild(document.createElement('div')));
    await render(); expect(document.body.textContent).toContain('有一条待恢复提交');
    expect(document.getElementById('company_name')).toBeNull();
    await click('查询／恢复结果');
    expect(vi.mocked(createApplicationWithJd).mock.calls[1][0]).toEqual(original);
    expect(created).toHaveBeenCalledWith(result);
  });
  it('does not read or replay another workspace pending request', async () => {
    savePendingCreation('workspace-b', { request, status: 'unknown' });
    await render(); expect(document.body.textContent).not.toContain('有一条待恢复提交');
    expect(createApplicationWithJd).not.toHaveBeenCalled();
    expect(loadPendingCreations('workspace-b')).toHaveLength(1);
  });
  it('only copies job link into source after explicit click', async () => {
    await render(); input('job_url', 'https://job.invalid/a'); await select('jd_mode', '现在填写／粘贴 JD');
    expect((document.getElementById('source_url') as HTMLInputElement).value).toBe('');
    await click('使用岗位链接作为本版来源'); input('job_url', 'https://job.invalid/b');
    expect((document.getElementById('source_url') as HTMLInputElement).value).toBe('https://job.invalid/a');
  });
  it('closing during submission does not later reset a reopened draft or navigate', async () => {
    let complete!: (value: ApplicationCreationResult) => void;
    vi.mocked(createApplicationWithJd).mockReturnValue(new Promise((resolve) => { complete = resolve; }));
    await render(); input('company_name', '公司 A'); input('position_name', '工程师');
    await click('核对并检查重复'); await click('确认保存');
    await act(async () => (document.querySelector('.ant-modal-close') as HTMLButtonElement).click());
    await render(false);
    await act(async () => complete(result)); await flush();
    expect(created).not.toHaveBeenCalled();
    expect(loadPendingCreations('workspace-a')).toEqual([]);
  });
  it('keeps recovery if browser storage cannot be written', async () => {
    await render(); input('company_name', '公司 A'); input('position_name', '工程师'); await click('核对并检查重复');
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    await click('确认保存');
    expect(createApplicationWithJd).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('暂未发送请求');
    spy.mockRestore();
  });
  it('does not bypass a submission created in another tab after review', async () => {
    await render(); input('company_name', '公司 B'); input('position_name', '工程师'); await click('核对并检查重复');
    savePendingCreation('workspace-a', { request, status: 'unknown' });
    await click('确认保存');
    expect(createApplicationWithJd).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('公司 A');
    expect(loadPendingCreations('workspace-a')).toHaveLength(1);
  });
  it('can recover one of multiple unknown attempts without blocking each other', async () => {
    savePendingCreation('workspace-a', { request, status: 'unknown' });
    savePendingCreation('workspace-a', { request: { ...request, idempotency_key: 'second-request-0001' }, status: 'unknown' });
    await render(); await click('查询／恢复结果');
    expect(createApplicationWithJd).toHaveBeenCalledWith(request);
    expect(loadPendingCreations('workspace-a')).toHaveLength(1);
  });
});
