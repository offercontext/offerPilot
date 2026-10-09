// @vitest-environment jsdom
import { act, StrictMode, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobMailStatus } from '@/types/jobMail';
import { MailSecureSetupError, type JobMailSecureCapability } from '@/types/jobMailSecureSetup';
const secure = vi.hoisted(() => ({ getJobMailSecureCapability: vi.fn(), startJobMailSecureSetup: vi.fn(), testJobMailSecureSetup: vi.fn(), saveJobMailSecureSetup: vi.fn(), cancelJobMailSecureSetup: vi.fn(), disconnectRealJobMail: vi.fn() }));
const mail = vi.hoisted(() => ({ getJobMailStatus: vi.fn(), disconnectJobMail: vi.fn(), syncJobMail: vi.fn() }));
vi.mock('@/services/jobMailSecureSetup', () => ({ ...secure, JOB_MAIL_SECURE_CAPABILITY_KEY: ['job-mail', 'secure-setup', 'capability'] }));
vi.mock('@/services/jobMail', () => ({ ...mail, JOB_MAIL_QUERY_KEY: ['job-mail'], JOB_MAIL_STATUS_KEY: ['job-mail', 'status'], cancelJobMailSync: vi.fn(), connectSyntheticJobMail: vi.fn(), updateJobMailSettings: vi.fn(), importJobMail: vi.fn() }));
vi.mock('antd', () => ({
  Alert: ({ message, description, action }: { message: ReactNode; description?: ReactNode; action?: ReactNode }) => <div role="status">{message}{description}{action}</div>,
  Button: ({ children, onClick, disabled, loading }: { children?: ReactNode; onClick?: () => void; disabled?: boolean; loading?: boolean }) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
  Modal: ({ open, title, children, onCancel, closable = true }: { open?: boolean; title?: ReactNode; children?: ReactNode; onCancel?: () => void; closable?: boolean }) => open ? <section role="dialog">{title}{closable && <button onClick={onCancel}>关闭窗口</button>}{children}</section> : null,
  Skeleton: () => <div>加载中</div>,
  Switch: ({ checked, onChange, disabled }: { checked: boolean; onChange: (value: boolean) => void; disabled: boolean }) => <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} />,
}));
const { default: JobMailSecureSetup } = await import('./JobMailSecureSetup');
const { default: JobMailSettings } = await import('./JobMailSettings');
const fixtureCode = 'synthetic-unit-test-code';
const token = 'synthetic-session-token';
const expires = '2030-01-01T00:00:00Z';
const capabilityBase: JobMailSecureCapability = { available: true, reason: '', backend: 'Native test vault', local_only: true, credential_input_allowed: true, configured: false, deletion_pending: false };
const statusBase: JobMailStatus = { capabilities: { real_connection: true, ai_recognition: false, synthetic_connection: false }, connection: null, run: null, budget: { limit: 100, used: 0, remaining: 100 }, execution_location: '本机', available_folders: [] };
let capability: JobMailSecureCapability; let status: JobMailStatus;
let host: HTMLDivElement; let root: Root; let client: QueryClient;
const closed = vi.fn();
function button(text: string) { const found = [...host.querySelectorAll('button')].find((node) => node.textContent === text); if (!found) throw new Error(`Missing button ${text}`); return found; }
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); }); }
async function render(node: ReactNode) { await act(async () => root.render(<StrictMode><QueryClientProvider client={client}>{node}</QueryClientProvider></StrictMode>)); await settle(); await settle(); }
async function click(node: HTMLElement) { await act(async () => node.click()); await settle(); }
async function type(selector: string, value: string) { const input = host.querySelector<HTMLInputElement>(selector)!; await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }); }
function consent(text: string) { return [...host.querySelectorAll('label')].find((label) => label.textContent?.includes(text))!.querySelector<HTMLInputElement>('input[type="checkbox"]')!; }
async function start() { await click(consent('开始一个短期安全配置会话')); await click(button('开始安全配置')); }
async function fill() { await type('[aria-label="QQ 邮箱地址"]', 'fixture@qq.com'); await type('[aria-label="QQ 邮箱授权码"]', fixtureCode); await click(consent('我同意现在使用上述授权码')); }
async function tested() { await start(); await fill(); await click(button('确认仅验证登录与目录')); }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: unknown) => void; const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; }); return { promise, resolve, reject }; }
function assertNoSecret() { expect(host.textContent).not.toContain(fixtureCode); expect(host.innerHTML).not.toContain(fixtureCode); expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(fixtureCode); }
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear(); capability = structuredClone(capabilityBase); status = structuredClone(statusBase);
  secure.getJobMailSecureCapability.mockImplementation(async () => structuredClone(capability));
  secure.startJobMailSecureSetup.mockResolvedValue({ setup_token: token, expires_at: expires });
  secure.testJobMailSecureSetup.mockResolvedValue({ setup_token: token, expires_at: expires, email_masked: 'fi***@qq.com', folders: [{ id: 'inbox-id', name: '收件箱', selectable: true, excluded_by_default: false, special_use: [] }, { id: 'trash-id', name: '垃圾箱', selectable: true, excluded_by_default: true, special_use: ['\\Trash'] }] });
  secure.saveJobMailSecureSetup.mockImplementation(async () => { capability.configured = true; return { ...status, connection: { id: 'qq-id', provider: 'qq', status: 'connected', sync_mode: 'manual' } }; });
  secure.cancelJobMailSecureSetup.mockResolvedValue(undefined);
  secure.disconnectRealJobMail.mockResolvedValue({ ...status, connection: null });
  mail.getJobMailStatus.mockImplementation(async () => structuredClone(status));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); });

