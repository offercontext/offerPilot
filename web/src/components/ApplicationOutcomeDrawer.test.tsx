// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
const getComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element: Element) => getComputedStyle(element);
if (!globalThis.crypto.randomUUID) {
  Object.defineProperty(globalThis.crypto, 'randomUUID', {
    value: () => '12345678-1234-4234-8234-123456789abc',
  });
}

const state = vi.hoisted(() => ({
  createSnapshot: vi.fn(),
  createOutcome: vi.fn(),
  listSnapshots: vi.fn(),
  listOutcomes: vi.fn(),
  getSummary: vi.fn(),
  getMaterialKit: vi.fn(),
  getStrategy: vi.fn(),
  listStrategyArchives: vi.fn(),
  writeStrategy: vi.fn(),
  listApplications: vi.fn(),
}));

vi.mock('@/services/applicationOutcomes', () => ({
  createSubmissionSnapshot: state.createSnapshot,
  createApplicationOutcome: state.createOutcome,
  listSubmissionSnapshots: state.listSnapshots,
  listApplicationOutcomes: state.listOutcomes,
  getApplicationOutcomeSummary: state.getSummary,
}));
vi.mock('@/services/applicationStrategy', () => ({
  getStrategy: state.getStrategy, listStrategyArchives: state.listStrategyArchives, writeStrategy: state.writeStrategy,
}));
vi.mock('@/services/applications', () => ({ listApplications: state.listApplications }));
vi.mock('@/services/materialKits', () => ({ getApplicationMaterialKit: state.getMaterialKit }));

const { default: ApplicationOutcomeDrawer } = await import('./ApplicationOutcomeDrawer');

const application = {
  id: 7,
  company_name: '云栖智能',
  position_name: 'AI 应用工程师',
} as never;
const resume = {
  id: 2,
  title: '筱哲 · AI 应用工程师版',
  name: '筱哲',
  deleted_at: null,
} as never;
const currentJd = {
  id: 3,
  application_id: 7,
  version_number: 2,
  jd_text: '负责企业级 AI 应用研发与知识库质量建设。',
} as never;
const frozenSnapshot = {
  id: 11,
  application_id: 7,
  resume_id: 2,
  resume_title: '筱哲 · AI 应用工程师版',
  jd_version_id: 3,
  jd_version_number: 2,
  material_kit_id: null,
  resume_snapshot: {},
  jd_snapshot: '负责企业级 AI 应用研发与知识库质量建设。',
  material_snapshot: null,
  note: '官网投递',
  source_kind: 'ui',
  source_states: { resume: 'current', jd: 'current', material: 'missing' },
  submitted_at: '2026-08-12T09:00:00Z',
  created_at: '2026-08-12T09:00:00Z',
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 40));
  });
}

beforeEach(() => {
  sessionStorage.clear();
  state.createSnapshot.mockReset().mockResolvedValue(frozenSnapshot);
  state.createOutcome.mockReset().mockResolvedValue({ id: 21 });
  state.listSnapshots.mockReset().mockResolvedValue([frozenSnapshot]);
  state.listOutcomes.mockReset().mockResolvedValue([]);
  state.getSummary.mockReset().mockResolvedValue({
    total: 0,
    stage_counts: {},
    result_counts: {},
    feedback_tag_counts: {},
    next_actions_pending: 0,
  });
  state.getMaterialKit.mockReset().mockResolvedValue(null);
  state.getStrategy.mockReset();
  state.listStrategyArchives.mockReset();
  state.writeStrategy.mockReset();
  state.listApplications.mockReset().mockResolvedValue([]);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  document.body.replaceChildren();
});

