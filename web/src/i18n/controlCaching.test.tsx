// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import ReviewFormDrawer from '@/components/ReviewFormDrawer';
import type { InterviewNote } from '@/types/note';
import { setLocale } from './index';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
const getComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element) => getComputedStyle(element);
let root: Root | undefined;
let host: HTMLDivElement | undefined;
afterEach(() => { act(() => root?.unmount()); host?.remove(); setLocale('zh-CN'); });

it('refreshes retained review mood selections without resetting saved input', async () => {
  setLocale('zh-CN');
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  const note = { id: 1, application_id: null, company: '用户公司', mood: 'good' } as unknown as InterviewNote;
  await act(async () => root!.render(<ReviewFormDrawer open applications={[]} note={note} onSubmit={vi.fn()} onClose={vi.fn()} />));
  expect(host.querySelector('#mood')?.closest('.ant-select')?.textContent).toContain('好');
  await act(async () => setLocale('en-US'));
  expect(host.querySelector('#mood')?.closest('.ant-select')?.textContent).toContain('Good');
  expect(host.querySelector<HTMLInputElement>('#company')?.value).toBe('用户公司');
});
