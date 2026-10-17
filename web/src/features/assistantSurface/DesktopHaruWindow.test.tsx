// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import DesktopHaruWindow from './DesktopHaruWindow';
import type { DesktopHaruState } from './desktopHaru';
import { live2dPilotMascotRuntime } from '@/features/pilotMascot/live2dRuntime';
import { ContextSystem } from '@pixi/core';
vi.mock('@/features/pilotMascot/live2dRuntime', () => ({ live2dPilotMascotRuntime: { mount: vi.fn() } }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement; let update: (value: DesktopHaruState) => void;
const request = vi.fn(); const windowAction = vi.fn().mockResolvedValue(true); const dragWindow = vi.fn().mockResolvedValue(true); const dispose = vi.fn();
let state: DesktopHaruState;
beforeEach(() => {
  vi.clearAllMocks();
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  state = { connected: true, generation: 3, visible: true, expanded: true, alwaysOnTop: false, snapshot: { version: 2, conversationId: 7, taskState: 'idle', messages: [{ role: 'assistant', content: 'Current answer' }], contextLabel: '工作台', loading: false, hasPending: false, canSend: true, canStop: false, stopping: false, error: '', stopMessage: '' } };
  window.offerpilotDesktop = { role: 'haru', request, windowAction, dragWindow, getState: vi.fn().mockImplementation(async () => state), onState: handler => { update = handler; return vi.fn(); } };
  vi.mocked(live2dPilotMascotRuntime.mount).mockResolvedValue({ dispose, setActivity: vi.fn(), setZoom: vi.fn() });
});
afterEach(() => { act(() => root.unmount()); host.remove(); delete window.offerpilotDesktop; });
async function mount() { await act(async () => root.render(<DesktopHaruWindow />)); }
const button = (label: string) => [...host.querySelectorAll('button')].find(item => item.textContent === label)!;
function type(text: string) {
  const input = host.querySelector('textarea')!;
  act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, text); input.dispatchEvent(new Event('input', { bubbles: true })); });
}
it('renders only mirrored state and forwards single sends with generation/version', async () => {
  request.mockResolvedValue({ ok: true }); await mount(); type('hello');
  await act(async () => { button('发送').click(); button('发送').click(); });
  expect(request).toHaveBeenCalledTimes(1);
  expect(request).toHaveBeenCalledWith({ action: 'send', text: 'hello', version: 2, generation: 3 });
  expect(host.querySelector('textarea')!.value).toBe('');
});
it('uses the current Haru portrait for assistant message avatars in both themes while retaining the user icon', async () => {
  state = { ...state, snapshot: { ...state.snapshot!, messages: [{ role: 'assistant', content: 'Current answer' }, { role: 'user', content: 'My message' }] } };
  await mount();
  for (const theme of ['light', 'dark'] as const) {
    await act(async () => update({ ...state, snapshot: { ...state.snapshot!, theme } }));
    const assistantAvatar = host.querySelector('[data-role="assistant"] .desktop-haru-message-avatar')!;
    const userAvatar = host.querySelector('[data-role="user"] .desktop-haru-message-avatar')!;
    const portrait = assistantAvatar.querySelector('img')!;
    expect(portrait.getAttribute('src')).toBe(host.querySelector('.desktop-haru-static-portrait')!.getAttribute('src'));
    expect(portrait.getAttribute('src')).toContain(`haru-window-${theme}.png`);
    expect(portrait.alt).toBe('Haru 头像');
    expect(assistantAvatar.getAttribute('aria-hidden')).not.toBe('true');
    expect(assistantAvatar.querySelector('svg')).toBeNull();
    expect(userAvatar.querySelector('img')).toBeNull();
    expect(userAvatar.querySelector('svg')).not.toBeNull();
  }
  expect(request).not.toHaveBeenCalled();
  expect(live2dPilotMascotRuntime.mount).not.toHaveBeenCalled();
});
it('retains stale submissions and never retries automatically', async () => {
  request.mockResolvedValue({ ok: false, reason: 'stale' }); await mount(); type('keep me');
  await act(async () => button('发送').click());
  expect(host.querySelector('textarea')!.value).toBe('keep me');
  expect(host.textContent).toContain('状态已变化'); expect(request).toHaveBeenCalledTimes(1);
});
it('ambiguous delivery locks replay until explicit manual reconciliation', async () => {
  request.mockResolvedValue({ ok: false, reason: 'uncertain' }); await mount(); type('once');
  await act(async () => button('发送').click());
  expect(host.querySelector('textarea')!.disabled).toBe(true);
  expect(button('发送').disabled).toBe(true);
  await act(async () => button('已在主窗口核对，清空此输入').click());
  expect(host.querySelector('textarea')!.value).toBe(''); expect(request).toHaveBeenCalledTimes(1);
});
it('keeps single-flight and uncertain delivery protection across collapse and reopen', async () => {
  let complete!: (result: { ok: false; reason: 'uncertain' }) => void;
  request.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  await mount(); type('do this once');
  await act(async () => button('发送').click());
  await act(async () => update({ ...state, expanded: false }));
  await act(async () => update(state));
  expect(host.querySelector('textarea')!.value).toBe('do this once');
  expect(button('发送').disabled).toBe(true);
  await act(async () => button('发送').click());
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => complete({ ok: false, reason: 'uncertain' }));
  await act(async () => update({ ...state, expanded: false }));
  await act(async () => update(state));
  expect(host.querySelector('textarea')!.disabled).toBe(true);
  expect(host.querySelector('textarea')!.value).toBe('do this once');
  expect(host.textContent).toContain('发送结果暂未确认');
  expect(button('发送').disabled).toBe(true);
  expect(request).toHaveBeenCalledTimes(1);
});
it('owner loss disables sends and pending actions open owner without confirmation payloads', async () => {
  await mount();
  await act(async () => update({ ...state, connected: false, snapshot: null }));
  expect(host.querySelector('textarea')!.disabled).toBe(true);
  expect(host.textContent).toContain('连接');
  request.mockResolvedValue({ ok: true });
  await act(async () => update({ ...state, snapshot: { ...state.snapshot!, hasPending: true, canSend: false } }));
  await act(async () => button('到 Pilot 查看并确认').click());
  expect(request).toHaveBeenCalledWith({ action: 'open-pending', version: 2, generation: 3 });
});
it('collapsed idle stays animated, hiding disposes runtime and expanded stop uses no Live2D', async () => {
  state = { ...state, expanded: false };
  await mount();
  expect(live2dPilotMascotRuntime.mount).toHaveBeenCalledWith(expect.anything(), expect.any(AbortSignal), 'full');
  await act(async () => update({ ...state, visible: false }));
  expect(dispose).toHaveBeenCalledTimes(1);
  await act(async () => update({ ...state, snapshot: { ...state.snapshot!, taskState: 'running', canStop: true, loading: true } }));
  expect(live2dPilotMascotRuntime.mount).toHaveBeenLastCalledWith(expect.anything(), expect.any(AbortSignal), 'full');
  await act(async () => update({ ...state, expanded: true, snapshot: { ...state.snapshot!, taskState: 'running', canStop: true, loading: true } }));
  expect(host.querySelector('canvas')).toBeNull();
  request.mockResolvedValue({ ok: true });
  await act(async () => button('停止').click());
  expect(request).toHaveBeenCalledWith({ action: 'stop', version: 2, generation: 3 });
});
it('never remounts a canvas whose WebGL context Pixi destroyed across status and visibility changes', async () => {
  state = { ...state, expanded: false };
  const lost = new WeakSet<HTMLCanvasElement>();
  const canvases: HTMLCanvasElement[] = [];
  vi.mocked(live2dPilotMascotRuntime.mount).mockImplementation(async canvas => {
    if (lost.has(canvas)) throw new Error('Cannot reuse destroyed WebGL context');
    canvases.push(canvas);
    // Exercise the installed Pixi teardown, including its real loseContext call.
    const context = new ContextSystem({ view: canvas } as never);
    Object.assign(context, { gl: { useProgram: vi.fn() } });
    context.extensions.loseContext = { loseContext: () => lost.add(canvas), restoreContext: vi.fn() };
    return { dispose: () => context.destroy(), setActivity: vi.fn(), setZoom: vi.fn() };
  });
  await mount();
  const initial = host.querySelector('canvas');
  expect(host.querySelector('.desktop-haru-portrait')?.getAttribute('data-runtime-state')).toBe('ready');
  for (const taskState of ['running', 'waiting_confirmation', 'completed', 'failed', 'idle'] as const) {
    await act(async () => update({ ...state, snapshot: { ...state.snapshot!, taskState, hasPending: taskState === 'waiting_confirmation' } }));
    expect(host.querySelector('.desktop-haru-portrait > span')).toBeNull();
    expect(host.querySelector('canvas')).not.toBe(initial);
    expect(host.querySelector('canvas')?.isConnected).toBe(true);
    expect(host.querySelectorAll('canvas')).toHaveLength(1);
  }
  await act(async () => update({ ...state, visible: false }));
  expect(host.querySelector('canvas')).toBeNull();
  expect(host.querySelector('.desktop-haru-portrait')?.getAttribute('data-runtime-state')).toBe('hidden');
  await act(async () => update(state));
  expect(host.querySelector('.desktop-haru-portrait > span')).toBeNull();
  expect(new Set(canvases).size).toBe(canvases.length);
});
it('removes a failed canvas and still allows opening the static dialogue', async () => {
  state = { ...state, expanded: false };
  vi.mocked(live2dPilotMascotRuntime.mount).mockRejectedValueOnce(new Error('GPU unavailable'));
  await mount();
  expect(host.querySelector('canvas')).toBeNull();
  expect(host.querySelector('.desktop-haru-portrait')?.getAttribute('data-runtime-state')).toBe('failed');
  await act(async () => host.querySelector<HTMLButtonElement>('.desktop-haru-avatar')!.click());
  expect(windowAction).toHaveBeenLastCalledWith('expand');
  await act(async () => update({ ...state, expanded: true }));
  expect(host.querySelector('textarea')!.disabled).toBe(false);
  expect(host.querySelector('.desktop-haru-static-portrait')).not.toBeNull();
});
it('late activity mounts cannot remove or overwrite the current idle canvas', async () => {
  state = { ...state, expanded: false };
  let finish!: (value: Awaited<ReturnType<typeof live2dPilotMascotRuntime.mount>>) => void;
  const lateDispose = vi.fn();
  vi.mocked(live2dPilotMascotRuntime.mount).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await mount();
  const oldCanvas = host.querySelector('canvas');
  await act(async () => update({ ...state, snapshot: { ...state.snapshot!, taskState: 'running' } }));
  await act(async () => update(state));
  const current = host.querySelector('canvas');
  await act(async () => finish({ dispose: lateDispose, setActivity: vi.fn(), setZoom: vi.fn() }));
  expect(lateDispose).toHaveBeenCalledTimes(1);
  expect(oldCanvas?.isConnected).toBe(false);
  expect(host.querySelectorAll('canvas')).toHaveLength(1);
  expect(host.querySelector('canvas')).toBe(current);
  expect(host.querySelector('.desktop-haru-portrait')?.getAttribute('data-runtime-state')).toBe('ready');
});
it('switches between a bare Live2D character and static dialogue while retaining draft and conversation', async () => {
  await mount();
  type('unfinished draft');
  expect(live2dPilotMascotRuntime.mount).not.toHaveBeenCalled();
  let previousCanvas: HTMLCanvasElement | null = null;
  for (const expanded of [false, true, false, true]) {
    await act(async () => update({ ...state, expanded, snapshot: { ...state.snapshot!, contextLabel: '选择的公司和岗位', version: 3 } }));
    if (expanded) {
      expect(host.querySelector('canvas')).toBeNull();
      expect(host.querySelector('.desktop-haru-portrait')).toBeNull();
      expect(host.querySelector('.desktop-haru-static-portrait')).not.toBeNull();
      expect(host.querySelector('textarea')!.value).toBe('unfinished draft');
      expect(host.textContent).toContain('Current answer');
    } else {
      expect(host.querySelector('canvas')).not.toBe(previousCanvas);
      previousCanvas = host.querySelector('canvas');
      expect(host.querySelector('.desktop-haru-portrait')?.getAttribute('data-runtime-state')).toBe('ready');
      expect(host.querySelectorAll('button')).toHaveLength(1);
      expect(host.querySelector('header')).toBeNull();
      expect(host.querySelector('img')).toBeNull();
      expect(host.querySelector('.desktop-haru-speech')).toBeNull();
      expect(host.querySelector('.desktop-haru-presence')).toBeNull();
      expect(host.querySelector('.desktop-haru-chat')).toBeNull();
      expect(host.textContent).toBe('');
    }
  }
  expect(live2dPilotMascotRuntime.mount).toHaveBeenCalledTimes(2);
  expect(dispose).toHaveBeenCalledTimes(2);
  expect(host.querySelector('.desktop-haru-context')?.textContent).toContain('选择的公司和岗位');
  expect(request).not.toHaveBeenCalled();
});
it('updates both themed static images without mounting Live2D or dropping the draft', async () => {
  await mount(); type('keep in both themes');
  for (const theme of ['dark', 'light'] as const) {
    await act(async () => update({ ...state, snapshot: { ...state.snapshot!, theme } }));
    expect(host.querySelector('main')?.getAttribute('data-theme')).toBe(theme);
    expect(host.querySelector('img')?.getAttribute('src')).toContain(`haru-window-${theme}.png`);
    expect(host.querySelector('textarea')!.value).toBe('keep in both themes');
    expect(host.querySelector('canvas')).toBeNull();
  }
  expect(live2dPilotMascotRuntime.mount).not.toHaveBeenCalled();
});
it('discloses the current mirrored context without creating a request or replacing the static portrait', async () => {
  await mount();
  const portrait = host.querySelector('img');
  const toggle = host.querySelector<HTMLButtonElement>('.desktop-haru-context-toggle')!;
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(host.querySelector('#desktop-haru-context-detail')).toBeNull();
  await act(async () => toggle.click());
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  await act(async () => update({ ...state, snapshot: { ...state.snapshot!, contextLabel: '远程团队 · 前端开发工程师 · 第二轮面试', version: 4 } }));
  expect(host.querySelector('#desktop-haru-context-detail')?.textContent).toContain('远程团队 · 前端开发工程师 · 第二轮面试');
  await act(async () => button('在主窗查看').click());
  expect(windowAction).toHaveBeenCalledWith('show-main');
  expect(request).not.toHaveBeenCalled();
  expect(host.querySelector('img')).toBe(portrait);
});
function pointer(type: string, x: number, y: number, primary = true) {
  const event = new MouseEvent(type, { bubbles: true, screenX: x, screenY: y, button: 0 });
  Object.defineProperties(event, { pointerId: { value: 1 }, isPrimary: { value: primary } });
  host.querySelector<HTMLButtonElement>('.desktop-haru-avatar')!.dispatchEvent(event);
}
function pointerClick() {
  host.querySelector<HTMLButtonElement>('.desktop-haru-avatar')!.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
}
it('drags the bare character without expanding, while a fresh click or keyboard activation still expands', async () => {
  state = { ...state, expanded: false }; await mount();
  await act(async () => {
    pointer('pointerdown', 100, 100);
    pointer('pointermove', 102, 100);
    pointer('pointermove', 130, 120);
    pointer('pointerup', 130, 120);
    pointerClick();
  });
  expect(dragWindow.mock.calls.map(([phase]) => phase)).toEqual(['start', 'move', 'end']);
  expect(windowAction).not.toHaveBeenCalled();
  await act(async () => {
    pointer('pointerdown', 130, 120);
    pointer('pointerup', 132, 120);
    pointerClick();
  });
  expect(windowAction).toHaveBeenCalledTimes(1);
  expect(windowAction).toHaveBeenLastCalledWith('expand');
  await act(async () => host.querySelector<HTMLButtonElement>('.desktop-haru-avatar')!.click());
  expect(windowAction).toHaveBeenCalledTimes(2);
  expect(request).not.toHaveBeenCalled();
});
it('suppresses drag expansion even when the last displacement only arrives on pointerup', async () => {
  state = { ...state, expanded: false }; await mount();
  await act(async () => { pointer('pointerdown', 100, 100); pointer('pointerup', 105, 100); pointerClick(); });
  expect(dragWindow.mock.calls.map(([phase]) => phase)).toEqual(['start', 'end']);
  expect(windowAction).not.toHaveBeenCalled();
});
it.each(['pointercancel', 'lostpointercapture', 'blur'])('cancels an interrupted %s gesture without expanding', async interruption => {
  state = { ...state, expanded: false }; await mount();
  await act(async () => {
    pointer('pointerdown', 100, 100);
    if (interruption === 'blur') window.dispatchEvent(new Event('blur'));
    else pointer(interruption, 100, 100);
    pointerClick();
  });
  expect(dragWindow.mock.calls.map(([phase]) => phase)).toEqual(['start', 'cancel']);
  expect(windowAction).not.toHaveBeenCalled();
});
it('dismisses the context disclosure before collapsing the native window and restores keyboard focus', async () => {
  await mount();
  const toggle = host.querySelector<HTMLButtonElement>('.desktop-haru-context-toggle')!;
  await act(async () => toggle.click());
  const escape = () => document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  button('在主窗查看').focus();
  await act(async () => escape());
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(document.activeElement).toBe(toggle);
  expect(windowAction).not.toHaveBeenCalled();
  await act(async () => escape());
  expect(windowAction).toHaveBeenCalledTimes(1);
  expect(windowAction).toHaveBeenCalledWith('collapse');
  await act(async () => update({ ...state, expanded: false }));
  await act(async () => host.querySelector('main')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
  expect(windowAction).toHaveBeenLastCalledWith('collapse');
  expect(windowAction).toHaveBeenCalledTimes(1);
  expect(request).not.toHaveBeenCalled();
});
it('keeps the native portrait toggle and close behavior separate from stopping a running task', async () => {
  await mount();
  await act(async () => update({ ...state, snapshot: { ...state.snapshot!, taskState: 'running', canStop: true, loading: true, canSend: false } }));
  await act(async () => host.querySelector<HTMLButtonElement>('.desktop-haru-avatar')!.click());
  expect(windowAction).toHaveBeenLastCalledWith('collapse');
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="关闭 Haru 对话"]')!.click());
  expect(windowAction).toHaveBeenLastCalledWith('collapse');
  expect(request).not.toHaveBeenCalled();
  expect(button('停止').disabled).toBe(false);
  await act(async () => update({ ...state, expanded: false }));
  await act(async () => host.querySelector<HTMLButtonElement>('.desktop-haru-avatar')!.click());
  expect(windowAction).toHaveBeenLastCalledWith('expand');
  expect(request).not.toHaveBeenCalled();
});

