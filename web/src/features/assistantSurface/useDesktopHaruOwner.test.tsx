// @vitest-environment jsdom
import { act } from 'react';
import { initializeLocale, setLocale, t, type Locale } from '@/i18n';
import { createRoot, type Root } from 'react-dom/client';
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { writePilotMascotAnimationLevel, PILOT_MASCOT_ANIMATION_CHANGED, PILOT_MASCOT_ANIMATION_KEY } from '@/features/pilotMascot/pilotMascotPreference';
import { desktopHaruSnapshot, useDesktopHaruOwner } from './useDesktopHaruOwner';
import { DESKTOP_HARU_SEND_FAILURE, type DesktopHaruCommand, type DesktopHaruSnapshot } from './desktopHaru';
import type { PilotConversationController } from './usePilotConversationController';
import type { ChatStartRequest, Conversation } from '@/types/chat';
import { buildPilotPageContext } from '@/lib/pilotPageContext';
import type { Application } from '@/types/application';
import type { Offer } from '@/types/offer';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
let command: (value: DesktopHaruCommand) => void;
let showPilot: () => void;
let theme: 'light' | 'dark';
vi.mock('@/theme/ThemeContext', () => ({ useThemeMode: () => ({ mode: theme }) }));
let controller: PilotConversationController;
const publish = vi.fn();
const reply = vi.fn();
const openPending = vi.fn();
const windowAction = vi.fn().mockResolvedValue(true);
const offShowPilot = vi.fn();
const localeListeners = new Set<(value: unknown) => void>();
let nativeLocale: Locale = 'zh-CN';
function localeChanged(value: Locale) { nativeLocale = value; localeListeners.forEach(listener => listener(value)); }
// Preserve the native event source for the module's one initialization, while
// resetting its authoritative preference before each independent test.
const localeBridge = {
  getLocale: vi.fn(async () => nativeLocale),
  onLocale: vi.fn((listener: (value: unknown) => void) => { localeListeners.add(listener); return () => { localeListeners.delete(listener); }; }),
  setLocale: vi.fn(async (value: Locale) => { localeChanged(value); return value; }),
};
const applicationDraft: ChatStartRequest = {
  requestKey: 1, context_type: 'application', context_ref: '8',
  context_label: '星河科技 · 前端工程师', mode: 'general',
};
const activeConversation: Conversation = {
  id: 7, title: 'Existing chat', context_type: 'application', context_ref: '7',
  context_label: '原会话公司 · 后端工程师', created_at: '', updated_at: '',
};
function Harness() { useDesktopHaruOwner(controller, openPending); return null; }
function render() { act(() => root.render(<Harness />)); }
function latest(): DesktopHaruSnapshot { return publish.mock.calls[publish.mock.calls.length - 1][0]; }
beforeEach(async () => {
  vi.clearAllMocks();
  localStorage.removeItem(PILOT_MASCOT_ANIMATION_KEY);
  theme = 'light';
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  window.offerpilotDesktop = { role: 'owner', publish, reply, onShowPilot: handler => { showPilot = handler; return offShowPilot; }, onCommand: handler => { command = handler; return vi.fn(); }, disconnect: vi.fn(), windowAction, getState: vi.fn(), onState: vi.fn() };
  window.offerpilotLocale = localeBridge;
  await initializeLocale();
  localeChanged('zh-CN');
  controller = {
    conversations: [], turns: [], taskState: 'idle', hasKey: true, loading: false, pending: null,
    attachments: [], followingContext: { view: 'applications-list', label: '同名公司', entity: { kind: 'application', id: 1, label: '同名公司' } },
    activeRequestRef: { current: null }, activeConversationSelectionRef: { current: null }, activePendingRef: { current: null },
    executionControl: { canStop: false, stopping: false },
    isActionOwnerReady: vi.fn(() => true), sendMessage: vi.fn().mockResolvedValue('ignored'), stopActiveRequest: vi.fn(), setLastError: vi.fn(), setComposerDraft: vi.fn(),
  } as unknown as PilotConversationController;
});
afterEach(() => { act(() => root.unmount()); host.remove(); localeChanged('zh-CN'); delete window.offerpilotDesktop; delete window.offerpilotLocale; });
afterAll(() => localeListeners.clear());

