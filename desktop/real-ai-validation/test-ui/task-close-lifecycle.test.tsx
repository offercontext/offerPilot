// Real ApplicationDetail, controller, Host, proposal/editor components and the
// pinned Playwright selector engine. jsdom does not animate CSS: dispatch the
// owner's native animationend explicitly, rather than claiming Windows E2E.
import React, { act, useCallback, useState, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { Modal } from '../../../web/node_modules/antd';
declare const __PLAYWRIGHT_BUNDLE__: string;

const mocks = vi.hoisted(() => ({
  preparation: vi.fn(), history: vi.fn(), advisory: vi.fn(),
  resumePreview: vi.fn(), resumeConfirm: vi.fn(), resumeUpdate: vi.fn(),
  offerCreate: vi.fn(), offerPreview: vi.fn(), offerConfirm: vi.fn(), offerGet: vi.fn(),
  events: [] as unknown[], empty: [] as unknown[], jd: null as unknown,
  guard: vi.fn(), attempt: vi.fn(), draft: vi.fn(), offerDraft: vi.fn(),
}));
// Keep the real close authority and guard calculation; replace only transport
// and unrelated owners. No product lifecycle or Ant component is mocked.
vi.mock('../../../web/node_modules/@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => ({
    data: queryKey[0] === 'events' ? mocks.events
      : queryKey[0] === 'application-jd-current' ? mocks.jd
        : queryKey[0] === 'application-material-kit' ? null : mocks.empty,
    isLoading: false, isFetching: false, isError: false,
  }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('@/components/MaterialKitDrawer', () => ({ default: () => null }));
vi.mock('@/components/OpportunityFitReviewDrawer', () => ({ default: () => null }));
vi.mock('@/components/ScheduleEventForm', () => ({ default: () => null }));
vi.mock('@/components/ReviewFormDrawer', () => ({ default: () => null }));
vi.mock('@/components/InterviewReviewProposalDrawer', () => ({ default: () => null }));
vi.mock('@/components/ApplicationOutcomeDrawer', () => ({ default: () => null }));
vi.mock('@/services/interviewPreparationProposals', () => ({
  createInterviewPreparationProposal: mocks.preparation,
  listInterviewPreparationProposals: mocks.history,
  InterviewPreparationProposalError: class extends Error {},
}));
vi.mock('@/features/reviewReadiness/service', () => ({ getEventReadinessFeedback: mocks.advisory }));
vi.mock('@/services/resumes', () => ({
  previewResumeStructure: mocks.resumePreview, confirmResumeStructure: mocks.resumeConfirm,
  updateResume: mocks.resumeUpdate, getResume: vi.fn(),
}));
vi.mock('@/services/offers', () => ({
  createOfferNegotiationProposal: mocks.offerCreate, previewOfferNegotiation: mocks.offerPreview,
  confirmOfferNegotiationProposal: mocks.offerConfirm, getOfferNegotiationProposal: mocks.offerGet,
  listOfferNegotiationProposals: async () => [], listOfferComparisonDimensions: async () => [],
  listOfferComparisonValues: async () => [], OfferNegotiationError: class extends Error {},
}));

import ApplicationDetail from '../../../web/src/components/ApplicationDetail';
import ResumeEditorDrawer from '../../../web/src/components/ResumeEditorDrawer';
import { createCoreTaskSurfaceController, type CoreTaskSurfaceController } from '../../../web/src/features/coreTaskSurface/controller';
import type { InterviewPreparationAttemptState, InterviewPreparationDraft } from '../../../web/src/components/InterviewPreparationProposalDrawer';
import type { OfferNegotiationDraft } from '../../../web/src/components/OfferNegotiationDrawer';
import type { Application } from '../../../web/src/types/application';
import type { Resume } from '../../../web/src/types/resume';
import type { Offer } from '../../../web/src/types/offer';

const now = Date.parse('2026-10-08T00:00:00Z');
const application = { id: 1, company_name: '合成验收公司', position_name: '合成工程师',
  status: 'interview', source: 'manual', notes: '', applied_at: '2026-10-08T00:00:00Z',
  created_at: '2026-10-08T00:00:00Z', updated_at: '2026-10-08T00:00:00Z' } as Application;
const resume = { id: 3, title: '合成验收简历', name: '合成验收简历', source: 'upload',
  is_master: false, parent_resume_id: null, content_json: { raw_text: '姓名：合成候选人' },
  parsed_data: '姓名：合成候选人', parse_status: 'parsed', created_at: '2026-10-08T00:00:00Z',
  deleted_at: null, completion_percent: 0, missing_sections: [] } as unknown as Resume;
const offer = { id: 4, application_id: 1, company_name: application.company_name,
  position_name: application.position_name, status: 'pending', base_monthly: 20000,
  months_per_year: 12, signing_bonus: 0, total_cash: 240000 } as Offer;
const resumes = [resume];
const offers = [offer];
const interviewRequest = { ref: { taskId: 'application.interview_prepare' as const, applicationId: 1, eventId: 2 }, source: 'interview_event_card' as const };
const offerRequest = { ref: { taskId: 'application.offer_review' as const, applicationId: 1 }, source: 'application_header' as const };
const generated = { proposal_status: 'normal', proposal: {
  preparation_directions: [{ id: 'd1', text: '合成准备方向', evidence_refs: [] }],
  story_prompts: [], review_points: [], interviewer_questions: [], items_to_clarify: [],
} };

function Owner({ controller, openPilot }: { controller: CoreTaskSurfaceController; openPilot?: () => boolean }) {
  const state = useSyncExternalStore(controller.subscribe, controller.getState, controller.getState);
  const [attempts, setAttempts] = useState<Record<string, InterviewPreparationAttemptState>>({});
  const [drafts, setDrafts] = useState<Record<string, InterviewPreparationDraft>>({});
  const [offerDrafts, setOfferDrafts] = useState<Record<number, OfferNegotiationDraft>>({});
  const saveAttempt = useCallback((key: string, value: InterviewPreparationAttemptState | null) => {
    mocks.attempt(key, value);
    setAttempts(previous => update(previous, key, value));
  }, []);
  const saveDraft = useCallback((key: string, value: InterviewPreparationDraft | null) => {
    mocks.draft(key, value);
    setDrafts(previous => update(previous, key, value));
  }, []);
  const saveOfferDraft = useCallback((key: number, value: OfferNegotiationDraft | null) => {
    mocks.offerDraft(key, value);
    setOfferDrafts(previous => update(previous, key, value));
  }, []);
  return <ApplicationDetail application={application} open onClose={() => {}}
    taskController={controller} taskNow={now} resumes={resumes} offers={offers}
    interviewPreparationSelection={{ generation: state.generation, applicationId: 1, eventId: 2, resumeId: 3 }}
    interviewPreparationAttempts={attempts} onInterviewPreparationAttemptChange={saveAttempt}
    interviewPreparationDrafts={drafts} onInterviewPreparationDraftChange={saveDraft}
    offerNegotiationDrafts={offerDrafts} onOfferNegotiationDraftChange={saveOfferDraft}
    onOpenOfferNegotiationPilot={openPilot} onTaskSurfaceGuardChange={mocks.guard} />;
}

// Persist drafts without creating a render loop from unchanged callbacks.
// The real ApplicationDetail, not this wrapper, computes the close guard.
function update<T>(previous: Record<string, T>, key: string | number, value: T | null): Record<string, T> {
  if (JSON.stringify(previous[key] ?? null) === JSON.stringify(value)) return previous;
  const next = { ...previous };
  if (value === null) delete next[key]; else next[key] = value;
  return next;
}

let host: HTMLDivElement, root: ReturnType<typeof createRoot>, engine: any;
const role = (name: string, text: string) => `internal:role=${name}[name=${JSON.stringify(text)}s]`;
const query = (selector: string, scope: any = document): HTMLElement[] => engine.querySelectorAll(engine.parseSelector(selector), scope);
const one = (selector: string, scope: any = document): HTMLElement => {
  const nodes = query(selector, scope); expect(nodes).toHaveLength(1); return nodes[0];
};
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const click = async (node: HTMLElement) => { await act(async () => node.click()); await flush(); };
const finishAnimation = async (node: HTMLElement) => { await act(async () => node.dispatchEvent(new Event('animationend', { bubbles: true }))); };
const fill = async (node: HTMLTextAreaElement | HTMLInputElement, text: string) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(node, text);
    node.dispatchEvent(new Event('input', { bubbles: true }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
  });
};
async function openOwner(request = interviewRequest as typeof interviewRequest | typeof offerRequest, openPilot?: () => boolean) {
  const controller = createCoreTaskSurfaceController();
  controller.launch(request);
  await act(async () => root.render(<Owner controller={controller} openPilot={openPilot} />));
  await flush();
  const owner = one('[data-core-task-owner]');
  await finishAnimation(owner);
  expect(controller.getState().phase).toBe('open');
  return { controller, owner, generation: controller.getState().generation };
}
function expectClosing(controller: CoreTaskSurfaceController, owner: HTMLElement, innerSelector: string) {
  expect(controller.getState().phase).toBe('closing');
  expect(query(innerSelector)).toHaveLength(0);
  expect(one('[data-core-task-owner]')).toBe(owner);
  expect(one(role('button', '关闭任务'), owner).isConnected).toBe(true);
  expect(owner.className).toContain('closing');
}
async function expectDetached(controller: CoreTaskSurfaceController, owner: HTMLElement) {
  await finishAnimation(owner);
  expect(controller.getState()).toMatchObject({ phase: 'closed', active: null });
  expect(owner.isConnected).toBe(false);
  expect(query('[data-core-task-owner]')).toHaveLength(0);
  expect(query(role('button', '关闭任务'))).toHaveLength(0);
}

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  vi.stubEnv('NODE_ENV', 'development');
  Object.defineProperty(window, 'CSS', { configurable: true, value: { escape: (id: string) => [...id].map(c => '\\' + c.codePointAt(0)!.toString(16) + ' ').join('') } });
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const style = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => style.call(window, element));
  mocks.events = [{ id: 2, application_id: 1, event_type: 'interview', status: 'scheduled',
    scheduled_at: '2026-10-09T00:00:00Z', duration_minutes: 45, subtype: '', tags: [], notes: '' }];
  mocks.jd = { current: { id: 5, application_id: 1, jd_text: '合成 JD' } };
  mocks.preparation.mockResolvedValue(generated);
  mocks.history.mockResolvedValue([]);
  mocks.advisory.mockResolvedValue({ schema_version: 1, application_id: 1, event_id: 2, items: [] });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  const bundle = readFileSync(__PLAYWRIGHT_BUNDLE__, 'utf8');
  const section = bundle.slice(bundle.indexOf('// packages/playwright-core/src/generated/injectedScriptSource.ts'));
  const source = runInNewContext(section.match(/source\d+ = ('(?:\\.|[^'])*');/)![1]);
  engine = window.eval(`(()=>{const module={exports:{}};${source};return new InjectedScript(window,{isUnderTest:true,sdkLanguage:'javascript',testIdAttributeName:'data-testid',customEngines:[],stableRafCount:1,browserName:'chromium',frameSeq:1});})()`);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove(); Modal.destroyAll(); vi.restoreAllMocks(); vi.unstubAllEnvs();
});

