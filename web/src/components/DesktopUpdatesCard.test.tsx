// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DesktopUpdatesCard from './DesktopUpdatesCard';
import type { DesktopUpdatesBridge, UpdateState } from '@/features/desktopUpdates/types';

let container: HTMLDivElement;
let root: Root;
let emit: (state: UpdateState) => void;
let bridge: DesktopUpdatesBridge;
const unsubscribe = vi.fn();
const initial: UpdateState = { status: 'idle', currentVersion: '0.3.0' };

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  bridge = {
    getState: vi.fn().mockResolvedValue(initial),
    check: vi.fn().mockResolvedValue({ ...initial, status: 'available', version: '0.4.0', releaseNotes: '<b>重要更新</b>\n第二行' }),
    download: vi.fn().mockResolvedValue({ ...initial, status: 'downloaded', version: '0.4.0' }),
    install: vi.fn().mockResolvedValue({ ...initial, status: 'installing', version: '0.4.0' }),
    onState: vi.fn((listener) => { emit = listener; return unsubscribe; }),
    onPrepareInstall: vi.fn(() => vi.fn()),
    replyPrepareInstall: vi.fn(),
  };
  window.offerpilotUpdates = bridge;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete window.offerpilotUpdates;
  vi.clearAllMocks();
});

async function render() {
  await act(async () => root.render(<DesktopUpdatesCard />));
}

function button(text: string) {
  const result = [...container.querySelectorAll('button')].find((item) => item.textContent?.replace(/\s/g, '') === text);
  if (!result) throw new Error(`Missing button: ${text}`);
  return result;
}

async function click(text: string) {
  await act(async () => button(text).click());
}

describe('DesktopUpdatesCard', () => {
  it('renders nothing on the web without a desktop update bridge', async () => {
    delete window.offerpilotUpdates;
    await render();
    expect(container.innerHTML).toBe('');
    expect(bridge.getState).not.toHaveBeenCalled();
  });

  it('checks, downloads and installs only after each explicit click; notes remain plain text', async () => {
    await render();
    expect(container.textContent).toContain('当前桌面版本：0.3.0');
    expect(bridge.check).not.toHaveBeenCalled();
    expect(bridge.download).not.toHaveBeenCalled();
    await click('检查更新');
    expect(container.textContent).toContain('发现新版本 · 0.4.0');
    expect(container.textContent).toContain('<b>重要更新</b>');
    expect(container.querySelector('b')).toBeNull();
    await click('下载更新');
    expect(container.textContent).toContain('更新已下载，等待安装');
    expect(bridge.install).not.toHaveBeenCalled();
    await click('退出并安装');
    expect(bridge.install).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain('正在准备退出并安装');
  });

  it('subscribes to progress, ignores a stale initial read and unsubscribes on unmount', async () => {
    let resolveInitial!: (state: UpdateState) => void;
    vi.mocked(bridge.getState).mockReturnValue(new Promise((resolve) => { resolveInitial = resolve; }));
    await render();
    act(() => emit({ ...initial, status: 'downloading', percent: 41.7 }));
    await act(async () => resolveInitial(initial));
    expect(container.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('42');
    expect(button('检查更新').disabled).toBe(true);
    act(() => root.unmount());
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    root = createRoot(container);
  });

  it('keeps newer progress when an action returns an older state and prevents duplicate commands', async () => {
    let resolveCheck!: (state: UpdateState) => void;
    vi.mocked(bridge.check).mockReturnValue(new Promise((resolve) => { resolveCheck = resolve; }));
    await render();
    await click('检查更新');
    await click('检查更新');
    expect(bridge.check).toHaveBeenCalledTimes(1);
    act(() => emit({ ...initial, status: 'available', version: '0.5.0' }));
    await act(async () => resolveCheck({ ...initial, status: 'checking' }));
    expect(container.textContent).toContain('发现新版本 · 0.5.0');
  });

  it('shows unavailable reasons without enabling checks or claiming a release exists', async () => {
    vi.mocked(bridge.getState).mockResolvedValue({ ...initial, status: 'unavailable', reason: '尚未配置可信更新源。' });
    await render();
    expect(container.textContent).toContain('尚未配置可信更新源。');
    expect(container.textContent).not.toContain('发现新版本');
    expect(button('检查更新').disabled).toBe(true);
    expect([...container.querySelectorAll('button')].map((item) => item.textContent)).not.toContain('下载更新');
  });

  it('shows downloaded refusal/cancellation reasons and retains the install action', async () => {
    vi.mocked(bridge.getState).mockResolvedValue({ ...initial, status: 'downloaded', reason: '仍有待审批操作，请先处理。' });
    await render();
    expect(container.textContent).toContain('仍有待审批操作，请先处理。');
    expect(button('退出并安装').disabled).toBe(false);
    vi.mocked(bridge.install).mockResolvedValue({ ...initial, status: 'downloaded', reason: '已取消安装，可稍后继续。' });
    await click('退出并安装');
    expect(container.textContent).toContain('已取消安装，可稍后继续。');
  });

  it('recovers an error state by rechecking before allowing another download', async () => {
    await render();
    await click('检查更新');
    vi.mocked(bridge.download).mockResolvedValue({ ...initial, status: 'error', reason: '下载失败' });
    await click('下载更新');
    expect(container.textContent).toContain('下载失败');
    expect(() => button('下载更新')).toThrow();
    await click('重新检查');
    expect(bridge.check).toHaveBeenCalledTimes(2);
    expect(button('下载更新').disabled).toBe(false);
  });

  it('can retry reading/checking after initialization fails', async () => {
    vi.mocked(bridge.getState).mockRejectedValue(new Error('IPC disconnected'));
    await render();
    expect(container.textContent).toContain('无法读取桌面更新状态');
    expect(container.textContent).toContain('当前桌面版本：未知');
    await click('重新检查');
    expect(container.textContent).toContain('发现新版本');
  });

  it('surfaces download failure and retries only after an explicit click', async () => {
    await render();
    await click('检查更新');
    vi.mocked(bridge.download).mockRejectedValueOnce(new Error('网络暂时不可用'));
    await click('下载更新');
    expect(container.textContent).toContain('网络暂时不可用');
    expect(bridge.download).toHaveBeenCalledTimes(1);
    await click('重新检查');
    expect(bridge.check).toHaveBeenCalledTimes(2);
    await click('下载更新');
    expect(bridge.download).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('更新已下载');
  });
});
