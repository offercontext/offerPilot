// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setLocale } from '@/i18n';
import ConversationReadinessControl from '@/components/ConversationReadinessControl';
import ConversationSummaryControl from '@/components/ConversationSummaryControl';

const service = vi.hoisted(() => ({ read: vi.fn(), options: vi.fn(), generate: vi.fn() }));
vi.mock('@/services/conversationReadiness', () => ({
  getConversationReadinessContext: service.read,
  getConversationReadinessOptions: service.options,
  confirmConversationReadiness: vi.fn(),
  clearConversationReadiness: vi.fn(),
}));
vi.mock('@/services/conversationSummary', () => ({
  generateOlderSummary: service.generate,
  withdrawOlderSummary: vi.fn(),
}));

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const getStyle = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => getStyle(element));
  window.matchMedia = vi.fn().mockReturnValue({ matches: false, addListener: vi.fn(), removeListener: vi.fn() });
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  setLocale('zh-CN');
  service.read.mockReset().mockResolvedValue({ state: 'not_applicable', revision: 0 });
  service.options.mockReset().mockResolvedValue({
    events: [{ id: 101, event_type: 'interview', status: 'todo', subtype: '技术面', round: 1 }],
    resumes: [{ id: 201, title: '中文简历原名', deleted_at: null }],
    readiness: [{ signalId: 301, versionId: 401, state: 'available', practiceState: 'not_started',
      practiceSourceFingerprint: `sha256:${'a'.repeat(64)}`, practiceTargetFingerprint: `sha256:${'b'.repeat(64)}`,
      title: '用户准备重点', sourceLabel: '用户复盘来源', selected: false }],
  });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  setLocale('zh-CN');
  vi.restoreAllMocks();
});

it('keeps readiness selections, expanded state, and user labels while changing languages', async () => {
  await act(async () => root.render(<ConversationReadinessControl conversationId={7} applicationId={11}
    targetEventId={101} resumeId={201} selectedVersionIds={[401]} />));
  act(() => host.querySelector<HTMLButtonElement>('#conversation-readiness-title')?.click());
  const checkbox = host.querySelector<HTMLInputElement>('input[type="checkbox"]');
  expect(checkbox?.checked).toBe(true);
  const readCount = service.read.mock.calls.length;
  const optionCount = service.options.mock.calls.length;
  act(() => setLocale('en-US'));
  expect(host.textContent).toContain('Preparation priorities for this interview');
  expect(host.textContent).toContain('Target interview');
  expect(host.textContent).toContain('技术面');
  expect(host.textContent).toContain('中文简历原名');
  expect(host.textContent).toContain('用户准备重点');
  expect(host.textContent).toContain('用户复盘来源');
  expect(host.querySelector('input[type="checkbox"]')).toBe(checkbox);
  expect(checkbox?.checked).toBe(true);
  expect(host.querySelector('#conversation-readiness-title')?.getAttribute('aria-expanded')).toBe('true');
  expect(service.read).toHaveBeenCalledTimes(readCount);
  expect(service.options).toHaveBeenCalledTimes(optionCount);
});

it('translates a summary error in the open modal without restarting the failed operation', async () => {
  service.generate.mockReset().mockRejectedValue(new Error('offline'));
  await act(async () => root.render(<ConversationSummaryControl conversationId={7} busy={false} />));
  act(() => host.querySelector<HTMLButtonElement>('button')?.click());
  const confirm = [...document.querySelectorAll<HTMLButtonElement>('.ant-modal button')]
    .find((button) => button.textContent?.includes('确认整理'));
  await act(async () => confirm?.click());
  expect(document.body.textContent).toContain('当前没有可整理的较早对话');
  act(() => setLocale('en-US'));
  expect(document.body.textContent).toContain('Older conversation summary');
  expect(document.body.textContent).toContain('There are no older messages to summarize');
  expect(document.querySelector('.ant-modal')).not.toBeNull();
  expect(service.generate).toHaveBeenCalledTimes(1);
  act(() => setLocale('zh-CN'));
  expect(document.body.textContent).toContain('当前没有可整理的较早对话');
});
