// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { setLocale, t } from './index';
import { liveText } from './liveText';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('updates an already-created transient message without recreating its container', async () => {
  setLocale('zh-CN');
  const host = document.createElement('div');
  const root = createRoot(host);
  const content = liveText(() => t('版本 {version}', { version: 12 }));
  await act(async () => root.render(<section>{content}</section>));
  const section = host.firstElementChild;
  expect(host.textContent).toBe('版本 12');
  await act(async () => setLocale('en-US'));
  expect(host.textContent).toBe('Version 12');
  expect(host.firstElementChild).toBe(section);
  await act(async () => setLocale('zh-CN'));
  expect(host.textContent).toBe('版本 12');
  await act(async () => root.unmount());
});