describe('ApplicationOutcomeDrawer', () => {
  it('mounts a read-only communication draft from the already loaded archive', async () => {
    const interviewEvent = {
      id: 31,
      application_id: 7,
      event_type: 'interview',
      round: 2,
      scheduled_at: '2026-08-15T10:30:00+08:00',
    } as never;
    state.listOutcomes.mockResolvedValue([{ id: 41, application_id: 7, submission_snapshot_id: 11, application_event_id: 31, stage: 'interview', result: 'advanced', feedback_text: '', reflection_text: '', next_action_text: '', feedback_tags: [], source_kind: 'ui', occurred_at: '2026-08-15T11:40:00+08:00', created_at: '2026-08-15T11:40:00+08:00' }]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => root?.render(
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <ApplicationOutcomeDrawer
            application={application}
            open
            onClose={vi.fn()}
            resumes={[resume]}
            currentJd={currentJd}
            events={[interviewEvent]}
          />
        </AntApp>
      </QueryClientProvider>,
    ));
    await settle();

    expect(document.body.textContent).toContain('跟进与感谢信');
    const generate = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('生成草稿'));
    expect(generate).not.toBeUndefined();
    act(() => (generate as HTMLButtonElement).click());

    expect(document.body.querySelector<HTMLInputElement>('input[aria-label="邮件主题"]')?.value)
      .toContain('申请进展');
    expect(state.createSnapshot).not.toHaveBeenCalled();
    expect(state.createOutcome).not.toHaveBeenCalled();
  });

  it('renders the frozen archive and sends a deterministic Pilot action without writing', async () => {
    const onAskPilot = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => root?.render(
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <ApplicationOutcomeDrawer
            application={application}
            open
            onClose={vi.fn()}
            resumes={[resume]}
            currentJd={currentJd}
            events={[]}
            onAskPilot={onAskPilot}
          />
        </AntApp>
      </QueryClientProvider>,
    ));
    await settle();

    expect(document.body.textContent).toContain('云栖智能 · 投递事实与结果');
    expect(document.body.textContent).toContain('筱哲 · AI 应用工程师版');
    expect(document.body.textContent).toContain('简历 · 当前');
    expect(document.body.textContent).toContain('材料 · 未包含');

    const pilotButtons = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .filter((button) => button.textContent?.includes('交给 Pilot 确认'));
    expect(pilotButtons).toHaveLength(2);
    act(() => pilotButtons[0].click());
    await settle();

    expect(onAskPilot).toHaveBeenCalledWith(application, expect.objectContaining({
      type: 'application_submission_snapshot',
      resumeId: 2,
      jdVersionId: 3,
      materialKitId: null,
    }));
    expect(state.createSnapshot).not.toHaveBeenCalled();
    expect(state.createOutcome).not.toHaveBeenCalled();
  });

  it('freezes an unknown direct write and retries with the original idempotency key', async () => {
    state.createSnapshot
      .mockRejectedValueOnce(Object.assign(new Error('network lost'), { isAxiosError: true }))
      .mockResolvedValueOnce(frozenSnapshot);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => root?.render(
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <ApplicationOutcomeDrawer
            application={application}
            open
            onClose={vi.fn()}
            resumes={[resume]}
            currentJd={currentJd}
            events={[]}
          />
        </AntApp>
      </QueryClientProvider>,
    ));
    await settle();

    const save = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('直接保存档案'));
    expect(save).not.toBeUndefined();
    act(() => (save as HTMLButtonElement).click());
    await settle();

    expect(document.body.textContent).toContain('保存结果待确认');
    const retry = [...document.body.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('使用原尝试重试'));
    expect(retry).not.toBeUndefined();
    act(() => (retry as HTMLButtonElement).click());
    await settle();

    expect(state.createSnapshot).toHaveBeenCalledTimes(2);
    expect(state.createSnapshot.mock.calls[1][1]).toEqual(state.createSnapshot.mock.calls[0][1]);
    expect([...document.body.querySelectorAll('button')].some((button) => button.textContent?.includes('使用原尝试重试'))).toBe(false);
    expect(sessionStorage.getItem('offerpilot:application-outcome:7:snapshot')).toBeNull();
  });
});

it('shows the exact archive and round in history and retries the same strategy command after response loss', async () => {
  const later = { ...frozenSnapshot, id: 12, submitted_at: '2026-08-12T14:00:00Z' };
  const rejected = {
    id: 50, application_id: 7, submission_snapshot_id: 12, application_event_id: null,
    stage: 'screening', result: 'rejected', feedback_text: '', reflection_text: '',
    next_action_text: '量化可核实的项目结果', feedback_tags: [], source_kind: 'ui',
    occurred_at: '2026-08-14T10:00:00Z', created_at: '2026-08-14T10:00:00Z',
  };
  const roundOne = { id: 71, application_id: 7, event_type: 'interview', round: 1, scheduled_at: '2026-08-15T10:00:00Z' } as never;
  const roundTwo = { id: 72, application_id: 7, event_type: 'interview', round: 2, scheduled_at: '2026-08-15T15:00:00Z' } as never;
  const interview = {
    ...rejected, id: 51, stage: 'interview', result: 'advanced', application_event_id: 72,
    occurred_at: '2026-08-15T16:00:00Z', next_action_text: '面试反馈标记',
  };
  state.listSnapshots.mockResolvedValue([later, frozenSnapshot]);
  state.listOutcomes.mockResolvedValue([interview, rejected]);
  const empty = { origin: { ...rejected, has_external_feedback: false }, change: null, target: null, comparable_outcomes: [] };
  state.getStrategy.mockResolvedValue(empty);
  state.writeStrategy.mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({
    ...empty, change: { revision: 0, proposal: rejected.next_action_text, applied_detail: null, decision: null, decided_outcome_id: null },
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  act(() => root?.render(<QueryClientProvider client={queryClient}>
    <AntApp><ApplicationOutcomeDrawer application={application} open onClose={vi.fn()}
      resumes={[resume]} currentJd={currentJd} events={[roundOne, roundTwo]} /></AntApp>
  </QueryClientProvider>));
  await settle();
  const card = [...document.querySelectorAll('article')].find(item => item.textContent?.includes('面试反馈标记'));
  expect(card?.textContent).toContain('第 2 轮面试');
  expect(card?.textContent).toContain('日程 #72');
  expect(card?.textContent).toContain('档案 #12');
  expect(card?.textContent).not.toContain('日程 #71');

  const click = (label: string) => {
    const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.trim() === label);
    expect(button).toBeDefined();
    act(() => button?.click());
  };
  expect(state.getStrategy).not.toHaveBeenCalled();
  click('把改法带到下一次投递');
  await settle();
  expect(state.getStrategy).toHaveBeenCalledWith(7, 50, expect.anything());
  expect(state.writeStrategy).not.toHaveBeenCalled();
  expect(document.querySelector<HTMLTextAreaElement>('[aria-label="下一次的具体改法"]')?.value).toBe(rejected.next_action_text);
  click('保存改法');
  await settle();
  click('重试同一请求');
  await settle();
  expect(state.writeStrategy).toHaveBeenCalledTimes(2);
  expect(state.writeStrategy.mock.calls[0]).toEqual(state.writeStrategy.mock.calls[1]);
  expect(state.writeStrategy.mock.calls[0]).toEqual([7, 50, expect.objectContaining({ action: 'propose', proposal: rejected.next_action_text })]);
  expect(document.body.textContent).toContain('只有已在某份投递实际使用，才确认归档');
  expect(state.createOutcome).not.toHaveBeenCalled();
  expect(state.createSnapshot).not.toHaveBeenCalled();
});
