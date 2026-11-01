// @vitest-environment jsdom
/** Integration supplement only: jsdom cannot prove pixel layout or native cross-tab behavior. */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntApp } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setLocale, useLocale, t } from '../../src/i18n';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { createFixtureApi, retainedUserBody, retainedAiBody, sampleMail } from './fixture-api.mjs';

const state = vi.hoisted(() => ({ api: null as any }));
vi.mock('@/services/http', () => ({
  createApiClient: ({ baseURL = '/api' } = {}) => Object.fromEntries(['get', 'post', 'put', 'patch', 'delete'].map((method) => [method, async (path: string, body?: unknown) => {
    const response = state.api.handle(method.toUpperCase(), baseURL + path, method === 'get' ? undefined : body);
    if (response.status >= 400) throw new Error(`Blocked mock request: ${method} ${baseURL + path}`);
    return { data: response.data, status: response.status };
  }])),
}));
const { default: App } = await import('../../src/App');
let root: Root; let client: QueryClient;
const nativeComputedStyle = window.getComputedStyle.bind(window);
function Harness() { const locale = useLocale(); return <QueryClientProvider client={client}><ThemeProvider><ConfigProvider locale={locale === 'en-US' ? enUS : zhCN}><AntApp><App /></AntApp></ConfigProvider></ThemeProvider></QueryClientProvider>; }
async function flush() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); }); }
async function waitForUi<T>(label: string, read: () => T | undefined): Promise<T> {
  try {
    return await vi.waitFor(async () => {
      // Lazy modules and query notifications must settle, not just a fixed number of timers.
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
      const value = read();
      expect(value, label).toBeDefined();
      return value!;
    }, { timeout: 2_000, interval: 20 });
  } catch (error) {
    const queries = client.getQueryCache().getAll().map((query) => ({
      key: query.queryKey, status: query.state.status, fetchStatus: query.state.fetchStatus,
    }));
    throw new Error(`${String(error)}\nURL: ${location.href}\nDOM: ${document.body.textContent}\nQueries: ${JSON.stringify(queries)}\nRequests: ${JSON.stringify(state.api.requests)}\nUnexpected: ${JSON.stringify(state.api.unexpected)}`);
  }
}
async function mount(view: string) { history.replaceState({}, '', `/?view=${view}`); await act(async () => root.render(<Harness />)); for (let i = 0; i < 4; i++) await flush(); }
function buttons(text: string) { return [...document.querySelectorAll<HTMLButtonElement>('button')].filter((b) => b.textContent?.replace(/\s+/g, '') === text.replace(/\s+/g, '') || b.getAttribute('aria-label') === text); }
async function click(text: string) { const b = buttons(text).find((b) => !b.disabled); if (!b) throw new Error(`No button: ${text}\n${document.body.textContent}`); await act(async () => b.click()); await flush(); }
function fill(selector: string, value: string) { const el = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector); if (!el) throw new Error(`Missing field ${selector}`); act(() => { Object.getOwnPropertyDescriptor(el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); }); }
async function switchLocale(locale: 'zh-CN' | 'en-US') { await act(async () => setLocale(locale)); await flush(); }
// Content-free session liveness is not a domain/model mutation. Keep all other writes guarded.
function writes() { return state.api.requests.filter((r: any) => r.method !== 'GET' && !(r.method === 'POST' && r.path === '/api/companion/presence')); }
function chineseUi() { const text = document.body.textContent ?? ''; return text.replaceAll('简体中文', '').replaceAll('用户原文保持不变 / user content stays unchanged', '').match(/[\u3400-\u9fff]+/g) ?? []; }

beforeEach(() => {
  state.api = createFixtureApi(); document.body.replaceChildren(); localStorage.clear(); setLocale('zh-CN');
  localStorage.setItem('offerpilot:pilot-mascot-visible', 'false');
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
  vi.spyOn(window, 'getComputedStyle').mockImplementation((el) => nativeComputedStyle(el));
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('fetch', async (url: string, input?: RequestInit) => {
    if (!String(url).startsWith('/api/')) throw new Error(`Blocked outbound request ${url}`);
    const result = state.api.handle(input?.method ?? 'GET', String(url), input?.body ? JSON.parse(String(input.body)) : undefined);
    return new Response(JSON.stringify(result.data), { status: result.status, headers: { 'Content-Type': 'application/json' } });
  });
  Element.prototype.scrollIntoView = vi.fn(); window.scrollTo = vi.fn();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  root = createRoot(document.body.appendChild(document.createElement('div')));
});
afterEach(() => { act(() => root.unmount()); client.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setLocale('zh-CN'); });

