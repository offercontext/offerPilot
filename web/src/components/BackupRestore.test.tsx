// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ getRestoreStatus: vi.fn(), previewRestore: vi.fn(), confirmRestore: vi.fn(), cancelRestore: vi.fn() }));
vi.mock('../services/backupRestore', () => mocks);
const { BackupRestore } = await import('./BackupRestore');
const preview = { state: 'validated', id: 'a'.repeat(32), sha256: 'b'.repeat(64), legacy: false, counts: { conversations: 2 } };
let container: HTMLDivElement;
let root: Root;
function button(text: string) {
  const result = [...container.querySelectorAll('button')].find((item) => item.textContent?.includes(text));
  if (!result) throw new Error(`button missing: ${text}`);
  return result;
}
async function click(element: Element) { await act(async () => { element.dispatchEvent(new MouseEvent('click', { bubbles: true })); }); }
async function consent() {
  await click(container.querySelector('input[type="checkbox"]')!);
  const input = container.querySelector('input[placeholder="REPLACE WORKSPACE"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'REPLACE WORKSPACE');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function render() { await act(async () => root.render(<BackupRestore />)); }
async function upload(file = new File(['synthetic'], 'backup.zip')) {
  const input = container.querySelector('input[type="file"]')!;
  await act(async () => {
    Object.defineProperty(input, 'files', { configurable: true, value: [file] });
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
beforeEach(() => {
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) });
  mocks.getRestoreStatus.mockResolvedValue({ state: 'none' });
  mocks.previewRestore.mockResolvedValue(preview);
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

describe('backup restore consent and interrupted requests', () => {
  it('requires acknowledgement and exact phrase, then sends one confirmation', async () => {
    await render(); await upload();
    expect(button('确认恢复并等待重启').disabled).toBe(true);
    expect(container.textContent).toContain('确认后至重启前的新工作也会被覆盖');
    await consent();
    expect(button('确认恢复并等待重启').disabled).toBe(false);
    let finish!: (value: unknown) => void;
    mocks.confirmRestore.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    await click(button('确认恢复并等待重启'));
    await click(button('确认恢复并等待重启'));
    expect(mocks.confirmRestore).toHaveBeenCalledTimes(1);
    expect(mocks.confirmRestore).toHaveBeenCalledWith(preview.id, preview.sha256, 'REPLACE WORKSPACE');
    await act(async () => finish({ ...preview, state: 'queued' }));
    expect(container.textContent).toContain('请完整退出并重启服务');
  });
  it('clears old consent when refresh discovers another tab preview', async () => {
    mocks.getRestoreStatus.mockResolvedValueOnce(preview).mockResolvedValueOnce({ ...preview, id: 'c'.repeat(32) });
    await render(); await consent();
    expect(button('确认恢复并等待重启').disabled).toBe(false);
    await click(button('刷新恢复状态'));
    expect(button('确认恢复并等待重启').disabled).toBe(true);
    expect((container.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(false);
    expect((container.querySelector('input[placeholder="REPLACE WORKSPACE"]') as HTMLInputElement).value).toBe('');
  });
  it('blocks retries after a lost response until status is refreshed', async () => {
    await render(); await upload(); await consent();
    mocks.confirmRestore.mockRejectedValue(new Error('lost response'));
    await click(button('确认恢复并等待重启'));
    expect(container.textContent).toContain('操作结果未确认');
    expect(button('确认恢复并等待重启').disabled).toBe(true);
    mocks.getRestoreStatus.mockResolvedValue({ ...preview, state: 'queued' });
    await click(button('刷新恢复状态'));
    expect(mocks.confirmRestore).toHaveBeenCalledTimes(1);
    mocks.cancelRestore.mockResolvedValue({ ...preview, state: 'cancelled' });
    await click(button('取消恢复'));
    expect(mocks.cancelRestore).toHaveBeenCalledWith(preview.id);
    expect(container.textContent).toContain('恢复已取消');
  });
  it('explains incompatible schemas and prevents oversize uploads', async () => {
    await render();
    const file = new File([], 'large.zip'); Object.defineProperty(file, 'size', { value: 256 * 1024 * 1024 + 1 });
    await upload(file);
    expect(mocks.previewRestore).not.toHaveBeenCalled();
    expect(container.textContent).toContain('不能超过 256 MiB');
    mocks.previewRestore.mockRejectedValue({ isAxiosError: true, response: { status: 409, data: { error: 'archive_schema_mismatch' } } });
    await upload();
    expect(container.textContent).toContain('备份版本不兼容');
  });
});
