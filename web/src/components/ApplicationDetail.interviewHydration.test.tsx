// @vitest-environment jsdom
import { act, useCallback, useState, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Application } from '@/types/application';
import type { CurrentApplicationJd } from '@/types/applicationJdVersion';
import type { Resume } from '@/types/resume';
import type { ScheduleEvent } from '@/types/event';
import type { InterviewPreparationDraft } from './InterviewPreparationProposalDrawer';
import { createCoreTaskSurfaceController, type CoreTaskSurfaceController } from '@/features/coreTaskSurface/controller';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const services = vi.hoisted(() => ({
  currentJd: vi.fn(),
  events: vi.fn(),
  createPreparation: vi.fn(),
  listPreparations: vi.fn(),
  readiness: vi.fn(),
  draftChanged: vi.fn(),
  notes: vi.fn(),
  materialKit: vi.fn(),
  fitReviews: vi.fn(),
}));

vi.mock('@/services/applicationJdVersions', () => ({
  getCurrentApplicationJd: services.currentJd,
  getApplicationJdVersion: vi.fn(),
  listApplicationJdVersions: vi.fn().mockResolvedValue([]),
  saveApplicationJdVersion: vi.fn(),
}));
vi.mock('@/services/events', () => ({ listEvents: services.events }));
vi.mock('@/services/notes', () => ({
  listNotesByApp: services.notes,
  createNote: vi.fn(), deleteNote: vi.fn(), updateNote: vi.fn(),
}));
vi.mock('@/services/materialKits', () => ({ getApplicationMaterialKit: services.materialKit }));
vi.mock('@/services/opportunityFitReviews', () => ({ listOpportunityFitV2Reviews: services.fitReviews }));
vi.mock('@/services/interviewPreparationProposals', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/interviewPreparationProposals')>(),
  createInterviewPreparationProposal: services.createPreparation,
  listInterviewPreparationProposals: services.listPreparations,
}));
vi.mock('@/features/reviewReadiness/service', () => ({ getEventReadinessFeedback: services.readiness }));
vi.mock('./PilotAttachmentHandle', () => ({ createPilotAttachmentDragBinding: () => ({}) }));
vi.mock('./ScheduleEventForm', () => ({ default: () => null }));
vi.mock('./ReviewFormDrawer', () => ({ default: () => null }));
vi.mock('./InterviewReviewProposalDrawer', () => ({ default: () => null }));
vi.mock('./InterviewKnowledgeCaptureDrawer', () => ({ default: () => null, createInterviewKnowledgeCaptureDraft: () => ({}) }));
vi.mock('./MaterialKitDrawer', () => ({ default: () => null }));
vi.mock('./OpportunityFitReviewDrawer', () => ({ default: () => null }));
vi.mock('./ApplicationOutcomeDrawer', () => ({ default: () => null }));
vi.mock('./OfferNegotiationDrawer', () => ({ default: () => null }));
vi.mock('./NextStepSuggestions', () => ({ default: () => null }));
vi.mock('@ant-design/icons', () => ({
  ArrowLeftOutlined: () => null, CalendarOutlined: () => null,
  PlusOutlined: () => null, MoreOutlined: () => null,
  FileTextOutlined: () => null, HistoryOutlined: () => null, EditOutlined: () => null,
}));
vi.mock('antd', () => ({
  Typography: {
    Title: (props: { children?: ReactNode }) => <h2>{props.children}</h2>,
    Paragraph: (props: { children?: ReactNode }) => <p>{props.children}</p>,
    Text: (props: { children?: ReactNode }) => <span>{props.children}</span>,
  },
  Button: (props: { children?: ReactNode; onClick?: () => void; disabled?: boolean; 'data-testid'?: string }) => (
    <button type="button" onClick={props.onClick} disabled={props.disabled} data-testid={props['data-testid']}>{props.children}</button>
  ),
  Input: Object.assign(
    (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
    { TextArea: (props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...props} /> },
  ),
  Alert: (props: { message?: ReactNode; action?: ReactNode }) => <div role="alert">{props.message}{props.action}</div>,
  Empty: (props: { description?: ReactNode }) => <div>{props.description}</div>,
  Spin: () => <span>loading</span>,
  Tag: (props: { children?: ReactNode }) => <span>{props.children}</span>,
  Space: (props: { children?: ReactNode }) => <div>{props.children}</div>,
  Popconfirm: (props: { children?: ReactNode }) => <>{props.children}</>,
  Dropdown: (props: { children?: ReactNode }) => <>{props.children}</>,
  Modal: (props: { open?: boolean; children?: ReactNode }) => props.open ? <div role="dialog">{props.children}</div> : null,
  Timeline: () => null,
  message: { success: vi.fn(), error: vi.fn() },
}));

const { default: ApplicationDetail } = await import('./ApplicationDetail');

const application: Application = {
  id: 7, company_name: '示例公司', position_name: '后端工程师',
  job_url: '', status: 'interview', source: 'manual', notes: '',
  applied_at: '2026-10-01T00:00:00Z', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
};
const resume: Resume = {
  id: 13, name: '后端简历', title: '后端简历', file_path: '', parsed_data: '',
  parse_status: 'ready', is_master: true, parent_resume_id: null, source: 'manual',
  source_file_path: '', content_json: {}, deleted_at: null, created_at: '2026-10-01T00:00:00Z',
  completion_percent: 100, missing_sections: [], is_complete: true,
};
const taskNow = Date.parse('2026-10-11T08:00:00Z');

function interview(applicationId: number, eventId: number): ScheduleEvent {
  return {
    id: eventId, application_id: applicationId, event_type: 'interview', subtype: 'technical',
    tags: [], round: 1, scheduled_at: '2026-10-12T08:00:00Z', duration_minutes: 60,
    location: '', notes: '', status: 'scheduled', created_at: '2026-10-01T00:00:00Z',
  };
}
function savedJd(applicationId = 7, versionId = 71, text = '负责设计可靠的 API 服务。'): CurrentApplicationJd {
  return { current: {
    id: versionId, application_id: applicationId, version_number: 1, jd_text: text,
    content_sha256: 'a'.repeat(64), source_url: null, source_kind: 'ui',
    utf8_byte_length: new TextEncoder().encode(text).length, preview: text, created_at: '2026-10-01T00:00:00Z',
  } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}
function proposalResult() {
  return {
    id: 90, application_id: 7, event_id: 31, resume_id: 13,
    attempt_status: 'ready', proposal_status: 'safe_empty', source_status: 'current',
    proposal: { preparation_directions: [], story_prompts: [], review_points: [], interviewer_questions: [], items_to_clarify: [] },
  };
}

let root: Root;
let container: HTMLDivElement;
let queryClient: QueryClient;
let controller: CoreTaskSurfaceController;
let drafts: Record<string, InterviewPreparationDraft>;

function Harness({ selectedApplication = application, eventId = 31, initialDrafts = {} }: {
  selectedApplication?: Application;
  eventId?: number;
  initialDrafts?: Record<string, InterviewPreparationDraft>;
}) {
  const [ownerDrafts, setOwnerDrafts] = useState(initialDrafts);
  drafts = ownerDrafts;
  const persistDraft = useCallback((key: string, draft: InterviewPreparationDraft | null) => {
    services.draftChanged(key, draft);
    setOwnerDrafts((current) => {
      if (JSON.stringify(current[key] ?? null) === JSON.stringify(draft)) return current;
      const next = { ...current };
      if (draft) next[key] = draft;
      else delete next[key];
      return next;
    });
  }, []);
  return <QueryClientProvider client={queryClient}>
    <ApplicationDetail
      application={selectedApplication} open onClose={() => {}} initialTab="preparation"
      taskController={controller} taskNow={taskNow} resumes={[resume]} offers={[]}
      interviewPreparationSelection={{
        generation: controller.getState().active!.generation,
        applicationId: selectedApplication.id, eventId, resumeId: resume.id,
      }}
      interviewPreparationDrafts={ownerDrafts}
      onInterviewPreparationDraftChange={persistDraft}
    />
  </QueryClientProvider>;
}

async function flushQueries() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}
async function renderOwner(selectedApplication = application, eventId = 31, initialDrafts = {}) {
  await act(async () => {
    const launched = controller.launch({
      ref: { taskId: 'application.interview_prepare', applicationId: selectedApplication.id, eventId },
      source: 'application_task_card',
    });
    expect(['launched', 'focused_existing']).toContain(launched.kind);
    root.render(<Harness selectedApplication={selectedApplication} eventId={eventId} initialDrafts={initialDrafts} />);
  });
  await flushQueries();
}
function jdInput() {
  return container.querySelector<HTMLTextAreaElement>('textarea[aria-readonly="true"]');
}
function generateButton() {
  return container.querySelector<HTMLButtonElement>('[data-testid="interview-preparation-generate"]');
}
function assertionsInput() {
  return container.querySelector<HTMLTextAreaElement>('textarea[placeholder="每行一条本次准备的补充信息"]');
}
function setAssertions(value: string) {
  const textarea = assertionsInput();
  expect(textarea).not.toBeNull();
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, value);
    textarea!.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function resolveJd(request: ReturnType<typeof deferred<CurrentApplicationJd>>, value: CurrentApplicationJd) {
  await act(async () => { request.resolve(value); await request.promise; });
  await flushQueries();
}

beforeEach(() => {
  vi.clearAllMocks();
  services.currentJd.mockReset();
  services.events.mockImplementation(({ application_id }: { application_id: number }) => Promise.resolve([
    interview(application_id, application_id === 7 ? 31 : 41),
    interview(application_id, application_id === 7 ? 32 : 42),
  ]));
  services.listPreparations.mockResolvedValue([]);
  services.createPreparation.mockResolvedValue(proposalResult());
  services.readiness.mockResolvedValue({ schema_version: 1, items: [] });
  services.notes.mockResolvedValue([]);
  services.materialKit.mockResolvedValue(null);
  services.fitReviews.mockResolvedValue([]);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  controller = createCoreTaskSurfaceController();
  drafts = {};
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  container.remove();
  vi.restoreAllMocks();
});

describe('ApplicationDetail interview JD query hydration', () => {
  it('uses the late current JD text and version on the first open, without reopening the owner', async () => {
    const request = deferred<CurrentApplicationJd>();
    services.currentJd.mockReturnValue(request.promise);
    await renderOwner();
    const generation = controller.getState().active!.generation;
    expect(services.currentJd).toHaveBeenCalledWith(application.id);
    expect(generateButton()?.disabled ?? true).toBe(true);
    expect(services.createPreparation).not.toHaveBeenCalled();

    await resolveJd(request, savedJd());

    expect(queryClient.getQueryData(['application-jd-current', application.id])).toEqual(savedJd());
    expect(controller.getState().active!.generation).toBe(generation);
    expect(jdInput()?.value).toBe(savedJd().current!.jd_text);
    expect(generateButton()?.disabled).toBe(false);
    expect(drafts['7:31']).toMatchObject({ jdText: savedJd().current!.jd_text, jdVersionId: 71, resumeId: 13 });
    await act(async () => { generateButton()!.click(); });
    expect(services.createPreparation).toHaveBeenCalledTimes(1);
    expect(services.createPreparation).toHaveBeenCalledWith(expect.objectContaining({ application_id: 7, event_id: 31, resume_id: 13, jd_version_id: 71 }));
  });

  it.each(['failed', 'empty'] as const)('cannot generate when the current JD query is %s', async (outcome) => {
    const request = deferred<CurrentApplicationJd>();
    services.currentJd.mockReturnValue(request.promise);
    await renderOwner();
    if (outcome === 'empty') await resolveJd(request, { current: null });
    else {
      await act(async () => { request.reject(new Error('JD unavailable')); await request.promise.catch(() => undefined); });
      await flushQueries();
    }
    expect(queryClient.getQueryState(['application-jd-current', 7])?.status).toBe(outcome === 'failed' ? 'error' : 'success');
    expect(generateButton()?.disabled ?? true).toBe(true);
    await act(async () => { generateButton()?.click(); });
    expect(services.createPreparation).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it('recovers from a failed current JD query on the same open owner', async () => {
    const firstRequest = deferred<CurrentApplicationJd>();
    const retryRequest = deferred<CurrentApplicationJd>();
    services.currentJd.mockReturnValueOnce(firstRequest.promise).mockReturnValueOnce(retryRequest.promise);
    await renderOwner();
    const generation = controller.getState().active!.generation;
    await act(async () => { firstRequest.reject(new Error('Temporary failure')); await firstRequest.promise.catch(() => undefined); });
    await flushQueries();
    expect(generateButton()?.disabled ?? true).toBe(true);
    act(() => { void queryClient.refetchQueries({ queryKey: ['application-jd-current', 7] }); });
    await resolveJd(retryRequest, savedJd());

    expect(controller.getState().active!.generation).toBe(generation);
    expect(jdInput()?.value).toBe(savedJd().current!.jd_text);
    expect(generateButton()?.disabled).toBe(false);
    expect(drafts['7:31']).toMatchObject({ jdVersionId: 71, jdText: savedJd().current!.jd_text });
  });

  it('ignores an old application query that settles after the replacement application has loaded', async () => {
    const oldRequest = deferred<CurrentApplicationJd>();
    const newRequest = deferred<CurrentApplicationJd>();
    services.currentJd.mockImplementation((applicationId: number) => applicationId === 7 ? oldRequest.promise : newRequest.promise);
    await renderOwner();
    const oldDraft = drafts['7:31'];
    await renderOwner({ ...application, id: 8, company_name: '另一家公司' }, 41);
    const generation = controller.getState().active!.generation;
    await resolveJd(newRequest, savedJd(8, 81, '新投递的岗位职责。'));
    expect(jdInput()?.value).toBe('新投递的岗位职责。');
    services.draftChanged.mockClear();

    await resolveJd(oldRequest, savedJd(7, 71, '旧投递的迟到岗位职责。'));

    expect(queryClient.getQueryData(['application-jd-current', 7])).toEqual(savedJd(7, 71, '旧投递的迟到岗位职责。'));
    expect(controller.getState().active!.generation).toBe(generation);
    expect(jdInput()?.value).toBe('新投递的岗位职责。');
    expect(drafts['8:41']).toMatchObject({ jdText: '新投递的岗位职责。', jdVersionId: 81 });
    expect(drafts['7:31']).toEqual(oldDraft);
    expect(services.draftChanged.mock.calls.every(([key]) => key === '8:41')).toBe(true);
    await act(async () => { generateButton()!.click(); });
    expect(services.createPreparation).toHaveBeenCalledWith(expect.objectContaining({ application_id: 8, event_id: 41, jd_version_id: 81 }));
  });

  it('hydrates only the active event draft when the event changes before the shared JD query resolves', async () => {
    const request = deferred<CurrentApplicationJd>();
    services.currentJd.mockReturnValue(request.promise);
    const firstEventDraft: InterviewPreparationDraft = {
      attemptState: { key: 'first-event-attempt', result_unknown: false },
      resumeId: 13, jdText: '', jdVersionId: null, assertionsText: '只属于第一场面试的补充信息', knowledgeSelections: [],
    };
    await renderOwner(application, 31, { '7:31': firstEventDraft });
    await renderOwner(application, 32);
    const previousDraft = drafts['7:31'];
    const generation = controller.getState().active!.generation;
    services.draftChanged.mockClear();
    await resolveJd(request, savedJd());

    expect(controller.getState().active!.generation).toBe(generation);
    expect(jdInput()?.value).toBe(savedJd().current!.jd_text);
    expect(assertionsInput()?.value).toBe('');
    expect(drafts['7:31']).toEqual(previousDraft);
    expect(drafts['7:32']).toMatchObject({ jdVersionId: 71, jdText: savedJd().current!.jd_text, assertionsText: '' });
    expect(drafts['7:32'].attemptState.key).not.toBe('first-event-attempt');
    expect(services.draftChanged.mock.calls.every(([key]) => key === '7:32')).toBe(true);
    await act(async () => { generateButton()!.click(); });
    expect(services.createPreparation).toHaveBeenCalledWith(expect.objectContaining({ application_id: 7, event_id: 32, jd_version_id: 71 }));
  });

  it('keeps the hydrated version and edited assertions through a background JD refresh', async () => {
    const refreshRequest = deferred<CurrentApplicationJd>();
    services.currentJd.mockResolvedValueOnce(savedJd()).mockReturnValueOnce(refreshRequest.promise);
    await renderOwner();
    expect(jdInput()?.value).toBe(savedJd().current!.jd_text);
    setAssertions('复习事务一致性');
    expect(drafts['7:31'].assertionsText).toBe('复习事务一致性');
    const textarea = jdInput();
    const generation = controller.getState().active!.generation;
    act(() => { void queryClient.invalidateQueries({ queryKey: ['application-jd-current', 7] }); });
    await flushQueries();
    expect(queryClient.getQueryState(['application-jd-current', 7])?.fetchStatus).toBe('fetching');
    expect(jdInput()).toBe(textarea);

    await resolveJd(refreshRequest, savedJd(7, 72, '后台读取到的新岗位职责。'));

    expect(queryClient.getQueryData(['application-jd-current', 7])).toEqual(savedJd(7, 72, '后台读取到的新岗位职责。'));
    expect(controller.getState().active!.generation).toBe(generation);
    expect(jdInput()).toBe(textarea);
    expect(jdInput()?.value).toBe(savedJd().current!.jd_text);
    expect(assertionsInput()?.value).toBe('复习事务一致性');
    expect(drafts['7:31']).toMatchObject({ jdVersionId: 71, jdText: savedJd().current!.jd_text, assertionsText: '复习事务一致性' });
    await act(async () => { generateButton()!.click(); });
    expect(services.createPreparation).toHaveBeenCalledWith(expect.objectContaining({ jd_version_id: 71, user_assertions: ['复习事务一致性'] }));
  });
});