describe('real AppShell localization with strict synthetic API fixtures', () => {
  it('retains the normal backend authored mail status fields in the browser fixture', () => {
    expect(state.api.handle('GET', '/api/job-mail/status').data).toMatchObject({
      execution_location: '本地/自托管后端', recognition_mode: 'offline_rules',
      retention: { pending_days: 90, processed_days: 30 },
      notice: '识别默认离线规则；QQ连接由本机安全配置页管理，外部AI尚未开放。后端停止时不检查邮件。',
    });
  });
  it('models unchanged and edited mail previews with the backend proposal merge contract', () => {
    const preview = (edited_fields: Record<string, unknown>) => state.api.handle('POST', '/api/job-mail/suggestions/mail-fixture-1/preview', { operation_id: 'synthetic-operation', edited_fields }).data;
    const unchanged = preview({});
    expect(unchanged.after).toMatchObject(sampleMail.proposed_fields);
    expect(unchanged.after).toMatchObject({ application_id: 101, status: 'todo', subtype: '', remind_at: null });
    expect(unchanged.warnings).toEqual(['本次仅写入事件，不改变投递阶段，不发送邮件。']);
    expect(unchanged.changes).toContainEqual({ field: 'scheduled_at', before: null, after: sampleMail.proposed_fields.scheduled_at });
    expect(preview({ location: 'Edited meeting', notes: '' }).after).toMatchObject({ scheduled_at: sampleMail.proposed_fields.scheduled_at, location: 'Edited meeting', notes: '' });
    expect(state.api.unexpected).toEqual([]);
  });

  it.each(['settings', 'dashboard', 'board', 'applications-list', 'calendar', 'interview', 'questions', 'pilot'])('renders %s in both languages without new writes or untranslated authored Chinese', async (view) => {
    await mount(view); expect(document.body.textContent).toContain('OfferPilot');
    const count = writes().length; await switchLocale('en-US');
    expect(document.documentElement.lang).toBe('en-US'); expect(document.body.textContent).toContain('Settings');
    expect(writes()).toHaveLength(count); expect(state.api.unexpected).toEqual([]);
    expect(chineseUi(), `${view} authored Chinese remains: ${chineseUi().join(', ')}`).toEqual([]);
    await switchLocale('zh-CN'); expect(document.body.textContent).toContain('设置'); expect(writes()).toHaveLength(count);
  });


  it('keeps one grouped companion setting without a second job inbox, and retains it through AI settings', async () => {
    await mount('settings');
    const lifeSettings = await waitForUi('companion settings', () => document.querySelector<HTMLElement>('section[aria-labelledby="companion-life-settings-title"]') ?? undefined);
    expect(document.querySelectorAll('section[aria-labelledby="companion-life-settings-title"]')).toHaveLength(1);
    expect(lifeSettings.querySelector('[aria-label="开启虚拟日常"]')?.getAttribute('aria-checked')).toBe('false');
    expect(document.querySelector('[data-companion-life]')).toBeNull();
    expect(document.querySelector('section[aria-labelledby="proactive-settings-title"]')).not.toBeNull();
    expect(state.api.requests.filter((request: any) => request.path === '/api/proactive/jobs')).toEqual([]);
    expect(document.getElementById('settings-ai-privacy')!.compareDocumentPosition(lifeSettings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(lifeSettings.compareDocumentPosition(document.getElementById('settings-connections')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await click('配置 AI');
    await waitForUi('AI settings return action', () => buttons('返回设置')[0]);
    expect(document.querySelector('section[aria-labelledby="companion-life-settings-title"]')).toBe(lifeSettings);
    expect(lifeSettings.closest('section[aria-hidden="true"]')).not.toBeNull();
    await switchLocale('en-US');
    await click('Back to settings');
    expect(document.querySelector('section[aria-labelledby="companion-life-settings-title"]')).toBe(lifeSettings);
    expect(lifeSettings.closest('section[aria-hidden="true"]')).toBeNull();
    expect(chineseUi()).toEqual([]);
    expect(document.querySelector('[aria-label="Enable proactive tasks"]')).not.toBeNull();
    expect(writes()).toHaveLength(0);
    expect(state.api.unexpected).toEqual([]);
  });

  it('does not translate retained user and AI message bodies when the shared Pilot UI switches language', async () => {
    state.api = createFixtureApi({ retainedConversation: true }); await mount('pilot');
    const title = [...document.querySelectorAll<HTMLElement>('button,[role="button"]')].find((el) => el.textContent?.includes('Synthetic retained conversation'))!;
    expect(title).toBeDefined(); await act(async () => title.click()); for (let i = 0; i < 4; i++) await flush();
    expect(document.body.textContent).toContain(retainedUserBody); expect(document.body.textContent).toContain(retainedAiBody);
    const before = writes().length; await switchLocale('en-US'); expect(document.body.textContent).toContain(retainedUserBody); expect(document.body.textContent).toContain(retainedAiBody);
    await switchLocale('zh-CN'); expect(document.body.textContent).toContain(retainedUserBody); expect(document.body.textContent).toContain(retainedAiBody);
    expect(writes()).toHaveLength(before); expect(state.api.unexpected).toEqual([]);
  });
  it('keeps the interview practice surface selected while language changes, with no model or write request', async () => {
    await mount('interview'); await switchLocale('en-US');
    expect(document.querySelector('[data-testid="free-practice-workspace"]')).not.toBeNull();
    await click(t('快速模拟'));
    for (let i = 0; i < 4; i++) await flush();
    expect(document.querySelector('[data-testid="interview-practice-surface"]')).not.toBeNull();
    const before = writes().length; await switchLocale('zh-CN');
    expect(document.querySelector('[data-testid="interview-practice-surface"]')).not.toBeNull(); expect(writes()).toHaveLength(before);
    await switchLocale('en-US'); expect(chineseUi()).toEqual([]); expect(state.api.unexpected).toEqual([]);
  });
  it('preserves a generated email confirmation preview across language switches without confirming it', async () => {
    state.api.handle('POST', '/api/job-mail/imports', {});
    await mount('reminders'); await switchLocale('en-US');
    const suggestion = await waitForUi('generated email suggestion', () => [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.includes('Interview invitation')));
    expect(suggestion).toBeDefined(); await act(async () => suggestion.click()); await flush();
    await waitForUi('enabled email preview action', () => buttons('Preview final changes').find((button) => !button.disabled));
    await click('Preview final changes');
    await waitForUi('generated email confirmation preview', () => [...document.querySelectorAll('h3')].find((heading) => heading.textContent === 'Final confirmation summary'));
    expect(document.body.textContent).toContain('Final confirmation summary');
    const before = writes().length; await switchLocale('zh-CN'); expect(document.body.textContent).toContain('最终确认摘要');
    await switchLocale('en-US'); expect(document.body.textContent).toContain('Final confirmation summary'); expect(writes()).toHaveLength(before);
    expect(state.api.unexpected).toEqual([]);
  });
  it('retains live application form fields and validation through a locale switch, then Cancel closes without writing', async () => {
    await mount('board'); await click('添加投递'); await click('核对并检查重复'); expect(document.body.textContent).toContain('请输入公司名称');
    fill('#position_name', '保留岗位 draft'); const position = document.querySelector('#position_name');
    await switchLocale('en-US'); expect(document.querySelector('#position_name')).toBe(position); expect((position as HTMLInputElement).value).toBe('保留岗位 draft');
    expect(document.body.textContent).toContain('Enter a company name');
    fill('#company_name', 'Example Labs'); await click('Review and check duplicates');
    expect(document.body.textContent).toContain('Review and confirm save'); expect(writes()).toHaveLength(0);
    await click('Back to edit'); expect((document.querySelector('#position_name') as HTMLInputElement).value).toBe('保留岗位 draft');
    await click('Cancel'); expect(document.querySelector('.ant-modal-wrap')?.getAttribute('style')).toContain('display: none'); expect(writes()).toHaveLength(0);
  });
  it('retains open email import inputs and preview state through locale switch; Back and Cancel never import', async () => {
    await mount('settings'); await click('粘贴单封邮件');
    const modal = document.querySelector('.ant-modal-body')!; const fields = modal.querySelectorAll<HTMLInputElement>('input');
    for (const [i, value] of ['Interview invitation', 'qa@example.invalid', '2026-10-15T07:00:00Z'].entries()) { fields[i].id = `fixture-email-${i}`; fill(`#fixture-email-${i}`, value); }
    modal.querySelector('textarea')!.id = 'fixture-email-body'; fill('#fixture-email-body', '保留邮件原文，2026-10-22 10:00 UTC 面试。');
    await click('预览将提交的文本'); expect(document.body.textContent).toContain('将提交给当前 OfferPilot 后端的文本');
    await switchLocale('en-US'); expect(document.body.textContent).toContain('保留邮件原文，2026-10-22 10:00 UTC 面试。');
    expect(buttons('Submit text and generate suggestions')).toHaveLength(1); expect(writes()).toHaveLength(0);
    await click('Back to editing'); expect((document.querySelector('.ant-modal-body textarea') as HTMLTextAreaElement).value).toBe('保留邮件原文，2026-10-22 10:00 UTC 面试。');
    await click('Cancel'); expect(writes()).toHaveLength(0);
  });
});
