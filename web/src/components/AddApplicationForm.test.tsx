// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { message } from 'antd';
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
  expected_scope_id: 'workspace-a',
};
const result = { ...request, id: 7, jd_version_id: null } as unknown as ApplicationCreationResult;
let root: Root;
let host: HTMLDivElement;
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
  vi.resetAllMocks(); localStorage.clear();
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => nativeComputedStyle(element));
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
  vi.mocked(getApplicationCreationScope).mockResolvedValue('workspace-a');
  vi.mocked(checkApplicationDuplicates).mockResolvedValue({ items: [], has_more: false });
  vi.mocked(createApplicationWithJd).mockResolvedValue(result);
  host = document.body.appendChild(document.createElement('div'));
  root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(async () => {
  // Static messages own a separate React root and portal into document.body.
  // Let that owner remove its notices before removing only this test's host.
  // Clearing body directly leaves its delayed expiry holding detached nodes.
  try {
    await act(async () => { message.destroy(); root.unmount(); });
    expect(document.querySelector('.ant-message')).toBeNull();
  } finally {
    host.remove(); client.clear(); vi.restoreAllMocks();
  }
});

// Hold a real polyfill refresh across a clock switch, as a prior UI owner can.
// Its separate ESM controller keeps this fixture independent of AntD's CJS singleton.
async function pendingDisconnectedResizeFrame() {
  const requestFrame = globalThis.requestAnimationFrame;
  const cancelFrame = globalThis.cancelAnimationFrame;
  const frames: number[] = [];
  globalThis.requestAnimationFrame = (callback) => {
    const handle = requestFrame(callback);
    frames.push(handle);
    return handle;
  };
  let module: typeof import('resize-observer-polyfill');
  try {
    module = await vi.importActual<typeof import('resize-observer-polyfill')>(
      'resize-observer-polyfill/dist/ResizeObserver.es.js',
    );
  } finally {
    globalThis.requestAnimationFrame = requestFrame;
  }
  const onResize = vi.fn();
  const observer = new module.default(onResize);
  const schedule = vi.spyOn(globalThis, 'setTimeout')
    .mockImplementation(() => 0 as unknown as ReturnType<typeof setTimeout>);
  let registrations: typeof schedule.mock.calls;
  try {
    observer.observe(document.createElement('div'));
    registrations = schedule.mock.calls.slice();
  } finally {
    observer.disconnect();
    schedule.mockRestore();
  }
  expect(registrations).toHaveLength(1);
  expect(registrations[0][0]).toBeTypeOf('function');
  expect(registrations[0][1]).toBe(20);
  return {
    release: registrations[0][0] as () => void,
    frames,
    onResize,
    dispose: () => frames.forEach((handle) => cancelFrame(handle)),
  };
}

describe('single Application intake', () => {
  it.each(['cancel', 'external'])('reoffers a bypassed unknown submission after %s close and reopen', async (method) => {
    savePendingCreation('workspace-a', { request, status: 'unknown' });
    await render();
    await act(async () => (document.querySelector('input[type="checkbox"]') as HTMLInputElement).click());
    await flush();
    await click('承担重复风险，另建新草稿');
    if (method === 'cancel') await click('取消');
    await render(false);
    await render(true);
    expect(loadPendingCreations('workspace-a')).toHaveLength(1);
    expect(document.body.textContent).toContain('有一条待恢复提交');
    await click('查询／恢复结果');
    expect(createApplicationWithJd).toHaveBeenCalledWith(request);
    expect(loadPendingCreations('workspace-a')).toEqual([]);
  });
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
    act(() => root.unmount()); root = createRoot(host);
    await render(); expect(document.body.textContent).toContain('有一条待恢复提交');
    expect(document.getElementById('company_name')).toBeNull();
    await click('查询／恢复结果');
    expect(vi.mocked(createApplicationWithJd).mock.calls[1][0]).toEqual(original);
    expect(created).toHaveBeenCalledWith(result);
  });
  it('does not read or replay another workspace pending request', async () => {
    savePendingCreation('workspace-b', { request: { ...request, expected_scope_id: 'workspace-b' }, status: 'unknown' });
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
  it.each([false, true])('destroys real static message portals and their expiry timers (pending resize frame: %s)', async (withResizeFrame) => {
    const resizeFrame = withResizeFrame ? await pendingDisconnectedResizeFrame() : null;
    vi.useFakeTimers();
    const scheduleTimeout = vi.spyOn(globalThis, 'setTimeout');
    const cancelTimeout = vi.spyOn(globalThis, 'clearTimeout');
    const scheduleInterval = vi.spyOn(globalThis, 'setInterval');
    const cancelInterval = vi.spyOn(globalThis, 'clearInterval');
    const onClose = vi.fn();
    try {
      resizeFrame?.release();
      if (resizeFrame) {
        expect(resizeFrame.frames).toHaveLength(1);
        expect(scheduleInterval).toHaveBeenCalledTimes(1);
        expect(scheduleInterval.mock.results[0].type).toBe('return');
      }
      const frameInterval = scheduleInterval.mock.results[0]?.value;
      const firstRegistration = scheduleTimeout.mock.calls.length;
      await act(async () => {
        void message.success({
          key: 'owned-intake-cleanup-notice',
          content: 'Owned intake cleanup notice',
          duration: 60,
          onClose,
        });
      });
      expect(document.querySelector('.ant-message')?.textContent).toContain('Owned intake cleanup notice');
      expect(vi.getTimerCount()).toBeGreaterThan(0);
      // This open's exact expiry handle, not every prior owner's timer.
      const expiryRegistrations = scheduleTimeout.mock.calls.flatMap((args, index) =>
        index >= firstRegistration && args[1] === 60_000
          ? [scheduleTimeout.mock.results[index]]
          : [],
      );
      expect(expiryRegistrations).toHaveLength(1);
      expect(expiryRegistrations[0].type).toBe('return');
      const expiryHandle = expiryRegistrations[0].value;
      expect(cancelTimeout).not.toHaveBeenCalledWith(expiryHandle);
      expect(onClose).not.toHaveBeenCalled();
      await act(async () => { message.destroy(); });
      expect(document.querySelector('.ant-message')).toBeNull();
      expect(cancelTimeout).toHaveBeenCalledWith(expiryHandle);
      if (resizeFrame) expect(cancelInterval).not.toHaveBeenCalledWith(frameInterval);
      await act(async () => { await vi.runAllTimersAsync(); });
      expect(onClose).not.toHaveBeenCalled();
      expect(document.querySelector('.ant-message')).toBeNull();
      if (resizeFrame) {
        expect(cancelInterval).toHaveBeenCalledWith(frameInterval);
        expect(resizeFrame.onResize).not.toHaveBeenCalled();
      }
    } finally {
      // Cancel only this fixture's RAF on failure; normally it retires itself above.
      resizeFrame?.dispose();
      scheduleTimeout.mockRestore();
      cancelTimeout.mockRestore();
      scheduleInterval.mockRestore();
      cancelInterval.mockRestore();
      vi.useRealTimers();
    }
  });
});