it('generated interview closes its region before the real animated owner and task button detach', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  const { controller, owner } = await openOwner();
  const surface = one(role('region', '面试准备建议'));
  await click(one('[data-testid="interview-preparation-generate"]', surface));
  one(role('heading', '准备方向'), surface);
  expect(mocks.preparation).toHaveBeenCalledOnce();
  await click(one(role('button', '关闭'), surface));
  expectClosing(controller, owner, role('region', '面试准备建议'));
  // This is the exact premature completion boundary used by runInterview:
  // the next leaveTask can still observe an actionable outer close button.
  const observedByNextCase = one(role('button', '关闭任务'));
  await finishAnimation(observedByNextCase); // Bubbled child events cannot detach the owner.
  expect(controller.getState().phase).toBe('closing');
  await expectDetached(controller, owner);
  expect(observedByNextCase.isConnected).toBe(false);
  expect(confirm).toHaveBeenCalledTimes(1); // Generation disclosure only, no close confirmation.
});

it('pending interview close keeps its frozen attempt and recovery certificate across normal owner detach and reopen', async () => {
  let resolve!: (value: unknown) => void;
  mocks.preparation.mockReturnValue(new Promise(done => { resolve = done; }));
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  const { controller, owner, generation } = await openOwner();
  const surface = one(role('region', '面试准备建议'));
  await fill(one(role('textbox', '可选用户断言（不会发送给 AI）'), surface) as HTMLTextAreaElement, '本次合成补充');
  await click(one('[data-testid="interview-preparation-generate"]', surface));
  const attemptKey = mocks.preparation.mock.calls[0][0].idempotency_key;
  expect(mocks.guard).toHaveBeenLastCalledWith({ pending: true, unsaved: true });
  await click(one(role('button', '关闭'), surface));
  expectClosing(controller, owner, role('region', '面试准备建议'));
  expect(mocks.attempt).toHaveBeenLastCalledWith('1:2', { key: attemptKey, result_unknown: true });
  expect(mocks.draft.mock.calls.at(-1)?.[1]).toMatchObject({ attemptState: { key: attemptKey, result_unknown: true }, assertionsText: '本次合成补充', resumeId: 3, jdVersionId: 5 });
  await expectDetached(controller, owner);
  await act(async () => { controller.launch(interviewRequest); });
  expect(controller.getState().active?.recoveryGeneration).toBe(generation);
  expect((one('[data-testid="interview-preparation-resume-select"]') as HTMLSelectElement).disabled).toBe(true);
  expect((one(role('textbox', '可选用户断言（不会发送给 AI）')) as HTMLTextAreaElement).value).toBe('本次合成补充');
  // A late response belongs to the closed generation; it must not erase the
  // certificate, thaw the reopened draft, or fabricate a completed proposal.
  await act(async () => resolve(generated)); await flush();
  expect(controller.getState().active?.recoveryGeneration).toBe(generation);
  expect(query(role('heading', '准备方向'))).toHaveLength(0);
  expect((one('[data-testid="interview-preparation-resume-select"]') as HTMLSelectElement).disabled).toBe(true);
  expect(confirm).toHaveBeenCalledTimes(1);
});