describe('desktop Haru owner', () => {
  it('publishes the application theme without invalidating an in-flight command version', () => {
    render();
    const version = latest().version;
    expect(latest().theme).toBe('light');
    theme = 'dark'; render();
    expect(latest().theme).toBe('dark');
    expect(latest().version).toBe(version);
  });
  it('routes a native show-main request to the existing Pilot workspace without requesting or approving a turn', () => {
    render();
    act(() => showPilot());
    expect(openPending).toHaveBeenCalledOnce();
    expect(controller.sendMessage).not.toHaveBeenCalled();
    expect(controller.stopActiveRequest).not.toHaveBeenCalled();
    expect(reply).not.toHaveBeenCalled();
    expect(windowAction).not.toHaveBeenCalled();
    act(() => root.render(null));
    expect(offShowPilot).toHaveBeenCalledOnce();
  });
  it('publishes an application draft label before a conversation exists without a provider', () => {
    controller = { ...controller, hasKey: false, draftContext: applicationDraft };
    render();
    expect(latest()).toMatchObject({ conversationId: null, contextLabel: applicationDraft.context_label, aiConfigured: false, canSend: false });
    expect(controller.sendMessage).not.toHaveBeenCalled();
  });
  it.each([true, false])('keeps the frozen request label ahead of draft/conversation context (entity: %s)', withEntity => {
    controller = {
      ...controller, conversationId: 7, conversations: [activeConversation], draftContext: applicationDraft,
      requestContextSnapshot: {
        view: 'applications-list', label: '发送时页面',
        ...(withEntity ? { entity: { kind: 'application' as const, id: '9', label: '发送时投递' } } : {}),
      },
    };
    expect(desktopHaruSnapshot(controller).contextLabel).toBe(withEntity ? '发送时投递' : '发送时页面');
    controller = { ...controller, conversationId: undefined };
    expect(desktopHaruSnapshot(controller).contextLabel).toBe(withEntity ? '发送时投递' : '发送时页面');
  });
  it.each([
    { conversation: activeConversation, expected: activeConversation.context_label },
    { conversation: { ...activeConversation, context_label: undefined }, expected: '投递 #7' },
    { conversation: undefined, expected: '同名公司' },
  ])('does not let a stale draft replace an existing conversation context ($expected)', ({ conversation, expected }) => {
    controller = { ...controller, conversationId: 7, conversations: conversation ? [conversation] : [], draftContext: applicationDraft };
    expect(desktopHaruSnapshot(controller).contextLabel).toBe(expected);
  });
  it('bounds the draft label and publishes none of its body, action, or attachments', () => {
    controller = {
      ...controller,
      draftContext: {
        ...applicationDraft, context_label: '投'.repeat(450),
        composerDraft: 'PRIVATE_COMPOSER', initialMessage: 'PRIVATE_BODY',
        attachments: [{ kind: 'resume', id: 'PRIVATE_ATTACHMENT_ID', label: 'PRIVATE_ATTACHMENT_LABEL' }],
        pilot_action: { type: 'application_jd_save', jdText: 'PRIVATE_ACTION' },
      },
      pending: { confirmation_token: 'PRIVATE_CREDENTIAL', args: { provider: 'PRIVATE_PROVIDER' } } as never,
    };
    render();
    expect(latest().contextLabel).toBe('投'.repeat(400));
    expect(JSON.stringify(latest())).not.toContain('PRIVATE_');
    expect(latest()).not.toHaveProperty('draftContext');
    expect(latest()).not.toHaveProperty('attachments');
  });
  it.each(['requestKey', 'context_ref'] as const)('rejects commands from a previous draft %s even when the label is unchanged', field => {
    controller = { ...controller, draftContext: applicationDraft };
    render();
    const previous = latest();
    controller = {
      ...controller,
      draftContext: { ...applicationDraft, ...(field === 'requestKey' ? { requestKey: 2 } : { context_ref: '9' }) },
    };
    render();
    expect(latest().contextLabel).toBe(previous.contextLabel);
    expect(latest().version).toBeGreaterThan(previous.version);
    for (const [index, action] of (['send', 'stop', 'open-pending'] as const).entries()) {
      command({ id: index + 1, version: previous.version, action, text: 'old draft command' });
      expect(reply).toHaveBeenLastCalledWith(index + 1, { ok: false, reason: 'stale' });
    }
    expect(controller.sendMessage).not.toHaveBeenCalled();
    expect(controller.stopActiveRequest).not.toHaveBeenCalled();
    expect(openPending).not.toHaveBeenCalled();
  });
  it('rejects a command if the draft changed before its next render', () => {
    controller = { ...controller, draftContext: applicationDraft };
    render();
    const version = latest().version;
    controller.draftContext = { ...applicationDraft, requestKey: 2 };
    command({ id: 1, version, action: 'send', text: 'old draft command' });
    expect(reply).toHaveBeenLastCalledWith(1, { ok: false, reason: 'stale' });
    expect(controller.sendMessage).not.toHaveBeenCalled();
  });
  it('whitelists view fields and omits pending credentials/config/provider data', () => {
    controller.pending = { confirmation_token: 'SENSITIVE', args: { secret: 'SENSITIVE' } } as never;
    controller.turns = [{ role: 'assistant', content: 'Visible answer', action: { token: 'SENSITIVE' } as never }];
    const value = desktopHaruSnapshot(controller);
    expect(value.hasPending).toBe(true);
    expect(value.canSend).toBe(false);
    expect(JSON.stringify(value)).not.toContain('SENSITIVE');
    expect(Object.keys(value)).not.toContain('pending');
  });
  it('context/attachments/draft changes advance and publish revision even with identical visible labels', () => {
    render();
    const old = latest().version;
    controller = { ...controller, followingContext: { ...controller.followingContext!, entity: { kind: 'application', id: '2', label: '同名公司' } } };
    render();
    expect(latest().version).toBeGreaterThan(old);
    command({ id: 1, version: old, action: 'send', text: 'change this' });
    expect(reply).toHaveBeenLastCalledWith(1, { ok: false, reason: 'stale' });
    expect(controller.sendMessage).not.toHaveBeenCalled();
    const contextVersion = latest().version;
    controller = { ...controller, attachments: [{ kind: 'resume', id: '3', label: 'same' }] };
    render(); expect(latest().version).toBeGreaterThan(contextVersion);
    const attachmentVersion = latest().version;
    controller = { ...controller, draftContext: { requestKey: 1, context_type: 'application', context_ref: 8 } as never };
    render(); expect(latest().version).toBeGreaterThan(attachmentVersion);
  });
  it('streaming prose does not invalidate Stop, while changing execution does', () => {
    controller = { ...controller, loading: true, taskState: 'running', executionControl: { canStop: true, stopping: false, execution: { turn_id: 'A', conversation_id: 7, execution_generation: 1, state: 'running' } } as never };
    render(); const version = latest().version;
    controller = { ...controller, turns: [{ role: 'assistant', content: 'stream delta' }] };
    render(); expect(latest().version).toBe(version);
    command({ id: 1, version, action: 'stop' });
    expect(controller.stopActiveRequest).toHaveBeenCalledOnce();
    controller = { ...controller, executionControl: { ...controller.executionControl, execution: { turn_id: 'B', conversation_id: 7, execution_generation: 2, state: 'running' } } };
    render(); expect(latest().version).toBeGreaterThan(version);
    command({ id: 2, version, action: 'stop' });
    expect(controller.stopActiveRequest).toHaveBeenCalledOnce();
  });
  it('rejects a selection or new request ref immediately, before another React render', () => {
    render(); const version = latest().version;
    controller.activeConversationSelectionRef.current = 4;
    command({ id: 1, version, action: 'send', text: 'hello' });
    expect(reply).toHaveBeenLastCalledWith(1, { ok: false, reason: 'stale' });
    expect(controller.sendMessage).not.toHaveBeenCalled();
    controller.activeConversationSelectionRef.current = null;
    controller.activeRequestRef.current = { kind: 'chat' } as never;
    command({ id: 2, version, action: 'send', text: 'hello' });
    expect(controller.sendMessage).not.toHaveBeenCalled();
  });
  it('never acknowledges ignored send as admitted or drops it silently', async () => {
    render();
    await act(async () => command({ id: 1, version: latest().version, action: 'send', text: 'hello' }));
    expect(controller.sendMessage).toHaveBeenCalledOnce();
    expect(reply).toHaveBeenLastCalledWith(1, { ok: false, reason: 'busy' });
    expect(controller.setComposerDraft).toHaveBeenCalledWith(expect.any(Function));
    expect(vi.mocked(controller.setComposerDraft).mock.calls[0][0] instanceof Function).toBe(true);
  });
  it('admits exactly one request, acknowledges before stream ends and ignores duplicated command ids', async () => {
    let complete!: (value: 'sent') => void;
    controller.sendMessage = vi.fn(() => {
      controller.activeRequestRef.current = { kind: 'chat' } as never;
      return new Promise<'sent'>(resolve => { complete = resolve; });
    });
    render(); const version = latest().version;
    act(() => { command({ id: 1, version, action: 'send', text: 'hello' }); command({ id: 1, version, action: 'send', text: 'hello' }); command({ id: 2, version, action: 'send', text: 'hello' }); });
    expect(controller.sendMessage).toHaveBeenCalledOnce();
    expect(reply).toHaveBeenCalledWith(1, { ok: true });
    await act(async () => complete('sent'));
  });
  it.each(['ignored', 'failed', 'sent'] as const)('preserves an existing owner draft after Haru outcome %s', async outcome => {
    controller = { ...controller, composerDraft: 'unfinished main-window draft', sendMessage: vi.fn().mockResolvedValue(outcome) };
    render();
    await act(async () => command({ id: 1, version: latest().version, action: 'send', text: 'separate Haru message' }));
    expect(controller.setComposerDraft).not.toHaveBeenCalled();
  });
  it('pending action opens main workspace without approving, and disconnects on unmount', () => {
    controller.pending = { confirmation_token: 'SECRET' } as never;
    render(); command({ id: 1, version: latest().version, action: 'open-pending' });
    expect(openPending).toHaveBeenCalledOnce();
    expect(windowAction).toHaveBeenCalledWith('show-main');
    expect(controller.sendMessage).not.toHaveBeenCalled();
    act(() => root.render(null));
    expect(window.offerpilotDesktop!.disconnect).toHaveBeenCalledOnce();
  });
});