describe('user-owned secure credential handoff', () => {
  it.each(['unavailable', 'configured', 'deletion_pending'] as const)('does not render a password field for %s capability', async (condition) => {
    if (condition === 'unavailable') { capability.available = false; capability.reason = 'secure_store_unavailable'; }
    else if (condition === 'configured') capability.configured = true;
    else capability.deletion_pending = true;
    await render(<JobMailSecureSetup onClose={closed} />);
    expect(host.querySelector('input[type="password"]')).toBeNull(); expect(secure.startJobMailSecureSetup).not.toHaveBeenCalled();
  });
  it('rejects empty code and requires separate test consent; input is never persisted or echoed', async () => {
    await render(<JobMailSecureSetup onClose={closed} />); expect(secure.startJobMailSecureSetup).not.toHaveBeenCalled(); await start();
    await type('[aria-label="QQ 邮箱地址"]', 'fixture@qq.com'); await click(consent('我同意现在使用上述授权码')); expect(button('确认仅验证登录与目录').disabled).toBe(true);
    await type('[aria-label="QQ 邮箱授权码"]', fixtureCode); expect(button('确认仅验证登录与目录').disabled).toBe(true); assertNoSecret();
    const store = vi.spyOn(Storage.prototype, 'setItem'); await click(consent('我同意现在使用上述授权码')); await click(button('确认仅验证登录与目录'));
    expect(secure.testJobMailSecureSetup).toHaveBeenCalledWith({ setup_token: token, email: 'fixture@qq.com', authorization_code: fixtureCode, explicit_test_consent: true }, expect.any(AbortSignal));
    expect(store).not.toHaveBeenCalled(); assertNoSecret(); expect(host.querySelector('input[type="password"]')).toBeNull(); expect(secure.saveJobMailSecureSetup).not.toHaveBeenCalled();
  });
  it('clears a submitted code immediately, suppresses double test clicks, and cancels on close', async () => {
    const pending = deferred<unknown>(); secure.testJobMailSecureSetup.mockReturnValue(pending.promise);
    await render(<JobMailSecureSetup onClose={closed} />); await start(); await fill(); const input = host.querySelector<HTMLInputElement>('input[type="password"]')!; const test = button('确认仅验证登录与目录');
    await act(async () => { test.click(); test.click(); }); expect(secure.testJobMailSecureSetup).toHaveBeenCalledTimes(1); expect(input.value).toBe('');
    await click(button('取消并清空输入')); expect(secure.cancelJobMailSecureSetup).toHaveBeenCalledWith(token); expect(closed).toHaveBeenCalledTimes(1);
    await render(null); await act(async () => pending.resolve({ setup_token: token, expires_at: expires, folders: [] })); await settle(); expect(host.querySelector('[role="dialog"]')).toBeNull(); assertNoSecret();
  });
  it('requires explicit folder choices and new save consent after scope changes', async () => {
    await render(<JobMailSecureSetup onClose={closed} />); await tested();
    expect(host.querySelectorAll('input[type="checkbox"]:checked').length).toBe(0); expect(button('确认保存凭据与范围').disabled).toBe(true);
    await click(consent('收件箱')); await click(consent('我明确同意把此授权码保存')); expect(button('确认保存凭据与范围').disabled).toBe(false);
    await click(consent('垃圾箱')); expect(host.textContent).toContain('你选择了默认排除的目录'); expect(button('确认保存凭据与范围').disabled).toBe(true);
    await click(consent('垃圾箱')); await click(consent('我明确同意把此授权码保存')); await click(button('确认保存凭据与范围'));
    expect(secure.saveJobMailSecureSetup).toHaveBeenCalledWith({ setup_token: token, folder_ids: ['inbox-id'], backfill_days: 0, explicit_save_consent: true }, expect.any(AbortSignal));
    expect(host.textContent).toContain('当前为手动模式'); assertNoSecret();
  });
  it('does not resend secrets after a verification failure', async () => {
    secure.testJobMailSecureSetup.mockRejectedValue(new MailSecureSetupError('secure_test_failed'));
    await render(<JobMailSecureSetup onClose={closed} />); await tested();
    expect(host.textContent).toContain('QQ 登录或目录验证未成功'); expect(host.querySelector('input[type="password"]')).toBeNull();
    expect(secure.testJobMailSecureSetup).toHaveBeenCalledTimes(1); expect(secure.cancelJobMailSecureSetup).toHaveBeenCalledWith(token); assertNoSecret();
  });
  it('expires the in-memory session and cannot save with the old token', async () => {
    await render(<JobMailSecureSetup onClose={closed} />); await tested();
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse(expires) + 1);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1050)); });
    expect(host.textContent).toContain('安全配置会话已过期'); expect(secure.cancelJobMailSecureSetup).toHaveBeenCalledWith(token); expect(secure.saveJobMailSecureSetup).not.toHaveBeenCalled();
    expect(host.querySelector('input[type="password"]')).toBeNull();
  });
  it('cancels a late start result after the setup was dismissed', async () => {
    const pending = deferred<{ setup_token: string; expires_at: string }>(); secure.startJobMailSecureSetup.mockReturnValue(pending.promise);
    await render(<JobMailSecureSetup onClose={closed} />); await click(consent('开始一个短期安全配置会话')); await click(button('开始安全配置')); await render(null);
    await act(async () => pending.resolve({ setup_token: token, expires_at: expires })); await settle(); expect(secure.cancelJobMailSecureSetup).toHaveBeenCalledWith(token); expect(host.querySelector('input[type="password"]')).toBeNull();
  });
});