it('missing AI configuration permits drafts, blocks sends and opens settings without losing the conversation', async () => {
  state = { ...state, snapshot: { ...state.snapshot!, aiConfigured: false, canSend: false } };
  await mount();
  expect(host.textContent).toContain('尚未配置 AI');
  expect(host.querySelector('textarea')!.disabled).toBe(false);
  type('保留这份草稿');
  await act(async () => button('发送').click());
  expect(request).not.toHaveBeenCalled();
  await act(async () => button('去配置 AI').click());
  expect(windowAction).toHaveBeenCalledWith('show-ai-settings');
  expect(host.querySelector('textarea')!.value).toBe('保留这份草稿');
  await act(async () => update({ ...state, snapshot: { ...state.snapshot!, aiConfigured: true, canSend: true, version: 3 } }));
  expect(host.textContent).not.toContain('尚未配置 AI');
  expect(button('发送').disabled).toBe(false);
  expect(host.textContent).toContain('Current answer');
  expect(host.querySelector('textarea')!.value).toBe('保留这份草稿');
});

it('rebuilds the original runtime when system reduced motion changes and removes the listener on expansion', async () => {
  const original = window.matchMedia;
  let changed!: () => void;
  const remove = vi.fn();
  const media = { matches: false, addEventListener: vi.fn((_event, callback) => { changed = callback; }), removeEventListener: remove };
  window.matchMedia = vi.fn().mockReturnValue(media);
  try {
    state = { ...state, expanded: false };
    await mount();
    expect(window.matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)');
    await act(async () => { media.matches = true; changed(); });
    expect(dispose).toHaveBeenCalledTimes(1);
    expect(live2dPilotMascotRuntime.mount).toHaveBeenCalledTimes(2);
    await act(async () => { media.matches = false; changed(); });
    expect(dispose).toHaveBeenCalledTimes(2);
    expect(live2dPilotMascotRuntime.mount).toHaveBeenCalledTimes(3);
    await act(async () => update({ ...state, expanded: true }));
    expect(remove).toHaveBeenCalledWith('change', changed);
    expect(dispose).toHaveBeenCalledTimes(3);
    expect(host.querySelector('canvas')).toBeNull();
  } finally { window.matchMedia = original; }
});