it('unsaved interview closes without a discard prompt and restores the actual draft and recovery identity', async () => {
  const confirm = vi.spyOn(window, 'confirm');
  const { controller, owner, generation } = await openOwner();
  await fill(one(role('textbox', '可选用户断言（不会发送给 AI）')) as HTMLTextAreaElement, '尚未生成的补充');
  expect(mocks.guard).toHaveBeenLastCalledWith({ pending: false, unsaved: true });
  await click(one(role('button', '关闭任务')));
  expectClosing(controller, owner, role('region', '面试准备建议'));
  await expectDetached(controller, owner);
  await act(async () => { controller.launch(interviewRequest); });
  expect(controller.getState().active?.recoveryGeneration).toBe(generation);
  expect((one(role('textbox', '可选用户断言（不会发送给 AI）')) as HTMLTextAreaElement).value).toBe('尚未生成的补充');
  expect(confirm).not.toHaveBeenCalled();
  expect(mocks.preparation).not.toHaveBeenCalled();
});

it('Offer cancellation has the same owner tail and preserves the unconfirmed draft for reopening', async () => {
  const confirm = vi.spyOn(window, 'confirm');
  const { controller, owner, generation } = await openOwner(offerRequest);
  const surface = one('[data-testid="offer-negotiation-drawer"]');
  await fill(one(role('textbox', '本次沟通目标'), surface) as HTMLTextAreaElement, '核对合成薪资');
  expect(mocks.guard).toHaveBeenLastCalledWith({ pending: false, unsaved: true });
  await click(one(role('button', '关闭任务')));
  expectClosing(controller, owner, '[data-testid="offer-negotiation-drawer"]');
  await expectDetached(controller, owner);
  await act(async () => { controller.launch(offerRequest); });
  expect(controller.getState().active?.recoveryGeneration).toBe(generation);
  expect((one(role('textbox', '本次沟通目标')) as HTMLTextAreaElement).value).toBe('核对合成薪资');
  expect(mocks.offerConfirm).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
});

