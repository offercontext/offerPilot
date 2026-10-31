// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { setLocale, t } from '@/i18n';
import { localizedModal } from '@/i18n/modal';
import { companionLifeExpression } from './copy';
import { companionMirror } from './model';
import CompanionLifeStatus from './CompanionLifeStatus';
import CompanionLifeSettings from './CompanionLifeSettings';
import { CompanionLifeContext, type CompanionLifeController } from './useCompanionLife';
import type { CompanionSnapshot } from './contracts';
vi.mock('@/services/persona', () => ({ getPersona: async () => ({ spec: { display_name: 'Haru' } }) }));
vi.mock('@/i18n/modal', () => ({ localizedModal: vi.fn() }));
let root: Root, host: HTMLDivElement, life: CompanionLifeController;
const state: CompanionSnapshot = { identity: 'one', revision: 4, persona_version: 'companion-v1', settings: { enabled: true, proactivity: 'default', hide_status: false }, state: { interests: [], activity: { id: 'activity', template_id: 'paper_boat', title: '折一只纸小船', status: 'active', step_index: 0, step_count: 3, step_title: '挑一张想象中的彩纸', participants: ['companion'], promise: null, started_at: 1, updated_at: 1 }, wish: { id: 'wish', activity_id: 'activity', text: '一起给纸小船起个名字' } }, recent_events: [], shared_participation: [], presence: { session_id: 'session', session_quiet: false, active: true, blocked: false, blockers: [] } };
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setLocale('zh-CN');
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }) });
  life = { snapshot: structuredClone(state), signals: { visible: true, typing: false, work_running: false, awaiting_approval: false, mock_interview: false, quiet: false, failure: false }, busy: false, uncertain: false, error: '', quiet: false, bid: null, event: vi.fn(), updateSettings: vi.fn(), reset: vi.fn(), retry: vi.fn(), setQuiet: vi.fn(), setMockInterview: vi.fn(), dismissBid: vi.fn() } as unknown as CompanionLifeController;
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.clearAllMocks(); setLocale('zh-CN'); });
async function render() { await act(async () => root.render(<CompanionLifeContext.Provider value={life}><CompanionLifeStatus inline /><CompanionLifeSettings /></CompanionLifeContext.Provider>)); }
async function click(name: string) {
  const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(node => (node.getAttribute('aria-label') || node.textContent || '').replace(/\s/g, '') === name.replace(/\s/g, ''));
  expect(button, `button: ${name}`).toBeTruthy(); await act(async () => { button!.click(); });
}
it('opens, pauses, resumes and closes without stopping or abandoning activity', async () => {
  await render(); await click('查看 Haru 的虚拟日常'); await click('暂停日常'); expect(life.event).toHaveBeenLastCalledWith('pause', true);
  life.snapshot!.state.activity!.status = 'paused'; await render(); await click('关闭日常详情');
  expect(vi.mocked(life.event).mock.calls.some(call => call[0] === 'abandon')).toBe(false);
  await click('查看 Haru 的虚拟日常'); await click('继续日常'); expect(life.event).toHaveBeenLastCalledWith('resume', true);
});
it('keeps a shared step waiting until explicit completion and supports decline', async () => {
  await render(); await click('查看 Haru 的虚拟日常'); await click('这次先不了'); expect(life.event).toHaveBeenLastCalledWith('decline_wish', true);
  await click('一起做这件小事'); expect(life.event).toHaveBeenLastCalledWith('join', true);
  life.snapshot!.state.activity!.status = 'waiting_shared'; life.snapshot!.state.wish = null; await render();
  expect(document.body.textContent).toContain('不倒计时，也不会催促');
  await click('关闭日常详情'); expect(vi.mocked(life.event).mock.calls.some(call => call[0] === 'complete_shared')).toBe(false);
  await click('查看 Haru 的虚拟日常'); await click('约好的一步完成了'); expect(life.event).toHaveBeenLastCalledWith('complete_shared', true);
});
it('withdraws only a live shared step through the explicit control, including while paused', async () => {
  await render(); await click('查看 Haru 的虚拟日常');
  expect(document.body.textContent).not.toContain('这步请 Haru 自己做');
  life.snapshot!.state.activity!.status = 'waiting_shared';
  life.snapshot!.state.wish = null; await render();
  expect(document.body.textContent).toContain('不会清除之前的共同记录');
  await click('这步请 Haru 自己做');
  expect(life.event).toHaveBeenLastCalledWith('withdraw_shared', true);
  expect(vi.mocked(life.event).mock.calls.some(call => ['abandon', 'complete_shared', 'resume'].includes(call[0]))).toBe(false);
  life.snapshot!.state.activity!.status = 'paused';
  life.snapshot!.state.activity!.paused_from = 'waiting_shared'; await render();
  await click('这步请 Haru 自己做');
  expect(life.event).toHaveBeenLastCalledWith('withdraw_shared', true);
  life.snapshot!.state.activity!.paused_from = 'active'; await render();
  expect(document.body.textContent).not.toContain('这步请 Haru 自己做');
});
it('keeps withdrawal reachable in settings when hidden and quiet, and disables uncertain actions', async () => {
  life.snapshot!.settings.hide_status = true; life.signals.quiet = true; life.quiet = true;
  life.snapshot!.state.activity!.status = 'waiting_shared'; life.snapshot!.state.wish = null;
  await render(); await click('查看日常活动');
  life.uncertain = true; await render();
  const withdrawal = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent?.replace(/\s/g, '') === '这步请Haru自己做');
  expect(withdrawal?.disabled).toBe(true);
  life.uncertain = false; await render(); await click('这步请 Haru 自己做');
  expect(life.event).toHaveBeenLastCalledWith('withdraw_shared', true);
  await act(async () => { await setLocale('en-US'); }); await render();
  expect(document.body.textContent).toContain('Let Haru do this step');
  expect(document.body.textContent).toContain('Earlier shared records are kept');
});
it('suspends and closes the popover while typing, without reopening it afterward', async () => {
  await render(); await click('查看 Haru 的虚拟日常');
  life = { ...life, signals: { ...life.signals, typing: true } }; await render(); expect(host.querySelector('[data-companion-life]')).toBeNull();
  life = { ...life, signals: { ...life.signals, typing: false } }; await render();
  expect(host.querySelector('[aria-label="查看 Haru 的虚拟日常"]')?.getAttribute('aria-expanded')).toBe('false');
});
it('uses a localized destructive confirmation and never resets just for opening it', async () => {
  await render(); await click('重置虚拟日常'); expect(life.reset).not.toHaveBeenCalled();
  expect(localizedModal).toHaveBeenCalledWith('confirm', expect.any(Function));
  const factory = vi.mocked(localizedModal).mock.calls[0][1]; const options = factory();
  expect(options.content).toContain('无法撤销'); expect(options.content).toContain('不会删除对话、求职数据或已确认记忆');
  await act(async () => { options.onOk?.(); }); expect(life.reset).toHaveBeenCalledTimes(1);
});
it('keeps explicit main-window controls reachable when the native owner status is hidden or quiet', async () => {
  life.snapshot!.settings.hide_status = true; life.signals.quiet = true; life.quiet = true;
  await render(); expect(host.querySelector('[data-companion-life]')).toBeNull();
  await click('查看日常活动'); await click('暂停日常');
  expect(life.event).toHaveBeenLastCalledWith('pause', true);
  await click('关闭日常详情');
  expect(vi.mocked(life.event).mock.calls.some(call => call[0] === 'abandon')).toBe(false);
});
it('uses English authored life labels and hides the status when disabled', async () => {
  await setLocale('en-US'); await render(); await click("View Haru's virtual daily life");
  expect(document.body.textContent).toContain('Fold a little paper boat'); expect(document.body.textContent).not.toMatch(/\p{Script=Han}/u);
  life.snapshot!.settings.enabled = false; await render(); expect(host.querySelector('[data-companion-life]')).toBeNull();
});