describe('real credential deletion confirmation', () => {
  beforeEach(() => {
    capability.configured = true;
    status.connection = { id: 'qq-id', provider: 'qq', email_masked: 'fi***@qq.com', status: 'connected', folders: ['INBOX'], scope_version: 1, sync_mode: 'manual', interval_minutes: 15, ai_enabled: false, start_at: '2026-10-09T00:00:00Z', next_run_at: null, last_attempt_at: null, last_success_at: null, not_before_at: null };
  });
  it('shows stored but inactive QQ configuration and blocks reading without hiding disconnect', async () => {
    status.capabilities.real_connection = false;
    await render(<JobMailSettings />);
    expect(host.textContent).toContain('已保存邮箱配置，但本实例当前不会读取邮件');
    expect(button('立即同步').disabled).toBe(true); expect(button('调整读取范围').disabled).toBe(true);
    expect((host.querySelector('[aria-label="同步间隔"]') as HTMLInputElement).disabled).toBe(true);
    expect((host.querySelector('input[type="checkbox"]') as HTMLInputElement).disabled).toBe(true);
    expect(button('保存同步方式').disabled).toBe(true);
    expect(button('断开邮箱').disabled).toBe(false);
    await click(button('立即同步')); expect(mail.syncJobMail).not.toHaveBeenCalled();
  });
  it('does not present a cached inactive result as current after status refresh fails', async () => {
    status.capabilities.real_connection = false; await render(<JobMailSettings />);
    mail.getJobMailStatus.mockRejectedValue(new Error('offline'));
    await act(async () => { await client.invalidateQueries({ queryKey: ['job-mail', 'status'] }); }); await settle();
    expect(host.textContent).toContain('状态待核对');
    expect(host.textContent).not.toContain('本实例当前不会读取邮件');
  });
  it('requires explicit deletion consent and keeps failed deletion retryable without false success', async () => {
    secure.disconnectRealJobMail.mockImplementation(async () => { capability.deletion_pending = true; status.connection!.status = 'disconnected'; throw new MailSecureSetupError('credential_delete_pending'); });
    await render(<JobMailSettings />); await click(button('断开邮箱')); expect(button('确认断开并删除凭据').disabled).toBe(true); expect(secure.startJobMailSecureSetup).not.toHaveBeenCalled();
    await click(consent('我明确同意停止邮箱检查')); await click(button('确认断开并删除凭据'));
    expect(secure.startJobMailSecureSetup).toHaveBeenCalledWith('disconnect'); expect(secure.disconnectRealJobMail).toHaveBeenCalledWith(token);
    expect(host.textContent).toContain('本机凭据仍待删除'); expect(host.textContent).not.toContain('本机凭据删除已确认'); expect(mail.disconnectJobMail).not.toHaveBeenCalled();
  });
  it('only reports successful deletion after a fresh capability read verifies absence', async () => {
    secure.disconnectRealJobMail.mockImplementation(async () => { capability.configured = false; capability.deletion_pending = false; status.connection!.status = 'disconnected'; return status; });
    await render(<JobMailSettings />); await click(button('断开邮箱')); await click(consent('我明确同意停止邮箱检查')); await click(button('确认断开并删除凭据'));
    expect(host.textContent).toContain('本机凭据删除已确认'); expect(host.textContent).toContain('已确认业务记录保留');
  });
  it('still requests stop and deletion when the native vault is unavailable', async () => {
    capability.available = false; capability.credential_input_allowed = false; capability.reason = 'secure_store_unavailable';
    secure.disconnectRealJobMail.mockImplementation(async () => { capability.deletion_pending = true; status.connection!.status = 'credential_delete_pending'; throw new MailSecureSetupError('credential_delete_pending'); });
    await render(<JobMailSettings />); await click(button('断开邮箱')); await click(consent('我明确同意停止邮箱检查'));
    expect(button('确认断开并删除凭据').disabled).toBe(false);
    await click(button('确认断开并删除凭据'));
    expect(secure.startJobMailSecureSetup).toHaveBeenCalledWith('disconnect'); expect(secure.disconnectRealJobMail).toHaveBeenCalledWith(token);
    expect(host.textContent).toContain('本机凭据仍待删除'); expect(host.textContent).not.toContain('本机凭据删除已确认');
  });
});
