// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import type { DesktopLocale, DesktopLocaleBridge } from './desktopLocale';
function harness(initial: DesktopLocale = 'en-US') {
  let emit!: (value: unknown) => void;
  const bridge: DesktopLocaleBridge = { getLocale: vi.fn(async () => initial), onLocale: vi.fn(listener => { emit = listener; return vi.fn(); }), setLocale: vi.fn(async locale => locale) };
  window.offerpilotLocale = bridge;
  return { bridge, emit: (value: unknown) => emit(value) };
}
afterEach(() => { delete window.offerpilotLocale; delete window.offerpilotDesktop; localStorage.clear(); vi.resetModules(); vi.restoreAllMocks(); });
it('uses durable native preference before mount and ignores renderer-local storage', async () => {
  localStorage.setItem('offerpilot.locale', 'zh-CN');
  const h = harness(); const write = vi.spyOn(Storage.prototype, 'setItem');
  const core = await import('./index'); await core.initializeLocale();
  expect(core.getLocale()).toBe('en-US'); expect(write).not.toHaveBeenCalled(); expect(h.bridge.setLocale).not.toHaveBeenCalled();
  window.dispatchEvent(new StorageEvent('storage', { key: 'offerpilot.locale', newValue: 'zh-CN' }));
  expect(core.getLocale()).toBe('en-US'); h.emit('zh-CN'); expect(core.getLocale()).toBe('zh-CN');
});
it('does not let older setter replies overwrite a newer authoritative event', async () => {
  const h = harness('zh-CN'); const core = await import('./index'); await core.initializeLocale();
  const replies: Array<(value: DesktopLocale) => void> = [];
  h.bridge.setLocale = vi.fn(() => new Promise<DesktopLocale>(resolve => replies.push(resolve)));
  const older = core.setLocale('en-US'); await Promise.resolve();
  const newer = core.setLocale('zh-CN'); await Promise.resolve();
  h.emit('en-US'); h.emit('zh-CN'); replies[1]('zh-CN'); await newer;
  replies[0]('en-US'); await older; expect(core.getLocale()).toBe('zh-CN');
});
it('keeps displayed language on durable write failure and prevents Haru writes', async () => {
  const h = harness(); const core = await import('./index'); await core.initializeLocale();
  h.bridge.setLocale = vi.fn(async () => { throw Error('disk full'); });
  await expect(core.setLocale('zh-CN')).rejects.toThrow(); expect(core.getLocale()).toBe('en-US');
  delete h.bridge.setLocale; await expect(core.setLocale('zh-CN')).rejects.toThrow(); expect(core.getLocale()).toBe('en-US');
});
it('fails closed when a desktop renderer has no trusted preference bridge', async () => {
  window.offerpilotDesktop = { role: 'haru' } as Window['offerpilotDesktop'];
  const core = await import('./index'); await expect(core.initializeLocale()).rejects.toThrow('无法读取桌面语言设置');
});