it('shows the persisted chat promise across refresh without treating opening as completion', async () => {
  life.snapshot!.state.activity!.status = 'waiting_shared';
  life.snapshot!.state.activity!.promise = '等你一起选颜色';
  await render(); await click('查看 Haru 的虚拟日常');
  expect(document.body.textContent).toContain('等你一起选颜色');
  expect(document.body.textContent).toContain('在聊天里回应');
  expect(vi.mocked(life.event).mock.calls.some(call => call[0] === 'complete_shared')).toBe(false);
  await click('关闭日常详情'); await click('查看 Haru 的虚拟日常');
  expect(document.body.textContent).toContain('等你一起选颜色');
  await setLocale('en-US'); await render();
  expect(document.body.textContent).toContain('Waiting to choose a color with you');
});

it('shares the wish invitation with the owner mirror without repeating it in static status or details', async () => {
  life.bid = { id: 'bid', kind: 'companion_life', result_text: 'Backend prose must not replace committed facts', source_event_type: 'activity_completed', source_identity: 'one', source_revision: 4 };
  for (const locale of ['zh-CN', 'en-US'] as const) {
    life.bid.source_revision = life.snapshot!.revision;
    await act(async () => { await setLocale(locale); }); await render();
    const expected = companionLifeExpression(life.snapshot!.state.activity, t);
    const invitation = companionLifeExpression(life.snapshot!.state.activity, t, life.snapshot!.state.wish);
    expect(host.querySelector('[data-companion-life] [role="status"] span')?.textContent).toBe(invitation);
    expect(companionMirror(life.snapshot, life.signals, life.bid)?.detail).toBe(invitation);
    expect(host.querySelector('[data-companion-life] button[aria-expanded]')?.textContent).toContain(expected);
    expect(host.querySelector('[data-companion-life] button[aria-expanded]')?.textContent).not.toContain(invitation);
    expect(companionMirror(life.snapshot, life.signals)?.detail).toBe(expected);
    expect(host.textContent).not.toContain(life.bid.result_text);
    await click(t('查看 Haru 的虚拟日常'));
    expect(document.querySelector('section[aria-label="' + t('Haru 的虚拟日常') + '"]')?.textContent).toContain(expected);
    await click(t('关闭日常详情'));
    const refreshed = structuredClone(life.snapshot!);
    refreshed.revision += 1; refreshed.settings.proactivity = 'less';
    life.snapshot = refreshed; await render();
    expect(host.querySelector('[data-companion-life] [role="status"] span')?.textContent).toBe(expected);
  }
});