it('generated Offer close leaves its save unsubmitted while the outer owner finishes closing', async () => {
  const snapshot = { snapshot_version: 1, offer_snapshot: { ...offer, dimensions: [] },
    user_brief: { goal: '合成目标', concerns: '合成顾虑', scenario: '合成场景' } };
  mocks.offerPreview.mockResolvedValue({ source_fingerprint: 'synthetic', snapshot });
  mocks.offerCreate.mockResolvedValue({ id: 7, offer_id: 4, application_id: 1,
    attempt_status: 'ready', proposal_status: 'normal', source_changed: false, input_snapshot: snapshot,
    proposal: { proposal_status: 'normal', communication_goals: [], clarification_questions: [], talking_points: [], preparation_checks: [] } });
  const { controller, owner } = await openOwner(offerRequest);
  const surface = one('[data-testid="offer-negotiation-drawer"]');
  for (const [label, text] of [['本次沟通目标', '合成目标'], ['本次顾虑', '合成顾虑'], ['沟通场景', '合成场景']]) {
    await fill(one(role('textbox', label), surface) as HTMLTextAreaElement, text);
  }
  await click(one(role('button', '下一步：检查输入'), surface));
  one(role('region', '确认本次 AI 输入'), surface);
  await click(one(role('button', '确认生成谈薪准备草稿'), surface));
  one('[aria-label="谈薪准备草稿"]', surface);
  one('[data-testid="offer-negotiation-confirm"]', surface);
  expect(mocks.offerCreate).toHaveBeenCalledOnce();
  // Generation has completed and is retained by the service. The product
  // clears the local generation draft; that is not final-save confirmation.
  expect(mocks.guard).toHaveBeenLastCalledWith({ pending: false, unsaved: false });
  expect(mocks.offerDraft).toHaveBeenLastCalledWith(4, null);
  await click(one(role('button', '关闭任务')));
  expectClosing(controller, owner, '[data-testid="offer-negotiation-drawer"]');
  await expectDetached(controller, owner);
  expect(mocks.offerConfirm).not.toHaveBeenCalled();
});

