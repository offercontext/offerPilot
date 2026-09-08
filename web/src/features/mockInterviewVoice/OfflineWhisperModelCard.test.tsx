// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import OfflineWhisperModelCard from './OfflineWhisperModelCard';
import type { OfflineModelState, OfflineWhisperController } from './offlineWhisperTypes';

function fakeController(initial: OfflineModelState) {
  let state = initial;
  const listeners = new Set<() => void>();
  const controller: OfflineWhisperController & { setState(next: OfflineModelState): void } = {
    getState: () => state,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener); },
    check: vi.fn(async () => undefined),
    prepare: vi.fn(async () => 'webgpu' as const),
    transcribe: vi.fn(async () => ({ text: '示例', backend: 'webgpu' as const })),
    cancel: vi.fn(),
    remove: vi.fn(async () => undefined),
    dispose: vi.fn(),
    setState(next) { state = next; listeners.forEach((listener) => listener()); },
  };
  return controller;
}

let container: HTMLDivElement | undefined;
let root: Root | undefined;

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
});

async function renderCard(controller: OfflineWhisperController) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root!.render(<OfflineWhisperModelCard controller={controller} />));
  return container;
}

describe('OfflineWhisperModelCard', () => {
  it('does not download until the user clicks the explicit action', async () => {
    const controller = fakeController({ status: 'not_downloaded' });
    const onActivityChange = vi.fn();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root!.render(
      <OfflineWhisperModelCard controller={controller} onActivityChange={onActivityChange} />,
    ));
    const view = container;
    expect(view.textContent).toContain('录音不会上传');
    expect(controller.prepare).not.toHaveBeenCalled();
    const button = Array.from(view.querySelectorAll('button')).find((item) => item.textContent?.includes('下载离线模型'))!;
    await act(async () => button.click());
    expect(controller.prepare).toHaveBeenCalledOnce();
    expect(onActivityChange).toHaveBeenNthCalledWith(1, 'preparing_voice');
    expect(onActivityChange).toHaveBeenLastCalledWith('success');
  });

  it('renders determinate progress only when a total is known', async () => {
    const controller = fakeController({ status: 'downloading', receivedBytes: 50, totalBytes: 100 });
    const view = await renderCard(controller);
    const progress = view.querySelector('[role="progressbar"]')!;
    expect(progress.getAttribute('aria-valuenow')).toBe('50');
    await act(async () => controller.setState({ status: 'downloading', receivedBytes: 80 }));
    expect(view.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBeNull();
  });

  it('shows the ready backend and supports explicit removal', async () => {
    const controller = fakeController({ status: 'ready', modelVersion: 'abc', cachedBytes: 100, backend: 'wasm' });
    const view = await renderCard(controller);
    expect(view.textContent).toContain('兼容模式');
    const button = Array.from(view.querySelectorAll('button')).find((item) => item.textContent?.includes('删除模型'))!;
    await act(async () => button.click());
    const confirm = Array.from(document.body.querySelectorAll('button')).find((item) => item.textContent?.includes('确认删除'))!;
    await act(async () => confirm.click());
    expect(controller.remove).toHaveBeenCalledOnce();
  });
});
