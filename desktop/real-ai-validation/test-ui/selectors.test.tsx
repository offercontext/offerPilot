// Real pinned product React/Ant components + the locked Playwright selector
// engine in jsdom. This proves DOM contracts, never installed Windows E2E.
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
declare const __PLAYWRIGHT_BUNDLE__: string;
const mocks = vi.hoisted(() => ({ interviews: vi.fn(), resumePreview: vi.fn(), resumeConfirm: vi.fn(),
  preparation: vi.fn(), history: vi.fn(), advisory: vi.fn(), offerCreate: vi.fn(), offerPreview: vi.fn() }));
vi.mock('@/features/pilotMascot/live2dRuntime', () => ({ live2dPilotMascotRuntime: { mount: async () => ({ setActivity() {}, dispose() {} }) } }));
vi.mock('@/services/interviews', () => ({ listInterviews: mocks.interviews }));
vi.mock('@/services/applicationJdVersions', () => ({ getCurrentApplicationJd: async () => ({ current: { id: 5, jd_text: 'synthetic JD' } }) }));
vi.mock('@/services/interviewPracticeCases', () => ({ createInterviewPracticeCase: vi.fn() }));
vi.mock('@/services/interviewPreparationProposals', () => ({ createInterviewPreparationProposal: mocks.preparation,
  listInterviewPreparationProposals: mocks.history, InterviewPreparationProposalError: class extends Error {} }));
vi.mock('@/features/reviewReadiness/service', () => ({ getEventReadinessFeedback: mocks.advisory }));
vi.mock('@/services/resumes', () => ({ previewResumeStructure: mocks.resumePreview, confirmResumeStructure: mocks.resumeConfirm, getResume: vi.fn() }));
vi.mock('@/services/offers', () => ({ createOfferNegotiationProposal: mocks.offerCreate, previewOfferNegotiation: mocks.offerPreview,
  listOfferNegotiationProposals: async () => [], listOfferComparisonDimensions: async () => [], listOfferComparisonValues: async () => [],
  confirmOfferNegotiationProposal: vi.fn(), getOfferNegotiationProposal: vi.fn(), OfferNegotiationError: class extends Error {} }));
import InterviewV01View from '../../../web/src/components/InterviewV01View';
import InterviewReadinessCenter from '../../../web/src/features/interviewReadiness/InterviewReadinessCenter';
import InterviewPreparationProposalDrawer from '../../../web/src/components/InterviewPreparationProposalDrawer';
import ResumeCard from '../../../web/src/components/ResumeCard';
import ResumeImportReview from '../../../web/src/components/ResumeImportReview';
import OfferCard from '../../../web/src/components/OfferCard';
import OfferNegotiationDrawer from '../../../web/src/components/OfferNegotiationDrawer';
import DesktopHaruWindow from '../../../web/src/features/assistantSurface/DesktopHaruWindow';

let host: HTMLDivElement, root: ReturnType<typeof createRoot>, engine: any;
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); };
const role = (r: string, name: string) => `internal:role=${r}[name=${JSON.stringify(name)}s]`;
const query = (selector: string, scope: any = document) => engine.querySelectorAll(engine.parseSelector(selector), scope);
const one = (selector: string, scope: any = document): HTMLElement => { const nodes = query(selector, scope); expect(nodes).toHaveLength(1); return nodes[0]; };
const click = async (node: HTMLElement) => { await act(async () => { node.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); node.click(); }); await flush(); };
const value = async (label: string, text: string, scope = document as any) => {
  const node = one(`internal:label=${JSON.stringify(label)}s`, scope) as HTMLInputElement;
  await act(async () => { const proto = node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(node, text);
    node.dispatchEvent(new Event('input', { bubbles: true })); node.dispatchEvent(new Event('change', { bubbles: true })); });
};
const resume = { id: 3, title: 'OfferPilot 合成验收简历', source: 'upload', is_master: false, parent_resume_id: null,
  content_json: { raw_text: '姓名：合成候选人' }, created_at: '2026-10-08T00:00:00Z', deleted_at: null } as any;
const offer = { id: 4, application_id: 1, company_name: 'OfferPilot 合成验收公司', position_name: '合成工程师',
  status: 'pending', base_monthly: 20000, months_per_year: 12, signing_bonus: 0, total_cash: 240000 } as any;
