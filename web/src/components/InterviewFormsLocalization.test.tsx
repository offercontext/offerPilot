// @vitest-environment jsdom
import { act } from 'react';
import { ConfigProvider } from 'antd';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setLocale } from '@/i18n';
import ReviewFormDrawer from './ReviewFormDrawer';
import QuestionBankView from './QuestionBankView';

const questionService = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock('@/services/questions', () => ({
  listQuestions: vi.fn(async () => []),
  listDueQuestions: vi.fn(async () => []),
  getPracticeStats: vi.fn(async () => ({ total: 0, mastered: 0, due: 0, today_reviews: 0, streak_days: 0 })),
  createQuestion: questionService.create,
  updateQuestion: vi.fn(),
  deleteQuestion: vi.fn(),
  generateQuestions: vi.fn(),
  submitReview: vi.fn(),
}));

const getComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element) => getComputedStyle(element);

let root: Root;
let host: HTMLDivElement;
let client: QueryClient;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  setLocale('zh-CN');
  questionService.create.mockClear();
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  client.clear();
  setLocale('zh-CN');
});

function click(label: string) {
  const target = [...document.querySelectorAll<HTMLButtonElement>('button')]
    .find((item) => item.textContent?.replace(/\s/g, '') === label.replace(/\s/g, ''));
  if (!target) throw new Error(`Missing button: ${label}`);
  target.click();
}
function input(id: string, value: string) {
  const element = document.querySelector<HTMLInputElement>(`#${id}`)!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value);
  element.dispatchEvent(new Event('input', { bubbles: true }));
}
function errors() {
  return [...document.querySelectorAll('.ant-form-item-explain-error')].map((item) => item.textContent).join(' ');
}
async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 25));
}
async function expectError(text: string) {
  await vi.waitFor(async () => {
    await act(async () => { await settle(); });
    expect(errors()).toContain(text);
  }, { timeout: 2000 });
}

describe('Interview form localization', () => {
  it('refreshes retained review validation errors without changing inputs or submitting', async () => {
    const submit = vi.fn();
    await act(async () => root.render(<ConfigProvider theme={{ token: { motion: false } }}><ReviewFormDrawer open applications={[]} onSubmit={submit} onClose={vi.fn()} /></ConfigProvider>));
    await act(async () => { input('position', '用户填写的岗位'); click('保存复盘'); await settle(); });
    await act(async () => { await settle(); });
    await expectError('请输入公司');

    await act(async () => { setLocale('en-US'); await settle(); });
    await act(async () => { await settle(); });
    await expectError('Enter a company');
    expect(document.querySelector<HTMLInputElement>('#position')?.value).toBe('用户填写的岗位');
    expect(submit).not.toHaveBeenCalled();

    await act(async () => { setLocale('zh-CN'); await settle(); });
    await act(async () => { await settle(); });
    await expectError('请输入公司');
    expect(submit).not.toHaveBeenCalled();
  });

  it('refreshes question validation without creating a question or losing category input', async () => {
    await act(async () => {
      root.render(<ConfigProvider theme={{ token: { motion: false } }}><QueryClientProvider client={client}><QuestionBankView /></QueryClientProvider></ConfigProvider>);
      await settle();
    });
    await act(async () => { click('手动添加'); await settle(); });
    await act(async () => { input('category', '我的自定义分类'); click('保存'); await settle(); });
    await act(async () => { await settle(); });
    await expectError('请输入题目');

    await act(async () => { setLocale('en-US'); await settle(); });
    await act(async () => { await settle(); });
    await expectError('Enter a question');
    expect(document.querySelector<HTMLInputElement>('#category')?.value).toBe('我的自定义分类');
    expect(questionService.create).not.toHaveBeenCalled();

    await act(async () => { setLocale('zh-CN'); await settle(); });
    await act(async () => { await settle(); });
    await expectError('请输入题目');
    expect(questionService.create).not.toHaveBeenCalled();
  });
});
