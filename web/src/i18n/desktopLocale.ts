/** Native preferences are authoritative for both independently partitioned renderers. */
export type DesktopLocale = 'zh-CN' | 'en-US';
export interface DesktopLocaleBridge {
  getLocale(): Promise<DesktopLocale | null>;
  onLocale(listener: (locale: unknown) => void): () => void;
  /** Absent in the isolated Haru renderer. The main process also enforces this. */
  setLocale?(locale: DesktopLocale): Promise<DesktopLocale | null>;
}
declare global {
  interface Window { offerpilotLocale?: DesktopLocaleBridge }
}
export const isDesktopLocale = (value: unknown): value is DesktopLocale => value === 'zh-CN' || value === 'en-US';
export const DESKTOP_LOCALE_READ_ERROR = '无法读取桌面语言设置，请重新打开应用。';
export const DESKTOP_LOCALE_WRITE_ERROR = '无法保存语言设置，请重试。';

/** Install before get so a newer change cannot be overwritten by the startup reply. */
export async function initializeDesktopLocale(
  applyLocale: (locale: DesktopLocale) => void,
  bridge = typeof window === 'undefined' ? undefined : window.offerpilotLocale,
): Promise<() => void> {
  if (!bridge) return () => {};
  let received = false;
  let active = true;
  const off = bridge.onLocale((value) => {
    if (!active || !isDesktopLocale(value)) return;
    received = true;
    applyLocale(value);
  });
  try {
    const value = await bridge.getLocale();
    if (!received) {
      if (!isDesktopLocale(value)) throw new Error(DESKTOP_LOCALE_READ_ERROR);
      applyLocale(value);
    }
  } catch {
    if (!received) { active = false; off(); throw new Error(DESKTOP_LOCALE_READ_ERROR); }
  }
  return () => { active = false; off(); };
}

/** Call before applying an owner's setting change. A failed write never reports success. */
export async function persistDesktopLocale(
  locale: DesktopLocale,
  bridge = typeof window === 'undefined' ? undefined : window.offerpilotLocale,
): Promise<DesktopLocale> {
  if (!isDesktopLocale(locale) || !bridge?.setLocale) throw new Error(DESKTOP_LOCALE_WRITE_ERROR);
  try {
    const saved = await bridge.setLocale(locale);
    if (saved !== locale) throw new Error(DESKTOP_LOCALE_WRITE_ERROR);
    return saved;
  } catch { throw new Error(DESKTOP_LOCALE_WRITE_ERROR); }
}