it('honors owner animation off and resumes only after an explicit preference change', async () => {
  state = { ...state, expanded: false, snapshot: { ...state.snapshot!, animationLevel: 'off' } };
  await mount();
  expect(live2dPilotMascotRuntime.mount).toHaveBeenLastCalledWith(expect.anything(), expect.any(AbortSignal), 'off');
  for (const level of ['minimal', 'full', 'off'] as const) {
    await act(async () => update({ ...state, snapshot: { ...state.snapshot!, animationLevel: level } }));
    expect(live2dPilotMascotRuntime.mount).toHaveBeenLastCalledWith(expect.anything(), expect.any(AbortSignal), level);
  }
  expect(dispose).toHaveBeenCalledTimes(3);
});

it('retains explicit animation off during owner loss and only resumes on a later full preference', async () => {
  state = { ...state, expanded: false, snapshot: { ...state.snapshot!, animationLevel: 'off' } };
  await mount();
  await act(async () => update({ ...state, connected: false, snapshot: null }));
  expect(live2dPilotMascotRuntime.mount).toHaveBeenCalledTimes(1);
  expect(live2dPilotMascotRuntime.mount).toHaveBeenLastCalledWith(expect.anything(), expect.any(AbortSignal), 'off');
  await act(async () => update(state));
  expect(live2dPilotMascotRuntime.mount).toHaveBeenCalledTimes(1);
  await act(async () => update({ ...state, snapshot: { ...state.snapshot!, animationLevel: 'full' } }));
  expect(live2dPilotMascotRuntime.mount).toHaveBeenLastCalledWith(expect.anything(), expect.any(AbortSignal), 'full');
  expect(dispose).toHaveBeenCalledTimes(1);
});

it.each([false, true])('opens the native character menu when expanded=%s without dragging or changing chat', async expanded => {
  state = { ...state, expanded }; await mount();
  await act(async () => host.querySelector('.desktop-haru-avatar')!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, button: 2 })));
  expect(windowAction).toHaveBeenCalledTimes(1);
  expect(windowAction).toHaveBeenCalledWith('context-menu');
  expect(dragWindow).not.toHaveBeenCalled();
  expect(request).not.toHaveBeenCalled();
});
