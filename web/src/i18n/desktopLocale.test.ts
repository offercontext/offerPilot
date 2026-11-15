import { describe, expect, it, vi } from 'vitest';
import { initializeDesktopLocale, persistDesktopLocale, type DesktopLocaleBridge } from './desktopLocale';
function mockBridge() {
  let listener: ((value: unknown) => void) | undefined;
  const off = vi.fn();
  const bridge: DesktopLocaleBridge = {
    getLocale: vi.fn(async () => 'en-US' as const),
    onLocale: vi.fn(fn => { listener = fn; return off; }),
    setLocale: vi.fn(async locale => locale),
  };
  return { bridge, emit: (value: unknown) => listener?.(value), off };
}
describe('desktop locale synchronization', () => {
  it('initializes from main and applies only valid changes without echo writes', async () => {
    const h = mockBridge(); const apply = vi.fn(); const stop = await initializeDesktopLocale(apply, h.bridge);
    expect(apply).toHaveBeenLastCalledWith('en-US');
    h.emit('zh-CN'); expect(apply).toHaveBeenLastCalledWith('zh-CN');
    for (const value of ['fr-FR', null, { locale: 'en-US' }]) h.emit(value);
    expect(apply).toHaveBeenCalledTimes(2); expect(h.bridge.setLocale).not.toHaveBeenCalled();
    stop(); h.emit('en-US'); expect(apply).toHaveBeenCalledTimes(2); expect(h.off).toHaveBeenCalledOnce();
  });
  it('does not overwrite a newer broadcast with a stale startup reply', async () => {
    const h = mockBridge(); let resolve!: (value: 'zh-CN') => void;
    h.bridge.getLocale = () => new Promise(done => { resolve = done; });
    const apply = vi.fn(); const pending = initializeDesktopLocale(apply, h.bridge);
    h.emit('en-US'); resolve('zh-CN'); await pending;
    expect(apply.mock.calls).toEqual([['en-US']]);
  });
  it('fails safely and unsubscribes for missing or rejected authoritative values', async () => {
    for (const getLocale of [async () => null, async () => { throw Error('IPC gone'); }]) {
      const h = mockBridge(); h.bridge.getLocale = getLocale; const apply = vi.fn();
      await expect(initializeDesktopLocale(apply, h.bridge)).rejects.toThrow('无法读取桌面语言设置');
      expect(apply).not.toHaveBeenCalled(); expect(h.off).toHaveBeenCalledOnce();
    }
  });
  it('waits for persistence and rejects Haru writes, invalid replies, and disk failures', async () => {
    const h = mockBridge(); expect(await persistDesktopLocale('en-US', h.bridge)).toBe('en-US');
    for (const bridge of [{ ...h.bridge, setLocale: undefined }, { ...h.bridge, setLocale: async () => null }, { ...h.bridge, setLocale: async () => { throw Error('write failed'); } }]) {
      await expect(persistDesktopLocale('en-US', bridge)).rejects.toThrow('无法保存语言设置');
    }
    expect(h.bridge.setLocale).toHaveBeenCalledWith('en-US');
  });
});
