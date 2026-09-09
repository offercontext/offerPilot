// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationReadinessContext } from '@/services/conversationReadiness';

const service = vi.hoisted(() => ({
  read: vi.fn(),
  options: vi.fn(),
  confirm: vi.fn(),
  clear: vi.fn(),
}));

vi.mock('@/services/conversationReadiness', () => ({
  getConversationReadinessContext: service.read,
  getConversationReadinessOptions: service.options,
  confirmConversationReadiness: service.confirm,
  clearConversationReadiness: service.clear,
}));

const { default: ConversationReadinessControl } = await import('./ConversationReadinessControl');

const baseContext = {
  schema_version: 1 as const,
  state: 'not_applicable' as const,
  conversation_id: 7,
  application_id: 11,
  target_event_id: null,
  resume_id: null,
  ordered_version_ids: [],
  selection_fingerprint: '',
  scope_revision: 0,
  revision: 0,
};
const events = [{
  id: 101,
  application_id: 11,
  event_type: 'interview' as const,
  subtype: '技术面',
  tags: [],
  round: 1,
  scheduled_at: '2026-09-10T09:00:00Z',
  duration_minutes: 60,
  location: '',
  notes: '',
  status: 'todo',
  created_at: '2026-09-09T00:00:00Z',
}];
const resumes = [{
  id: 201,
  name: '后端简历',
  file_path: '',
  parsed_data: '',
  parse_status: 'done',
  title: '后端简历',
  is_master: true,
  parent_resume_id: null,
  source: 'manual' as const,
  source_file_path: '',
  content_json: {},
  deleted_at: null,
  created_at: '2026-09-09T00:00:00Z',
  completion_percent: 100,
  missing_sections: [],
  is_complete: true,
}];
const readiness = [{
  signalId: 301,
  versionId: 401,
  practiceSourceFingerprint: `sha256:${'a'.repeat(64)}`,
  practiceTargetFingerprint: `sha256:${'b'.repeat(64)}`,
  state: 'available' as const,
  practiceState: 'not_started' as const,
  selected: false,
  title: '拆解追问',
  sourceLabel: '第 1 轮复盘',
}];

function settle() {
  return act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function openSelect(container: HTMLDivElement, label: string) {
  const input = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  expect(input).not.toBeNull();
  act(() => input?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
}

function chooseOption(label: string) {
  const option = [...document.querySelectorAll<HTMLElement>('.ant-select-item-option')]
    .find((item) => item.textContent?.includes(label));
  expect(option).not.toBeUndefined();
  act(() => option?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
}

function openReadinessPanel() {
  const toggle = container?.querySelector<HTMLButtonElement>('#conversation-readiness-title');
  expect(toggle).not.toBeNull();
  expect(toggle?.getAttribute('aria-expanded')).toBe('false');
  act(() => toggle?.click());
  expect(toggle?.getAttribute('aria-expanded')).toBe('true');
}

let root: Root | undefined;
let container: HTMLDivElement | undefined;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined }),
  });
  service.read.mockReset().mockResolvedValue(baseContext);
  service.options.mockReset().mockResolvedValue({ events, resumes, readiness: [] });
  service.confirm.mockReset().mockResolvedValue({
    ...baseContext,
    state: 'confirmed',
    target_event_id: 101,
    resume_id: 201,
    ordered_version_ids: [401],
    revision: 1,
  });
  service.clear.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  document.body.querySelectorAll('.ant-select-dropdown').forEach((node) => node.remove());
  vi.restoreAllMocks();
});

