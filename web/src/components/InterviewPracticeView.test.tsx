// @vitest-environment jsdom
import { act } from 'react';
import { setLocale } from '@/i18n';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./AdaptiveInterviewPracticeWorkspace', () => ({
  default: () => <div data-testid="adaptive-practice-owner">复盘重点练习内容</div>,
}));
vi.mock('@/features/interviewReadiness/InterviewReadinessCenter', () => ({
  default: () => <div data-testid="readiness-owner">快速模拟内容</div>,
}));

const { default: InterviewPracticeView } = await import('./InterviewPracticeView');

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  setLocale('zh-CN');
});

describe('InterviewPracticeView', () => {
  it('honors the directly selected home entry without replacing either retained workspace', () => {
    act(() => root.render(<InterviewPracticeView initialMode="review" />));
    const owner = host.querySelector('[data-testid="adaptive-practice-owner"]');
    expect(host.querySelector<HTMLElement>('[data-testid="review-focus-practice"]')?.hidden).toBe(false);
    act(() => root.render(<InterviewPracticeView initialMode="quick" />));
    expect(host.querySelector<HTMLElement>('[data-testid="quick-interview-practice"]')?.hidden).toBe(false);
    expect(host.querySelector('[data-testid="adaptive-practice-owner"]')).toBe(owner);
  });

  it('switches labels without resetting the selected practice surface', () => {
    act(() => root.render(<InterviewPracticeView />));
    act(() => [...host.querySelectorAll<HTMLElement>('.ant-segmented-item')]
      .find((item) => item.textContent?.includes('复盘重点练习'))?.click());
    const owner = host.querySelector('[data-testid="adaptive-practice-owner"]');
    act(() => setLocale('en-US'));
    expect(host.querySelector('#interview-practice-title')?.textContent).toBe('Interview practice');
    expect(host.querySelector('[data-testid="adaptive-practice-owner"]')).toBe(owner);
    expect(host.querySelector<HTMLElement>('[data-testid="review-focus-practice"]')?.hidden).toBe(false);
    expect(host.querySelector<HTMLElement>('[data-testid="quick-interview-practice"]')?.hidden).toBe(true);
    act(() => setLocale('zh-CN'));
    expect(host.querySelector('#interview-practice-title')?.textContent).toBe('面试练习');
    expect(host.querySelector<HTMLElement>('[data-testid="review-focus-practice"]')?.hidden).toBe(false);
  });

  it('keeps quick simulation and review-focus practice as exclusive interview surfaces', () => {
    act(() => root.render(<InterviewPracticeView />));

    expect(host.querySelector('[data-testid="quick-interview-practice"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="readiness-owner"]')).not.toBeNull();
    expect(host.querySelector<HTMLElement>('[data-testid="quick-interview-practice"]')?.hidden).toBe(false);
    expect(host.querySelector<HTMLElement>('[data-testid="review-focus-practice"]')?.hidden).toBe(true);
    expect(host.querySelector('[data-testid="adaptive-practice-owner"]')).not.toBeNull();

    act(() => [...host.querySelectorAll<HTMLElement>('.ant-segmented-item')]
      .find((item) => item.textContent?.includes('复盘重点练习'))?.click());

    expect(host.querySelector<HTMLElement>('[data-testid="quick-interview-practice"]')?.hidden).toBe(true);
    expect(host.querySelector('[data-testid="readiness-owner"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="review-focus-practice"]')).not.toBeNull();
    expect(host.querySelector<HTMLElement>('[data-testid="review-focus-practice"]')?.hidden).toBe(false);
    expect(host.querySelector('[data-testid="adaptive-practice-owner"]')).not.toBeNull();
  });

  it('opens the review-focus mode when launched with an exact readiness focus', () => {
    act(() => root.render(<InterviewPracticeView adaptiveFocus={{
      ownerGeneration: 8,
      signalVersionId: 91,
      targetEventId: 103,
    }} />));

    expect(host.querySelector('[data-testid="review-focus-practice"]')).not.toBeNull();
    expect(host.querySelector<HTMLElement>('[data-testid="review-focus-practice"]')?.hidden).toBe(false);
    expect(host.querySelector('[data-testid="adaptive-practice-owner"]')).not.toBeNull();
    expect(host.querySelector<HTMLElement>('[data-testid="quick-interview-practice"]')?.hidden).toBe(true);
    expect(host.querySelector('[data-testid="readiness-owner"]')).not.toBeNull();
  });
});
