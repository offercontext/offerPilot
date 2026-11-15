import { initializeDesktopLocale, persistDesktopLocale, DESKTOP_LOCALE_READ_ERROR, DESKTOP_LOCALE_WRITE_ERROR } from './desktopLocale';
import { useSyncExternalStore } from 'react';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import 'dayjs/locale/en';

export type Locale = 'zh-CN' | 'en-US';
export const LOCALE_STORAGE_KEY = 'offerpilot.locale';
const catalogs = import.meta.glob<{ default: Record<string, string> }>('./catalogs/*.ts', { eager: true });
const english: Record<string, string> = Object.create(null);
for (const catalog of Object.values(catalogs)) {
  for (const [key, value] of Object.entries(catalog.default)) {
    if (english[key] !== undefined && english[key] !== value) {
      throw new Error(`Conflicting UI translation: ${key}`);
    }
    english[key] = value;
  }
}
const listeners = new Set<() => void>();
function readLocale(): Locale {
  try { return localStorage.getItem(LOCALE_STORAGE_KEY) === 'en-US' ? 'en-US' : 'zh-CN'; }
  catch { return 'zh-CN'; }
}
let locale: Locale = readLocale();
function applyLocale() {
  dayjs.locale(locale === 'zh-CN' ? 'zh-cn' : 'en');
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale;
    document.title = locale === 'en-US' ? 'OfferPilot · Job Search Workspace' : 'OfferPilot · 求职管理台';
  }
}
applyLocale();
function applyPreference(next: Locale, persistWeb = true) {
  if (next !== 'zh-CN' && next !== 'en-US') return;
  locale = next;
  try { if (persistWeb) localStorage.setItem(LOCALE_STORAGE_KEY, next); } catch { /* Private/locked storage must not block switching. */ }
  applyLocale();
  listeners.forEach((listener) => listener());
}
const nativeContext = () => typeof window !== 'undefined' && Boolean(window.offerpilotLocale || window.offerpilotDesktop);
let nativeInitialization: Promise<void> | undefined;
let nativeRevision = 0;
let nativeRequest = 0;
/** Native preferences are authoritative before either renderer mounts. */
export function initializeLocale(): Promise<void> {
  if (!nativeContext()) return Promise.resolve();
  if (!window.offerpilotLocale) return Promise.reject(new UiTextError(DESKTOP_LOCALE_READ_ERROR));
  nativeInitialization ??= initializeDesktopLocale((next) => {
    nativeRevision += 1;
    applyPreference(next, false);
  }).then(() => undefined);
  return nativeInitialization;
}
export function setLocale(next: Locale): void | Promise<void> {
  if (next !== 'zh-CN' && next !== 'en-US') return;
  if (!nativeContext()) { applyPreference(next); return; }
  const request = ++nativeRequest;
  return initializeLocale().then(async () => {
    const revision = nativeRevision;
    try {
      const saved = await persistDesktopLocale(next);
      // Main's newer broadcast/request always wins over an older setter reply.
      if (request === nativeRequest && revision === nativeRevision) applyPreference(saved, false);
    } catch { throw new UiTextError(DESKTOP_LOCALE_WRITE_ERROR); }
  });
}
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (nativeContext()) return;
    if (event.key !== LOCALE_STORAGE_KEY && event.key !== null) return;
    locale = readLocale();
    applyLocale();
    listeners.forEach((listener) => listener());
  });
}
export function subscribeLocale(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function getLocale(): Locale { return locale; }
export function useLocale() {
  return useSyncExternalStore(subscribeLocale, getLocale, () => 'zh-CN' as Locale);
}
/** Translate only authored UI copy. Never pass user documents or AI replies here. */
export function t(source: string, values?: Record<string, string | number | boolean | null | undefined>, context?: string): string {
  const message = locale === 'en-US' ? english[context ?? source] ?? english[source] ?? source : source;
  return values ? message.replace(/\{(\w+)\}/g, (match, key: string) => Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match) : message;
}
/** An authored error retains its key so a mounted error state can change language. */
export class UiTextError extends Error {
  constructor(source: string, values?: Record<string, string | number>) {
    super(source);
    Object.defineProperty(this, 'message', { configurable: true, get: () => t(source, values) });
  }
}
/** Translate a known system label without modifying a custom/user label. */
export function translateKnownLabel(source: string, value: string): string {
  return value === source || value === english[source] ? t(source) : value;
}
export function formatNumber(value: number, options?: Intl.NumberFormatOptions) {
  return new Intl.NumberFormat(locale, options).format(value);
}
export function formatDate(value: string | number | Date, options?: Intl.DateTimeFormatOptions) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(locale, options).format(date);
}
