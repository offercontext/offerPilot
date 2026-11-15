// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import dayjs from 'dayjs';
import { UiTextError, formatDate, formatNumber, getLocale, LOCALE_STORAGE_KEY, setLocale, t, translateKnownLabel, useLocale } from './index';
import { pipelineActionId } from '@/lib/pipelineInsights';
import { displayPilotPageLabel } from '@/lib/pilotPageContext';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
beforeEach(() => { localStorage.clear(); setLocale('zh-CN'); });
afterEach(() => { vi.restoreAllMocks(); setLocale('zh-CN'); });

describe('interface language', () => {
  it('defaults to Chinese and switches authored copy, document language and date library together', () => {
    expect(t('设置')).toBe('设置');
    setLocale('en-US');
    expect(t('设置')).toBe('Settings');
    expect(document.documentElement.lang).toBe('en-US');
    expect(dayjs.locale()).toBe('en');
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US');
    setLocale('zh-CN');
    expect(dayjs.locale()).toBe('zh-cn');
  });

  it('updates mounted views without replacing local input or component state', async () => {
    const host = document.createElement('div');
    const root = createRoot(host);
    function View() {
      useLocale();
      return <><span>{t('设置')}</span><input defaultValue="用户写的中文" /></>;
    }
    await act(async () => root.render(<View />));
    const input = host.querySelector('input')!;
    input.value = 'Unsaved 中文 draft';
    await act(async () => setLocale('en-US'));
    expect(host.querySelector('span')?.textContent).toBe('Settings');
    expect(host.querySelector('input')).toBe(input);
    expect(input.value).toBe('Unsaved 中文 draft');
    await act(async () => setLocale('zh-CN'));
    expect(input.value).toBe('Unsaved 中文 draft');
    await act(async () => root.unmount());
  });

  it('keeps switching usable when browser storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(() => setLocale('en-US')).not.toThrow();
    expect(getLocale()).toBe('en-US');
    expect(t('设置')).toBe('Settings');
  });

  it('synchronizes same-origin web tabs through storage events', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en-US');
    window.dispatchEvent(new StorageEvent('storage', { key: LOCALE_STORAGE_KEY, newValue: 'en-US' }));
    expect(getLocale()).toBe('en-US');
    localStorage.removeItem(LOCALE_STORAGE_KEY);
    window.dispatchEvent(new StorageEvent('storage', { key: LOCALE_STORAGE_KEY, newValue: null }));
    expect(getLocale()).toBe('zh-CN');
  });

  it('ignores invalid locale writes and leaves unknown user text intact', () => {
    setLocale('fr-FR' as 'en-US');
    expect(getLocale()).toBe('zh-CN');
    setLocale('en-US');
    expect(t('这是用户的简历内容')).toBe('这是用户的简历内容');
    expect(translateKnownLabel('投递列表', '美团投递')).toBe('美团投递');
    expect(displayPilotPageLabel({ view: 'applications-list', label: '美团投递' })).toBe('美团投递');
    expect(displayPilotPageLabel({ view: 'applications-list', label: '投递列表' })).toBe('Application list');
  });

  it('does not resolve unknown text through Object prototype properties', () => {
    setLocale('en-US');
    for (const text of ['constructor', '__proto__', 'toString']) {
      expect(t(text)).toBe(text);
      expect(t(text, {})).toBe(text);
    }
  });

  it('keeps retained authored errors and action identities language-safe', () => {
    const error = new UiTextError('投递 #{value0}', { value0: 12 });
    const action = { target: 'calendar' as const, eventId: 42, label: '查看日程' };
    const id = pipelineActionId({ id: 'insight' }, action, 'secondary');
    setLocale('en-US');
    expect(error.message).toBe('Application #12');
    expect(pipelineActionId({ id: 'insight' }, { ...action, label: 'View schedule' }, 'secondary')).toBe(id);
    setLocale('zh-CN');
    expect(error.message).toBe('投递 #12');
  });

  it('preserves placeholder contents and formats values without changing a supplied timezone', () => {
    setLocale('en-US');
    expect(t('投递 #{value0}', { value0: 42 })).toBe('Application #42');
    expect(t('投递 #{value0}', { value0: '中文<script>' })).toBe('Application #中文<script>');
    expect(formatNumber(12345.5)).toBe('12,345.5');
    expect(formatDate('2026-10-15T23:00:00Z', { timeZone: 'Asia/Shanghai', day: 'numeric' })).toBe('16');
  });
});