it('shares only the animation enum and updates it without revoking the conversation command version', () => {
  render();
  expect(latest().animationLevel).toBe('full');
  const version = latest().version;
  for (const level of ['off', 'minimal', 'full'] as const) {
    act(() => writePilotMascotAnimationLevel(level));
    expect(latest().animationLevel).toBe(level);
    expect(latest().version).toBe(version);
    expect(latest().canSend).toBe(true);
  }
  expect(controller.sendMessage).not.toHaveBeenCalled();
});

it('honors an explicit session preference when storage fails and rejects non-enum event payloads', () => {
  render();
  const storage = { setItem: vi.fn(() => { throw new Error('storage blocked'); }) };
  act(() => writePilotMascotAnimationLevel('off', storage));
  expect(latest().animationLevel).toBe('off');
  for (const detail of [null, { level: 'full' }, 'invalid', true]) {
    act(() => window.dispatchEvent(new CustomEvent(PILOT_MASCOT_ANIMATION_CHANGED, { detail })));
    expect(latest().animationLevel).toBe('off');
  }
});

it('republishes localized generated labels without changing command versions or translating custom content', async () => {
  controller = { ...controller, followingContext: undefined, conversationId: 7, conversations: [{ ...activeConversation, context_label: undefined }], turns: [{ role: 'assistant', content: '工作台' }] };
  render(); const version = latest().version;
  expect(latest().contextLabel).toBe('投递 #7');
  await act(async () => { await setLocale('en-US'); });
  expect(latest().contextLabel).toBe('Application #7'); expect(latest().version).toBe(version);
  expect(localeBridge.setLocale).toHaveBeenCalledWith('en-US');
  expect(latest().messages[0].content).toBe('工作台');
  expect(controller.sendMessage).not.toHaveBeenCalled(); expect(controller.stopActiveRequest).not.toHaveBeenCalled();
  controller = { ...controller, conversations: [{ ...activeConversation, context_label: '工作台' }] }; render();
  expect(latest().contextLabel).toBe('工作台');
  controller = { ...controller, conversations: [] }; render(); expect(latest().contextLabel).toBe('Workspace');
  controller = { ...controller, conversationId: undefined }; render(); expect(latest().contextLabel).toBe('Current page');
  await act(async () => { await setLocale('zh-CN'); }); expect(latest().contextLabel).toBe('当前页面');
  expect(controller.setComposerDraft).not.toHaveBeenCalled();
});