const event = { id: 2, application_id: 1, status: 'scheduled', event_type: 'interview' } as any;
const indexRow = { application_id: 1, event_id: 2, company_name: offer.company_name, position_name: offer.position_name,
  scheduled_at: new Date(Date.now() + 86400000).toISOString(), note_id: null, note_source_status: null,
  has_review_proposal: false, review_summary: null, has_confirmed_knowledge: false, preparation_available: true,
  event_status: 'scheduled', duration_minutes: 45, scheduled_at_state: 'present' };
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  // rc-util otherwise gives every React control the same test-id; use its
  // normal unique React useId branch for accessible-name contract tests.
  vi.stubEnv('NODE_ENV', 'development');
  // jsdom lacks CSS.escape used by Playwright's aria-labelledby resolver.
  // Hex-escape every code point of these synthetic IDs, without changing DOM.
  Object.defineProperty(window, 'CSS', { configurable: true, value: { escape: (id: string) => [...id].map(c => '\\' + c.codePointAt(0)!.toString(16) + ' ').join('') } });
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const style = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => style.call(window, element));
  vi.spyOn(console, 'error').mockImplementation(() => {});
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  const bundle = readFileSync(__PLAYWRIGHT_BUNDLE__, 'utf8');
  const section = bundle.slice(bundle.indexOf('// packages/playwright-core/src/generated/injectedScriptSource.ts'));
  const source = runInNewContext(section.match(/source\d+ = ('(?:\\.|[^'])*');/)![1]);
  engine = window.eval(`(()=>{const module={exports:{}};${source};return new InjectedScript(window,{isUnderTest:true,sdkLanguage:'javascript',testIdAttributeName:'data-testid',customEngines:[],stableRafCount:1,browserName:'chromium',frameSeq:1});})()`);
  mocks.interviews.mockResolvedValue({ items: [indexRow], next_cursor: null });
  mocks.history.mockResolvedValue([]); mocks.advisory.mockResolvedValue({ schema_version: 1, application_id: 1, event_id: 2, items: [] });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllEnvs(); delete window.offerpilotDesktop; });
