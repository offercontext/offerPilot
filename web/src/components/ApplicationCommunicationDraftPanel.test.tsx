// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { App as AntApp } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Application } from '@/types/application';
import type { ApplicationOutcome, ApplicationSubmissionSnapshot } from '@/types/applicationOutcome';
import type { ScheduleEvent } from '@/types/event';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
if (!HTMLElement.prototype.scrollIntoView) HTMLElement.prototype.scrollIntoView = vi.fn();
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

const { default: ApplicationCommunicationDraftPanel } = await import('./ApplicationCommunicationDraftPanel');

const application = {
  id: 7,
  company_name: '云栖智能',
  position_name: '高级后端工程师',
  status: 'interview',
} as Application;

const snapshot = {
  id: 11,
  application_id: 7,
  resume_id: 2,
  resume_title: '筱哲 · 高级后端工程师版',
  jd_version_id: 3,
  jd_version_number: 2,
  material_kit_id: null,
  resume_snapshot: {},
  jd_snapshot: '负责高并发 API 设计。',
  material_snapshot: null,
  note: '',
  source_kind: 'ui',
  source_states: { resume: 'current', jd: 'current', material: 'missing' },
  submitted_at: '2026-08-12T09:00:00Z',
  created_at: '2026-08-12T09:00:00Z',
} satisfies ApplicationSubmissionSnapshot;

const outcome = {
  id: 41,
  application_id: 7,
  submission_snapshot_id: 11,
  application_event_id: 31,
  stage: 'interview',
  result: 'advanced',
  feedback_text: '',
  reflection_text: '',
  next_action_text: '',
  feedback_tags: [],
  source_kind: 'ui',
  occurred_at: '2026-08-15T11:40:00+08:00',
  created_at: '2026-08-15T11:40:00+08:00',
} satisfies ApplicationOutcome;

const event = {
  id: 31,
  application_id: 7,
  event_type: 'interview',
  subtype: 'technical',
  tags: [],
  round: 2,
  scheduled_at: '2026-08-15T10:30:00+08:00',
  duration_minutes: 60,
  location: '',
  notes: '',
  status: 'scheduled',
  created_at: '2026-08-10T10:00:00Z',
} satisfies ScheduleEvent;

let container: HTMLDivElement;
let root: Root;
let clipboardWrite: ReturnType<typeof vi.fn>;

function button(label: string): HTMLButtonElement {
  const match = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
    .find((item) => item.textContent?.includes(label));
  if (!match) throw new Error(`Missing button: ${label}`);
  return match;
}

function input(selector: string, value: string) {
  const element = document.body.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector);
  if (!element) throw new Error(`Missing input: ${selector}`);
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(
      element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
      'value',
    )?.set;
    setter?.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function selectOption(label: string, optionText: string) {
  const select = document.body.querySelector<HTMLElement>(`[aria-label="${label}"]`);
  if (!select) throw new Error(`Missing select: ${label}`);
  const trigger = select.querySelector<HTMLElement>('.ant-select-selector') ?? select;
  await act(async () => {
    trigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    trigger.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  const option = [...document.body.querySelectorAll<HTMLElement>('[role="option"], .ant-select-item-option')]
    .find((item) => item.textContent?.includes(optionText));
  if (!option) throw new Error(`Missing option: ${optionText}`);
  await act(async () => {
    option.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    option.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  clipboardWrite = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: clipboardWrite } });
});

