// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import DesktopHaruWindow from './DesktopHaruWindow';
import type { DesktopHaruState } from './desktopHaru';
import { live2dPilotMascotRuntime } from '@/features/pilotMascot/live2dRuntime';
vi.mock('@/features/pilotMascot/live2dRuntime', () => ({ live2dPilotMascotRuntime: { mount: vi.fn() } }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement; let update: (value: DesktopHaruState) => void;
const request = vi.fn(); const windowAction = vi.fn().mockResolvedValue(true); const dispose = vi.fn();
let state: DesktopHaruState;
beforeEach(() => {
  vi.clearAllMocks();
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  state = { connected: true, generation: 3, visible: true, expanded: true, alwaysOnTop: false, snapshot: { version: 2, conversationId: 7, taskState: 'idle', messages: [{ role: 'assistant', content: 'Current answer' }], contextLabel: '工作台', loading: false, hasPending: false, canSend: true, canStop: false, stopping: false, error: '', stopMessage: '' } };
  window.offerpilotDesktop = { role: 'haru', request, windowAction, getState: vi.fn().mockResolvedValue(state), onState: handler => { update = handler; return vi.fn(); } };
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
it('idle is single-frame, hiding disposes runtime and showing restores latest state', async () => {
  await mount();
  expect(live2dPilotMascotRuntime.mount).toHaveBeenCalledWith(expect.anything(), expect.any(AbortSignal), 'off');
  await act(async () => update({ ...state, visible: false }));
  expect(dispose).toHaveBeenCalledTimes(1);
  await act(async () => update({ ...state, snapshot: { ...state.snapshot!, taskState: 'running', canStop: true, loading: true } }));
  expect(live2dPilotMascotRuntime.mount).toHaveBeenLastCalledWith(expect.anything(), expect.any(AbortSignal), 'minimal');
  request.mockResolvedValue({ ok: true });
  await act(async () => button('停止').click());
  expect(request).toHaveBeenCalledWith({ action: 'stop', version: 2, generation: 3 });
});