it('stale empty source hides the real interview primary action; fresh seeded source restores exact task click', async () => {
  const launch = vi.fn();
  await act(async () => root.render(<InterviewV01View events={[]} onOpenTask={launch} />)); await flush();
  one(role('tab', '即将进行'));
  const card = one('[data-testid="interview-event-card-2"]');
  expect(card.getAttribute('data-interview-card-bucket')).toBe('unavailable');
  expect(query('[data-interview-primary="true"]', card)).toHaveLength(0);
  await act(async () => root.render(<InterviewV01View events={[event]} onOpenTask={launch} />)); await flush();
  await click(one('[data-testid="interview-event-card-2"] >> [data-interview-primary="true"]'));
  expect(launch).toHaveBeenCalledWith({ ref: { taskId: 'application.interview_prepare', applicationId: 1, eventId: 2 }, source: 'interview_event_card', focus: 'current' });
});
it('interview primary leads to readiness: select the saved resume and click Start before proposal exists', async () => {
  const launch = vi.fn();
  await act(async () => root.render(<InterviewReadinessCenter fixedMode="real" lockedEvent={{ applicationId: 1, eventId: 2 }} generation={1} resumes={{ status: 'ready', value: [resume] }} onOpenTask={launch} />)); await flush();
  expect(query(role('region', '面试准备建议'))).toHaveLength(0);
  const scope = one('[data-testid="locked-real-preparation"]');
  expect((one(role('button', '开始准备'), scope) as HTMLButtonElement).disabled).toBe(false);
  const select = one('#locked-readiness-resume').closest('.ant-select')!;
  await click(one('.ant-select-selector', select));
  const option = one('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option');
  expect(option.textContent).toBe(`${resume.title} · 其他简历`); await click(option);
  await click(one(role('button', '开始准备'), scope));
  expect(launch).toHaveBeenCalledWith({ ref: { taskId: 'application.interview_prepare', applicationId: 1, eventId: 2 }, source: 'interview_event_card', focus: 'current', hints: { suggestedResumeId: 3 } });
});
it('interview proposal native labels, disclosure, output heading and close match the fixed component', async () => {
  const close = vi.fn(); const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  mocks.preparation.mockResolvedValue({ proposal_status: 'normal', proposal: { preparation_directions: [{ id: 'd1', text: 'synthetic', evidence_refs: [] }], story_prompts: [], review_points: [], interviewer_questions: [], items_to_clarify: [] } });
  await act(async () => root.render(<InterviewPreparationProposalDrawer open context={{ applicationId: 1, eventId: 2, resumeId: 3, jdVersionId: 5, jdText: 'synthetic JD', knowledgeSelections: [], userAssertions: [] }} resumeOptions={[resume]} onClose={close} />)); await flush();
  const scope = one(role('region', '面试准备建议'));
  expect((one('[data-testid="interview-preparation-resume-select"]', scope) as HTMLSelectElement).value).toBe('3');
  expect(query('internal:label="粘贴 JD"s', scope)).toHaveLength(0);
  expect((one(role('textbox', '粘贴 JD'), scope) as HTMLTextAreaElement).value).toBe('synthetic JD');
  await click(one('[data-testid="interview-preparation-generate"]', scope));
  expect(confirm).toHaveBeenCalledWith('仅 JD、所选简历和已确认 Knowledge Evidence 会发送给 AI；用户断言仅保存于本次快照，不会发送给 AI，也不作为建议依据。是否继续？');
  one(role('heading', '准备方向'), scope); expect(query('article', scope)).toHaveLength(1);
  await click(one(role('button', '关闭'), scope)); expect(close).toHaveBeenCalledOnce();
});
it('resume card exact Edit and real classification modal controls remain uniquely actionable', async () => {
  const edit = vi.fn(); const close = vi.fn();
  await act(async () => root.render(<ResumeCard resume={resume} resumes={[resume]} onEdit={edit} onSetMaster={() => {}} onCopy={() => {}} onDelete={() => {}} />));
  await click(one(role('button', '编辑'), one('.ant-card'))); expect(edit).toHaveBeenCalledWith(3);
  mocks.resumePreview.mockResolvedValue({ resume_id: 3, source_fingerprint: 'synthetic', fields: [{ path: 'contact.name', value: '合成候选人', evidence: '姓名：合成候选人' }] });
  await act(async () => root.render(<ResumeImportReview resume={resume} onSaved={() => {}} onClose={close} />)); await flush();
  const modal = one(role('dialog', 'AI 简历分类与核对'));
  await click(one(role('button', '开始分类'), modal)); one(role('region', '分类候选'), modal);
  expect(query(role('textbox', '基本信息 · 姓名/名称候选'), modal)).toHaveLength(1);
  await click(one('button >> internal:has-text=/取\\s*消/', modal)); expect(close).toHaveBeenCalledOnce(); expect(mocks.resumeConfirm).not.toHaveBeenCalled();
});
it('Offer card, three brief fields, explicit input-review and generate button match real DOM', async () => {
  const start = vi.fn();
  await act(async () => root.render(<OfferCard offer={offer} selected={false} onToggleSelect={() => {}} onCoach={start} onView={() => {}} />));
  await click(one('[data-action="start-negotiation"]', one('.ant-card'))); expect(start).toHaveBeenCalledWith(offer);
  mocks.offerPreview.mockResolvedValue({ source_fingerprint: 'synthetic', snapshot: { snapshot_version: 1, offer_snapshot: { ...offer, dimensions: [] }, user_brief: { goal: 'goal', concerns: 'concerns', scenario: 'scenario' } } });
  mocks.offerCreate.mockResolvedValue({ id: 7, offer_id: 4, application_id: 1, attempt_status: 'ready', proposal_status: 'normal', source_changed: false, input_snapshot: { snapshot_version:1,offer_snapshot:{...offer,dimensions:[]},user_brief:{goal:'goal',concerns:'concerns',scenario:'scenario'} }, proposal: { proposal_status: 'normal', communication_goals: [], clarification_questions: [], talking_points: [], preparation_checks: [] } });
  await act(async () => root.render(<OfferNegotiationDrawer offer={offer} onClose={() => {}} open />)); await flush();
  const scope = one('[data-testid="offer-negotiation-drawer"]');
  await value('本次沟通目标', 'goal', scope); await value('本次顾虑', 'concerns', scope); await value('沟通场景', 'scenario', scope);
  await click(one(role('button', '下一步：检查输入'), scope)); one(role('region', '确认本次 AI 输入'), scope);
  await click(one(role('button', '确认生成谈薪准备草稿'), scope));
  expect(mocks.offerCreate).toHaveBeenCalledOnce(); one('[aria-label="谈薪准备草稿"]', scope); one('[data-testid="offer-negotiation-confirm"]', scope);
});

it('Haru real visible Stop has the exact name and forwards the current stop request only while enabled', async () => {
  let listener: any;
  const snapshot = { version: 3, conversationId: 7, taskState: 'running', loading: true, hasPending: false,
    canStop: true, stopping: false, canSend: false, error: '', stopMessage: '', contextLabel: '工作区', messages: [] };
  const state = { connected: true, visible: true, expanded: true, alwaysOnTop: false, generation: 1, snapshot };
  const request = vi.fn(async () => ({ ok: true }));
  window.offerpilotDesktop = { role: 'haru', getState: async () => state, onState: fn => { listener = fn; return () => {}; },
    request, windowAction: async () => true } as any;
  await act(async () => root.render(<DesktopHaruWindow />)); await flush();
  const stop = one(role('button', '停止')) as HTMLButtonElement;
  expect(stop.disabled).toBe(false); await click(stop);
  expect(request).toHaveBeenCalledWith({ action: 'stop', version: 3, generation: 1 });
  await act(async () => listener({ ...state, snapshot: { ...snapshot, canStop: false } }));
  expect((one(role('button', '停止')) as HTMLButtonElement).disabled).toBe(true);
});