it('does not revive a declined wish in a later bid or invite from a paused activity', async () => {
  life.bid = { id: 'bid', kind: 'companion_life', result_text: '', source_identity: 'one', source_revision: 4 };
  await render();
  expect(host.querySelector('[data-companion-life] [role="status"] span')?.textContent).toMatch(/小心愿/);
  await click('查看 Haru 的虚拟日常'); await click('这次先不了');
  expect(life.event).toHaveBeenLastCalledWith('decline_wish', true);
  life.snapshot!.state.wish = null; life.snapshot!.revision += 1;
  life.bid = { ...life.bid, id: 'later-bid', source_revision: life.snapshot!.revision };
  await render();
  const plain = companionLifeExpression(life.snapshot!.state.activity, t);
  expect(host.querySelector('[data-companion-life] [role="status"] span')?.textContent).toBe(plain);
  life.snapshot!.state.wish = structuredClone(state.state.wish);
  life.snapshot!.state.activity!.status = 'paused'; await render();
  expect(host.querySelector('[data-companion-life] [role="status"] span')?.textContent).toBe(companionLifeExpression(life.snapshot!.state.activity, t));
  expect(host.querySelector('[data-companion-life] [role="status"] span')?.textContent).not.toMatch(/小心愿/);
});


it.each(['active', 'paused', 'waiting_shared', 'completed', 'abandoned'] as const)('localizes every English life status including %s', async status => {
  life.snapshot!.state.activity!.status = status;
  life.snapshot!.state.wish = null;
  if (status === 'paused') life.snapshot!.state.activity!.paused_from = 'active';
  await setLocale('en-US'); await render(); await click("View Haru's virtual daily life");
  expect(document.body.textContent).not.toMatch(/\p{Script=Han}/u);
});