it('tags only its own rejected-send failure and clears provenance for another error', async () => {
  await setLocale('en-US');
  controller.sendMessage = vi.fn().mockRejectedValue(Error('private provider details'));
  controller.setLastError = vi.fn(value => { controller = { ...controller, lastError: value as string }; });
  render(); const version = latest().version;
  await act(async () => command({ id: 1, version, action: 'send', text: 'hello' }));
  render();
  expect(controller.setLastError).toHaveBeenCalledWith(DESKTOP_HARU_SEND_FAILURE);
  expect(latest()).toMatchObject({ error: DESKTOP_HARU_SEND_FAILURE, errorKey: 'desktop_send_failed', version });
  expect(JSON.stringify(latest())).not.toContain('private provider details');
  await act(async () => { await setLocale('zh-CN'); });
  expect(latest().errorKey).toBe('desktop_send_failed'); expect(latest().version).toBe(version);
  controller = { ...controller, lastError: '检查更新' }; render();
  expect(latest().error).toBe('检查更新'); expect(latest()).not.toHaveProperty('errorKey');
  controller = { ...controller, lastError: DESKTOP_HARU_SEND_FAILURE }; render();
  expect(latest()).not.toHaveProperty('errorKey');
});

it.each(['dashboard', 'application', 'offer'] as const)('keeps the control version across real %s page-context translations and retains Stop', async kind => {
  const application = { id: 17, company_name: '今日', position_name: '自定义岗位', status: 'interview' } as Application;
  const offer = { id: 19, company_name: 'Today', position_name: 'Custom role', status: 'negotiating' } as Offer;
  const build = () => buildPilotPageContext({ view: kind === 'dashboard' ? 'dashboard' : kind === 'application' ? 'applications-list' : 'offers',
    selectedApplication: kind === 'application' ? application : undefined, coachedOffer: kind === 'offer' ? offer : undefined })!;
  const chinese = build();
  controller = { ...controller, followingContext: chinese, pinnedContext: chinese, requestContextSnapshot: chinese,
    loading: true, taskState: 'running', executionControl: { ...controller.executionControl, canStop: true } };
  render(); const version = latest().version;
  await act(async () => { await setLocale('en-US'); });
  const english = build();
  expect(english).not.toEqual(chinese);
  controller = { ...controller, followingContext: english }; render();
  expect(latest().version).toBe(version);
  // Frozen request/pinned snapshots can retain the previous display language.
  act(() => command({ id: 1, version, action: 'stop' }));
  expect(controller.stopActiveRequest).toHaveBeenCalledOnce(); expect(reply).toHaveBeenLastCalledWith(1, { ok: true });
  controller = { ...controller, pinnedContext: english, requestContextSnapshot: english }; render();
  expect(latest().version).toBe(version);
  await act(async () => { await setLocale('zh-CN'); });
  controller = { ...controller, followingContext: build() }; render();
  expect(latest().version).toBe(version);
  expect(controller.sendMessage).not.toHaveBeenCalled();
});

