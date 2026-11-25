// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import source from './InterviewReadinessCenter.tsx?raw';
import InterviewReadinessCenter from './InterviewReadinessCenter';
import { createResumeSelectionLease } from '@/features/interviewEvents/resumeSelectionLease';
import { getCurrentApplicationJd } from '@/services/applicationJdVersions';
import { listSubmissionSnapshots } from '@/services/applicationOutcomes';
import type { ApplicationSubmissionSnapshot } from '@/types/applicationOutcome';
import { createInterviewPracticeCase } from '@/services/interviewPracticeCases';

vi.mock('@/services/interviewPracticeCases', () => ({
  createInterviewPracticeCase: vi.fn(),
}));

vi.mock('@/services/applicationJdVersions', () => ({ getCurrentApplicationJd: vi.fn() }));
const currentJd = vi.mocked(getCurrentApplicationJd);

vi.mock('@/services/applicationOutcomes', () => ({ listSubmissionSnapshots: vi.fn() }));
const snapshots = vi.mocked(listSubmissionSnapshots);

const createPracticeCaseMock = vi.mocked(createInterviewPracticeCase);

const quickResume = { id: 11, title: '基础版', is_master: true, parent_resume_id: null, deleted_at: null };

function setInputValue(selector: string, value: string) {
  const element = document.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector);
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