describe('ConversationReadinessControl', () => {
  it('requires explicit target, resume, and readiness choices', async () => {
    service.options.mockImplementation(async (_applicationId: number, targetEventId?: number) => ({
      events,
      resumes,
      readiness: targetEventId ? readiness : [],
    }));
    act(() => root?.render(<ConversationReadinessControl conversationId={7} applicationId={11} />));
    await settle();

    expect(service.options).toHaveBeenCalledWith(11, undefined);
    expect(container?.querySelector('#conversation-readiness-title')?.textContent).toContain('本次面试的准备重点');
    expect(container?.querySelector('#conversation-readiness-title')?.getAttribute('aria-expanded')).toBe('false');
    expect(container?.querySelector<HTMLButtonElement>('button.ant-btn-primary')).toBeNull();
    expect(container?.querySelector('input[type="checkbox"]')).toBeNull();

    openReadinessPanel();
    expect(container?.querySelector<HTMLButtonElement>('button.ant-btn-primary')?.disabled).toBe(true);
    openSelect(container!, '目标面试');
    chooseOption('技术面');
    await settle();
    expect(service.options).toHaveBeenLastCalledWith(11, 101);
    expect(container?.textContent).toContain('拆解追问');

    openSelect(container!, '本次使用的简历');
    chooseOption('后端简历');
    await settle();
    const checkbox = container?.querySelector<HTMLInputElement>('input[type="checkbox"]');
    expect(checkbox).not.toBeNull();
    act(() => checkbox?.click());
    await settle();
    act(() => container?.querySelector<HTMLButtonElement>('button.ant-btn-primary')?.click());
    await settle();

    expect(service.confirm).toHaveBeenCalledWith(7, expect.objectContaining({
      target_event_id: 101,
      resume_id: 201,
      ordered_version_ids: [401],
      confirmed: true,
    }));
  });

  it('replays an existing binding and ignores late results from a prior conversation', async () => {
    let resolveFirst: ((value: ConversationReadinessContext) => void) | undefined;
    service.read
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({
        ...baseContext,
        conversation_id: 8,
        application_id: 22,
      });
    service.options.mockImplementation(async (applicationId: number, targetEventId?: number) => ({
      events: applicationId === 22 ? [{ ...events[0], application_id: 22, id: 202, subtype: '终面' }] : events,
      resumes,
      readiness: targetEventId ? readiness : [],
    }));
    act(() => root?.render(<ConversationReadinessControl conversationId={7} applicationId={11} />));
    act(() => root?.render(<ConversationReadinessControl conversationId={8} applicationId={22} />));
    resolveFirst?.({
      ...baseContext,
      state: 'confirmed' as const,
      target_event_id: 101,
      resume_id: 201,
      ordered_version_ids: [401],
      revision: 3,
    });
    await settle();

    expect(service.options).toHaveBeenCalledWith(22, undefined);
    expect(service.options).not.toHaveBeenCalledWith(22, 101);
    openReadinessPanel();
    openSelect(container!, '目标面试');
    const optionLabels = [...document.querySelectorAll<HTMLElement>('.ant-select-item-option')]
      .map((item) => item.textContent);
    expect(optionLabels.some((label) => label?.includes('终面'))).toBe(true);
    expect(optionLabels.some((label) => label?.includes('技术面'))).toBe(false);
    expect(container?.querySelector('input[type="checkbox"]')).toBeNull();
  });

  it('keeps edits and confirmation disabled until the initial context is known', async () => {
    let resolveContext: ((value: ConversationReadinessContext) => void) | undefined;
    service.read.mockImplementationOnce(() => new Promise((resolve) => { resolveContext = resolve; }));
    service.options.mockResolvedValue({ events, resumes, readiness });
    act(() => root?.render(<ConversationReadinessControl conversationId={7} applicationId={11} />));
    openReadinessPanel();
    await settle();

    expect(container?.querySelector('.ant-select-disabled')).not.toBeNull();
    expect(container?.querySelector<HTMLButtonElement>('button.ant-btn-primary')?.disabled).toBe(true);

    resolveContext?.(baseContext);
    await settle();
    expect(container?.querySelector('.ant-select-disabled')).toBeNull();
  });

  it('does not allow confirmation after the context read fails', async () => {
    service.read.mockRejectedValueOnce(new Error('offline'));
    service.options.mockResolvedValue({ events, resumes, readiness });
    act(() => root?.render(<ConversationReadinessControl conversationId={7} applicationId={11} />));
    await settle();
    openReadinessPanel();
    await settle();

    expect(container?.textContent).toContain('本次面试的准备重点读取失败');
    expect(container?.querySelector<HTMLButtonElement>('button.ant-btn-primary')?.disabled).toBe(true);
    expect(service.confirm).not.toHaveBeenCalled();
  });
});