afterEach(() => {
  act(() => root.unmount());
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe('ApplicationCommunicationDraftPanel', () => {
  it('generates, edits, restores, and copies a follow-up without requests or writes', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('unexpected request'));
    const historySpy = vi.spyOn(history, 'pushState');
    act(() => root.render(
      <AntApp>
        <ApplicationCommunicationDraftPanel
          application={application}
          snapshots={[snapshot]}
          outcomes={[]}
          events={[]}
        />
      </AntApp>,
    ));

    expect(document.body.textContent).toContain('跟进与感谢信');
    expect(button('面试感谢信').getAttribute('aria-disabled')).toBe('true');
    expect(button('投递跟进信').getAttribute('aria-pressed')).toBe('true');
    act(() => button('生成草稿').click());

    const subject = document.body.querySelector<HTMLInputElement>('input[aria-label="邮件主题"]');
    const body = document.body.querySelector<HTMLTextAreaElement>('textarea[aria-label="邮件正文"]');
    expect(subject?.value).toBe('关于「高级后端工程师」申请进展的跟进');
    expect(body?.value).toContain('2026年8月12日');
    expect(document.body.textContent).toContain('投递档案 #11');

    input('textarea[aria-label="邮件正文"]', '我编辑后的草稿');
    expect(body?.value).toBe('我编辑后的草稿');
    act(() => button('恢复系统草稿').click());
    expect(body?.value).toContain('想礼貌了解目前的招聘进展');

    await act(async () => { button('复制完整内容').click(); });
    expect(clipboardWrite).toHaveBeenCalledWith(expect.stringContaining('关于「高级后端工程师」申请进展的跟进'));
    expect(document.body.querySelector('[aria-live="polite"]')?.textContent).toBe('完整草稿已复制');
    input('textarea[aria-label="邮件正文"]', '复制后的新编辑');
    expect(document.body.querySelector('[aria-live="polite"]')?.textContent).toBe('');
    await act(async () => { button('复制完整内容').click(); });
    expect(clipboardWrite).toHaveBeenCalledTimes(2);
    expect(document.body.querySelector('[aria-live="polite"]')?.textContent).toBe('完整草稿已复制');
    clipboardWrite.mockRejectedValueOnce(new Error('permission denied'));
    await act(async () => { button('复制完整内容').click(); });
    expect(document.body.querySelector('[aria-live="polite"]')?.textContent).toBe('复制失败，请手动选择文字');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(historySpy).not.toHaveBeenCalled();
  });

  it('uses an explicit interview source and user-confirmed highlight for a thank-you draft', async () => {
    act(() => root.render(
      <AntApp>
        <ApplicationCommunicationDraftPanel
          application={application}
          snapshots={[snapshot]}
          outcomes={[outcome]}
          events={[event]}
        />
      </AntApp>,
    ));

    act(() => button('面试感谢信').click());
    expect(button('面试感谢信').getAttribute('aria-pressed')).toBe('true');
    expect(button('生成草稿').disabled).toBe(true);
    await selectOption('面试结果', '进入下一阶段');
    input('input[aria-label="收件人称呼"]', '林女士');
    input('textarea[aria-label="交流亮点"]', '服务稳定性与可观测性');
    act(() => button('生成草稿').click());

    expect(document.body.querySelector<HTMLInputElement>('input[aria-label="邮件主题"]')?.value)
      .toBe('感谢「高级后端工程师」面试交流');
    expect(document.body.querySelector<HTMLTextAreaElement>('textarea[aria-label="邮件正文"]')?.value)
      .toContain('林女士，您好：');
    expect(document.body.textContent).toContain('用户确认亮点');
    expect(document.body.textContent).toContain('第 2 轮面试');

    act(() => button('投递跟进信').click());
    act(() => button('生成草稿').click());
    expect(document.body.textContent).not.toContain('用户确认亮点');
    expect(document.body.textContent).not.toContain('第 2 轮面试');
    expect(document.body.textContent).not.toContain('结果 #41');
  });

  it('keeps generated edits in memory only and resets after remount', () => {
    const renderPanel = () => root.render(
      <AntApp>
        <ApplicationCommunicationDraftPanel
          application={application}
          snapshots={[snapshot]}
          outcomes={[]}
          events={[]}
        />
      </AntApp>,
    );
    act(renderPanel);
    act(() => button('生成草稿').click());
    input('textarea[aria-label="邮件正文"]', '只存在当前会话');
    act(() => root.unmount());
    container.remove();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(renderPanel);
    expect(document.body.querySelector('textarea[aria-label="邮件正文"]')).toBeNull();
  });

  it('invalidates a generated thank-you when its linked event becomes unavailable', async () => {
    const renderPanel = (events: ScheduleEvent[]) => root.render(
      <AntApp>
        <ApplicationCommunicationDraftPanel
          application={application}
          snapshots={[snapshot]}
          outcomes={[outcome]}
          events={events}
        />
      </AntApp>,
    );
    act(() => renderPanel([event]));
    act(() => button('面试感谢信').click());
    await selectOption('面试结果', '进入下一阶段');
    act(() => button('生成草稿').click());
    expect(document.body.querySelector('textarea[aria-label="邮件正文"]')).not.toBeNull();

    await act(async () => {
      renderPanel([{ ...event, status: 'cancelled' }]);
      await Promise.resolve();
    });
    expect(document.body.querySelector('textarea[aria-label="邮件正文"]')).toBeNull();
    expect(document.body.textContent).not.toContain('日程 #31');
  });
});