it('retains real identity/status/filter/custom text changes in the canonical control signature', async () => {
  const application = { id: 17, company_name: 'Company', position_name: 'Role', status: 'interview' } as Application;
  const build = () => buildPilotPageContext({ view: 'applications-list', selectedApplication: application })!;
  controller = { ...controller, followingContext: build() }; render();
  let version = latest().version;
  for (const patch of [
    { ...build(), entity: { ...build().entity!, id: '18' } },
    { ...build(), entity: { ...build().entity!, label: '面试' } },
    { ...build(), entity: { ...build().entity!, description: '用户自定义说明' } },
    { ...build(), label: '自定义页面名称' },
    { ...build(), filters: [{ key: 'status', label: '自定义筛选', value: 'interview' }] },
    { ...build(), filters: [{ key: 'status', label: '自定义筛选', value: 'offer' }] },
    { ...build(), filters: [{ key: 'status', label: '更改筛选名称', value: 'offer' }] },
  ]) {
    controller = { ...controller, followingContext: patch }; render();
    expect(latest().version).toBeGreaterThan(version); version = latest().version;
  }
  controller = { ...controller, followingContext: build() }; render(); version = latest().version;
  application.status = 'offer'; controller = { ...controller, followingContext: build() }; render();
  expect(latest().version).toBeGreaterThan(version);
  const custom = { ...build(), label: 'My custom context', entity: { ...build().entity!, label: '今日', description: 'Current status: user-authored note' } };
  controller = { ...controller, followingContext: custom }; render(); version = latest().version;
  await act(async () => { await setLocale('en-US'); });
  expect(latest().version).toBe(version); expect(controller.followingContext).toEqual(custom);
});


