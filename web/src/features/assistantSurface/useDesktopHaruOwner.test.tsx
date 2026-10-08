// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { desktopHaruSnapshot, useDesktopHaruOwner } from './useDesktopHaruOwner';
import type { DesktopHaruCommand, DesktopHaruSnapshot } from './desktopHaru';
import type { PilotConversationController } from './usePilotConversationController';
import type { ChatStartRequest, Conversation } from '@/types/chat';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
let command: (value: DesktopHaruCommand) => void;
let controller: PilotConversationController;
const publish = vi.fn();
const reply = vi.fn();
const openPending = vi.fn();
const windowAction = vi.fn().mockResolvedValue(true);
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
beforeEach(() => {
  vi.clearAllMocks();
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  window.offerpilotDesktop = { role: 'owner', publish, reply, onCommand: handler => { command = handler; return vi.fn(); }, disconnect: vi.fn(), windowAction, getState: vi.fn(), onState: vi.fn() };
  controller = {
    conversations: [], turns: [], taskState: 'idle', hasKey: true, loading: false, pending: null,
    attachments: [], followingContext: { view: 'applications-list', label: '同名公司', entity: { kind: 'application', id: 1, label: '同名公司' } },
    activeRequestRef: { current: null }, activeConversationSelectionRef: { current: null }, activePendingRef: { current: null },
    executionControl: { canStop: false, stopping: false },
    isActionOwnerReady: vi.fn(() => true), sendMessage: vi.fn().mockResolvedValue('ignored'), stopActiveRequest: vi.fn(), setLastError: vi.fn(), setComposerDraft: vi.fn(),
  } as unknown as PilotConversationController;
});
afterEach(() => { act(() => root.unmount()); host.remove(); delete window.offerpilotDesktop; });

describe('desktop Haru owner', () => {
  it('publishes an application draft label before a conversation exists without a provider', () => {
    controller = { ...controller, hasKey: false, draftContext: applicationDraft };
    render();
    expect(latest()).toMatchObject({ conversationId: null, contextLabel: applicationDraft.context_label, canSend: false });
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