async function selectQuickResume() {
  const select = document.querySelector<HTMLElement>('#quick-readiness-resume');
  if (!select) throw new Error('Missing quick resume select');
  const trigger = select.querySelector<HTMLElement>('.ant-select-selector') ?? select;
  await act(async () => {
    trigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    trigger.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  const option = [...document.body.querySelectorAll<HTMLElement>('[role="option"], .ant-select-item-option')]
    .find((item) => item.textContent?.includes('基础版'));
  if (!option) throw new Error('Missing quick resume option');
  await act(async () => {
    option.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    option.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

function quickStartButton(): HTMLButtonElement {
  const button = [...document.querySelectorAll<HTMLButtonElement>('button')]
    .find((item) => item.textContent?.includes('进入快速练习'));
  if (!button) throw new Error('Missing quick practice button');
  return button;
}

function renderQuickDraft(root: ReturnType<typeof createRoot>, resumes: unknown) {
  act(() => root.render(
    <InterviewReadinessCenter
      initialMode="quick"
      fixedMode="quick"
      resumes={resumes as never}
    />,
  ));
}

async function selectQuickDraft(root: ReturnType<typeof createRoot>) {
  renderQuickDraft(root, [quickResume]);
  setInputValue('input[placeholder="例如：后端工程师"]', '后端工程师');
  setInputValue('#quick-readiness-jd', '负责高并发 API 设计。');
  const checkbox = document.querySelector<HTMLInputElement>('input[type="checkbox"]');
  if (!checkbox) throw new Error('Missing JD confirmation checkbox');
  act(() => checkbox.click());
  await selectQuickResume();
  expect(quickStartButton().disabled).toBe(false);
}

beforeEach(() => {
  document.body.replaceChildren();
  createPracticeCaseMock.mockReset();
  snapshots.mockReset();
  currentJd.mockReset().mockRejectedValue(new Error('Current JD read unavailable'));
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  });
});

describe('InterviewReadinessCenter', () => {
  it('keeps JD confirmation in a single native label and toggles without nested label activation', () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    renderQuickDraft(root, [quickResume]);
    const checkbox = host.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    const label = checkbox.closest('label')!;
    expect(label.textContent).toContain('已核对，本次按此岗位资料练习');
    expect(label.parentElement?.closest('label')).toBeNull();
    act(() => label.click());
    expect(checkbox.checked).toBe(true);
    act(() => label.click());
    expect(checkbox.checked).toBe(false);
    expect(createPracticeCaseMock).not.toHaveBeenCalled();
    act(() => root.unmount());
  });

  it('does not expose an application or event picker for real preparation', () => {
    const markup = renderToStaticMarkup(
      <InterviewReadinessCenter initialMode="real" fixedMode="real" />,
    );

    expect(markup).toContain('面试准备');
    expect(markup).not.toContain('选择投递');
    expect(markup).not.toContain('选择面试事件');
    expect(markup).not.toContain('请选择投递');
    expect(markup).not.toContain('请选择已排期面试');
  });

  it('renders a locked real event and resolves a single visible resume through the lease', () => {
    const lease = createResumeSelectionLease(4);
    const markup = renderToStaticMarkup(
      <InterviewReadinessCenter
        initialMode="real"
        fixedMode="real"
        lockedEvent={{ applicationId: 7, eventId: 8, companyName: 'Acme', positionName: '工程师' }}
        resumes={[{ id: 11, deleted_at: null } as never]}
        resumeSelectionLease={lease}
        generation={4}
        jdSource={{ status: 'ready', id: 3, text: '负责平台稳定性。' }}
      />,
    );

    expect(markup).toContain('Acme');
    expect(markup).toContain('工程师');
    expect(markup).toContain('当前 JD（只读）');
    expect(markup).toContain('已选择一份已保存简历');
    expect(markup).not.toContain('选择投递');
    expect(markup).not.toContain('选择面试事件');
  });

  it('keeps unresolved or invalid resume sources unavailable and does not launch preparation', () => {
    const open = vi.fn();
    const markup = renderToStaticMarkup(
      <InterviewReadinessCenter
        initialMode="real"
        fixedMode="real"
        lockedEvent={{ applicationId: 7, eventId: 8 }}
        resumes={{ status: 'loading' }}
        jdSource={{ status: 'ready', id: 3, text: 'JD' }}
        onLaunchTask={open}
      />,
    );

    expect(markup).toContain('暂时不可用');
    expect(markup).toContain('开始准备');
    expect(markup).toContain('disabled=""');
    expect(open).not.toHaveBeenCalled();
  });

  it('fails closed when a resume source getter throws during projection', () => {
    const hostile = {
      status: 'ready',
      get value(): never { throw new Error('resume source unavailable'); },
    } as never;
    const markup = renderToStaticMarkup(
      <InterviewReadinessCenter
        initialMode="real"
        fixedMode="real"
        lockedEvent={{ applicationId: 7, eventId: 8 }}
        resumes={hostile}
        jdSource={{ status: 'ready', id: 3, text: 'JD' }}
      />,
    );

    expect(markup).toContain('暂时不可用');
    expect(markup).toContain('简历状态暂时无法确认');
  });

  it('does not label an invalid ready JD as executable preparation input', () => {
    const markup = renderToStaticMarkup(
      <InterviewReadinessCenter
        initialMode="real"
        fixedMode="real"
        lockedEvent={{ applicationId: 7, eventId: 8 }}
        resumes={[{ id: 11, deleted_at: null } as never]}
        jdSource={{ status: 'ready', id: 0, text: '' }}
      />,
    );

    expect(markup).toContain('暂时不可用');
    expect(markup).toContain('岗位资料暂时无法读取');
  });

  it('keeps quick practice as the only editable mode and does not create a case while opening', () => {
    expect(source).toContain('createInterviewPracticeCase');
    expect(source).toContain('onClick={() => {');
    expect(source).toContain('mode === \'quick\'');
    expect(source).not.toContain('onOpenMockInterview');
  });

  it('drops the executable resume when the explicit selection is cleared', () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: () => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    });
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    act(() => root.render(
      <InterviewReadinessCenter
        fixedMode="real"
        lockedEvent={{ applicationId: 7, eventId: 8 }}
        resumes={[
          { id: 11, title: '基础版', is_master: true, parent_resume_id: null, deleted_at: null } as never,
          { id: 12, title: '岗位版', is_master: false, parent_resume_id: 11, deleted_at: null } as never,
        ]}
        selectedResumeId={11}
        jdSource={{ status: 'ready', id: 3, text: 'JD' }}
      />,
    ));
    const start = [...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes('开始准备'));
    expect(start?.disabled).toBe(false);
    const clear = host.querySelector<HTMLElement>('.ant-select-clear');
    expect(clear).toBeTruthy();
    act(() => clear?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
    expect(start?.disabled).toBe(true);
    act(() => root.unmount());
    host.remove();
  });

  it.each([
    ['deleted', [{ ...quickResume, deleted_at: '2026-08-30T00:00:00Z' }]],
    ['hidden', [{ ...quickResume, hidden: true }]],
    ['empty', []],
  ] as const)('does not create a quick case when the selected resume becomes %s after selection', async (_label, nextResumes) => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    await selectQuickDraft(root);

    renderQuickDraft(root, nextResumes);
    await act(async () => {
      quickStartButton().click();
      await Promise.resolve();
    });

    expect(createPracticeCaseMock).not.toHaveBeenCalled();
    act(() => root.unmount());
    host.remove();
  });

  it.each(['loading', 'error', 'unknown', 'absent'] as const)('does not create a quick case for %s envelope carrying stale resume rows', async (status) => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    await selectQuickDraft(root);

    renderQuickDraft(root, { status, value: [quickResume] });
    await act(async () => {
      quickStartButton().click();
      await Promise.resolve();
    });

    expect(createPracticeCaseMock).not.toHaveBeenCalled();
    act(() => root.unmount());
    host.remove();
  });

  it.each([
    ['loading', '简历列表正在加载', '暂时未知'],
    ['error', '简历列表状态暂时无法确认', '暂时不可用'],
    ['unknown', '简历列表状态暂时无法确认', '暂时不可用'],
    ['absent', '简历列表尚未加载', '暂时不可用'],
  ] as const)('renders an explicit quick-practice resume source state for %s', (status, copy, statusCopy) => {
    const markup = renderToStaticMarkup(
      <InterviewReadinessCenter
        initialMode="quick"
        fixedMode="quick"
        resumes={{ status, value: [quickResume] }}
      />,
    );

    expect(markup).toContain(copy);
    expect(markup).toContain(statusCopy);
    expect(markup).not.toContain('将冻结当前已保存版本');
  });

  it('supports secondary embedding and reduced motion', async () => {
    const markup = renderToStaticMarkup(
      <InterviewReadinessCenter initialMode="quick" fixedMode="quick" actionEmphasis="secondary" resumes={[]} />,
    );
    const fsModule = 'node:fs';
    const { readFileSync } = (await import(fsModule)) as {
      readFileSync: (path: string | URL, encoding: string) => string;
    };
    const styles = readFileSync('src/features/interviewReadiness/InterviewReadinessCenter.module.css', 'utf8');

    expect(markup).toContain('data-readiness-mode="quick"');
    expect(markup).toContain('secondaryAction');
    expect(styles).toMatch(/prefers-reduced-motion:[^}]+reduce[\s\S]*transform:\s*none/);
  });
});