it.each(['view label', 'entity label', 'entity description'] as const)('retains custom %s changes even when text equals authored translations', async field => {
  // Deliberately hand-built, not registered by buildPilotPageContext. Matching
  // catalog text must never grant user context authored-display provenance.
  const chinese = { view: 'dashboard' as const, label: '今日', entity: {
    kind: 'application' as const, id: '17', label: '今日',
    description: t('当前状态：{value0}', { value0: t('待投递') }),
  } };
  controller = { ...controller, followingContext: chinese }; render();
  const version = latest().version;
  await act(async () => { await setLocale('en-US'); });
  expect(latest().version).toBe(version);
  const english = { ...chinese, entity: { ...chinese.entity } };
  if (field === 'view label') english.label = t('今日');
  if (field === 'entity label') english.entity.label = t('今日');
  if (field === 'entity description') english.entity.description = t('当前状态：{value0}', { value0: t('待投递') });
  expect(english).not.toEqual(chinese);
  controller = { ...controller, followingContext: english }; render();
  expect(latest().version).toBeGreaterThan(version);
  command({ id: 1, version, action: 'send', text: 'stale custom context' });
  expect(reply).toHaveBeenLastCalledWith(1, { ok: false, reason: 'stale' });
  expect(controller.sendMessage).not.toHaveBeenCalled();
});

it('invalidates old commands when a proven context is mutated before another render', () => {
  const context = buildPilotPageContext({ view: 'dashboard' })!;
  controller = { ...controller, followingContext: context }; render();
  const version = latest().version;
  context.label = 'Today';
  command({ id: 1, version, action: 'send', text: 'old context' });
  expect(reply).toHaveBeenLastCalledWith(1, { ok: false, reason: 'stale' });
  expect(controller.sendMessage).not.toHaveBeenCalled();
  render(); expect(latest().version).toBeGreaterThan(version);
});