it('Offer-to-Pilot refusal keeps the owner; accepted handoff still needs owner animation completion', async () => {
  const openPilot = vi.fn().mockReturnValue(false);
  const { controller, owner } = await openOwner(offerRequest, openPilot);
  const open = one('[data-testid="offer-negotiation-open-pilot"]');
  await click(open);
  expect(openPilot).toHaveBeenCalledOnce();
  expect(controller.getState().phase).toBe('open');
  one('[data-testid="offer-negotiation-drawer"]');
  openPilot.mockReturnValue(true);
  await click(open);
  expectClosing(controller, owner, '[data-testid="offer-negotiation-drawer"]');
  await expectDetached(controller, owner);
  expect(mocks.offerConfirm).not.toHaveBeenCalled();
});

it('resume classification cancel and clean editor return detach directly, without a core-task closing tail', async () => {
  const confirm = vi.spyOn(Modal, 'confirm');
  const saved = vi.fn();
  const original = JSON.stringify(resume.content_json);
  mocks.resumePreview.mockResolvedValue({ resume_id: 3, source_fingerprint: 'synthetic',
    fields: [{ path: 'contact.name', value: '合成候选人', evidence: '姓名：合成候选人' }] });
  function Editor() {
    const [open, setOpen] = useState(true);
    return <ResumeEditorDrawer resume={resume} open={open} onClose={() => setOpen(false)} onSaved={saved} />;
  }
  await act(async () => root.render(<Editor />)); await flush();
  const editor = one(role('region', '编辑简历'));
  await click(one(role('button', 'AI 分类并核对'), editor));
  const modal = one(role('dialog', 'AI 简历分类与核对'));
  await click(one(role('button', '开始分类'), modal));
  one(role('region', '分类候选'), modal);
  await click(one('button >> internal:has-text=/取\\s*消/', modal));
  expect(query(role('dialog', 'AI 简历分类与核对'))).toHaveLength(0);
  expect(modal.isConnected).toBe(false);
  expect(one(role('region', '编辑简历'))).toBe(editor);
  await click(one(role('button', '返回简历库'), editor));
  expect(editor.isConnected).toBe(false);
  expect(query('[data-core-task-owner]')).toHaveLength(0);
  expect(query(role('button', '关闭任务'))).toHaveLength(0);
  expect(mocks.resumeConfirm).not.toHaveBeenCalled();
  expect(mocks.resumeUpdate).not.toHaveBeenCalled();
  expect(saved).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
  expect(JSON.stringify(resume.content_json)).toBe(original);
});

it('dirty resume return keeps the real editor behind its existing confirmation and never auto-discards changes', async () => {
  const confirm = vi.spyOn(Modal, 'confirm');
  const close = vi.fn();
  await act(async () => root.render(<ResumeEditorDrawer resume={resume} open onClose={close} />));
  await flush();
  const editor = one(role('region', '编辑简历'));
  const title = one('input[placeholder="简历标题"]', editor) as HTMLInputElement;
  await fill(title, '尚未保存的合成标题');
  await click(one(role('button', '返回简历库'), editor));
  expect(confirm).toHaveBeenCalledOnce();
  const prompt = one(role('dialog', '有未保存的更改'));
  expect(prompt.textContent).toContain('离开后，本次编辑内容不会保存。');
  one(role('button', '放弃更改'), prompt);
  expect(editor.isConnected).toBe(true);
  expect(close).not.toHaveBeenCalled();
  await click(one(role('button', '继续编辑'), prompt));
  expect(close).not.toHaveBeenCalled();
  expect(one(role('region', '编辑简历'))).toBe(editor);
  expect(title.value).toBe('尚未保存的合成标题');
  expect(mocks.resumeUpdate).not.toHaveBeenCalled();
  expect(mocks.resumeConfirm).not.toHaveBeenCalled();
  expect(resume.title).toBe('合成验收简历');
});