describe('saved submission materials in locked readiness', () => {
  const archivedJd = `Archived first line
${'archived full text '.repeat(320)}
Archived final line`;
  const record = (overrides: Partial<ApplicationSubmissionSnapshot> = {}): ApplicationSubmissionSnapshot => ({
    id: 52, application_id: 7, resume_id: 50, resume_title: 'Do not label from live resume',
    jd_version_id: 4, jd_version_number: 1, material_kit_id: null,
    resume_snapshot: { experience: [{ company: 'Frozen employer', highlights: ['Frozen nested achievement'] }],
      skills: ['Frozen skill'], raw_text: 'Untrusted <img src=x> raw extraction' },
    jd_snapshot: archivedJd, material_snapshot: null, note: '', source_kind: 'ui',
    source_states: { resume: 'changed', jd: 'changed', material: 'missing' },
    submitted_at: '2026-11-01T10:00:00Z', created_at: '2026-11-02T10:00:00Z',
    ...overrides,
  });

  const button = (host: HTMLElement, title: string) => [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((candidate) => candidate.textContent?.trim() === title)!;
  const archive = (host: HTMLElement) => host.querySelector<HTMLElement>('[data-testid="saved-submission-materials"]')!;
  async function click(host: HTMLElement, title: string) { await act(async () => button(host, title).click()); }
  function choose(host: HTMLElement, id: string) {
    const select = archive(host).querySelector('select')!;
    act(() => { select.value = id; select.dispatchEvent(new Event('change', { bubbles: true })); });
  }
  const open = '查看已保存的投递材料';
  const close = '收起已保存的投递材料';

  function mount() {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    const launch = vi.fn();
    const render = (applicationId = 7, eventId = 8, generation = 1, useLiveJd = false) => act(() => root.render(
      <InterviewReadinessCenter fixedMode="real"
        lockedEvent={{ applicationId, eventId }} generation={generation}
        resumes={[{ id: 11, deleted_at: null } as never]} selectedResumeId={11}
        jdSource={useLiveJd ? undefined : { status: 'ready', id: 3, text: 'Live current JD' }}
        onLaunchTask={launch} />,
    ));
    return { host, render, launch, unmount: () => act(() => root.unmount()) };
  }

  it('reads only after opening; even a singleton needs explicit selection, and full frozen inputs never become current inputs', async () => {
    snapshots.mockResolvedValue([record()]);
    const { host, render, launch, unmount } = mount();
    render();
    expect(snapshots).not.toHaveBeenCalled();
    expect(button(host, open).getAttribute('aria-expanded')).toBe('false');
    await click(host, open);
    expect(snapshots).toHaveBeenCalledTimes(1);
    expect(snapshots).toHaveBeenCalledWith(7, expect.any(AbortSignal));
    expect(archive(host).querySelector('select')?.value).toBe('');
    expect(archive(host).textContent).not.toContain('Frozen nested achievement');
    expect(archive(host).textContent).not.toContain('Do not label from live resume');
    choose(host, '52');
    expect(archive(host).textContent).toContain('Frozen nested achievement');
    expect(archive(host).textContent).toContain('Frozen skill');
    expect(archive(host).querySelector('pre')?.textContent).toBe(archivedJd);
    const raw = archive(host).querySelector('details');
    expect(raw?.textContent).toContain('Untrusted <img src=x> raw extraction');
    expect(raw?.open).toBe(false);
    expect(raw?.querySelector('img')).toBeNull();
    expect(host.textContent).toContain('Live current JD');
    expect(launch).not.toHaveBeenCalled();
    await click(host, '开始准备↗');
    expect(launch.mock.calls[0][0]).toMatchObject({ ref: { applicationId: 7, eventId: 8 }, hints: { suggestedResumeId: 11 } });
    await click(host, close);
    expect(archive(host).querySelector('article')).toBeNull();
    await click(host, open);
    expect(archive(host).querySelector('select')?.value).toBe('');
    unmount();
  });

  it('suppresses a closed, switched event, switched application, or replaced owner response even if fetch ignores abort', async () => {
    const pending: Array<(rows: ApplicationSubmissionSnapshot[]) => void> = [];
    snapshots.mockImplementation(() => new Promise(resolve => pending.push(resolve)));
    const { host, render, unmount } = mount();
    render();
    await click(host, open);
    await click(host, close);
    await click(host, open);
    await act(async () => pending[0]([record()]));
    expect(archive(host).textContent).toContain('正在读取');
    expect(archive(host).querySelector('select')).toBeNull();
    render(7, 9);
    expect(button(host, open).getAttribute('aria-expanded')).toBe('false');
    await click(host, open);
    render(8, 9);
    await click(host, open);
    render(8, 9, 2);
    for (const resolve of pending.slice(1)) await act(async () => resolve([record()]));
    expect(button(host, open).getAttribute('aria-expanded')).toBe('false');
    expect(host.textContent).not.toContain('Frozen employer');
    expect(snapshots.mock.calls.map(([id]) => id)).toEqual([7, 7, 7, 8]);
    expect(snapshots.mock.calls.every(([, signal]) => signal?.aborted)).toBe(true);
    unmount();
  });

  it('ignores foreign or duplicate records and distinguishes legacy/blank, empty and retryable failures', async () => {
    snapshots.mockRejectedValueOnce(new Error('permission failure')).mockResolvedValueOnce([])
      .mockResolvedValueOnce([record({ id: 53, application_id: 8 }), record({ id: 54 }), record({ id: 54 }),
        record({ resume_snapshot: { experience: 'legacy prose', raw_text: 'only raw' }, jd_snapshot: '' })]);
    const { host, render, unmount } = mount();
    render();
    await click(host, open);
    expect(archive(host).querySelector('[role="alert"]')?.textContent).toContain('暂时无法读取');
    await click(host, '重试');
    expect(archive(host).textContent).toContain('还没有已保存的材料');
    await click(host, close);
    await click(host, open);
    expect([...archive(host).querySelectorAll('option')].map(({ value }) => value)).toEqual(['', '52']);
    choose(host, '52');
    expect(archive(host).textContent).toContain('没有可展示的结构化简历');
    expect(archive(host).querySelector('details')?.textContent).toContain('legacy prose');
    expect(archive(host).textContent).toContain('only raw');
    expect(archive(host).textContent).toContain('未保存岗位描述');
    unmount();
  });

  it('shows a genuine empty legacy résumé with a saved JD but cannot substitute for a failed current JD read', async () => {
    snapshots.mockResolvedValue([record({ resume_snapshot: {} })]);
    const { host, render, launch, unmount } = mount();
    await act(async () => render(7, 8, 1, true));
    expect(currentJd).toHaveBeenCalledWith(7);
    expect(host.textContent).toContain('岗位资料暂时无法读取');
    expect(host.textContent).toContain('已选择一份已保存简历');
    expect(button(host, '开始准备↗').disabled).toBe(true);
    await click(host, open);
    choose(host, '52');
    expect(archive(host).textContent).toContain('没有可展示的结构化简历');
    expect(archive(host).querySelector('pre')?.textContent).toBe(archivedJd);
    expect(button(host, '开始准备↗').disabled).toBe(true);
    expect(launch).not.toHaveBeenCalled();
    unmount();
  });

  it('does not expose archive or fetch records in quick practice, even with locked event props', () => {
    const markup = renderToStaticMarkup(<InterviewReadinessCenter initialMode="quick" fixedMode="quick"
      lockedEvent={{ applicationId: 7, eventId: 8 }} />);
    expect(markup).not.toContain('saved-submission-materials');
    expect(snapshots).not.toHaveBeenCalled();
  });
});
