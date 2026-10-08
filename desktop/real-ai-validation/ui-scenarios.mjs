// Real installed-renderer actions only. No provider calls, secrets, raw response
// logs, traces, exports, controller replacement, or synthetic Haru publication.
import { proveLivePilotCompletion, liveLedgerFingerprint } from './live-pilot-completion.mjs';
import { randomUUID } from 'node:crypto';
import { createUiDiagnostic, sanitizeStreamDiagnostic } from './ui-diagnostics.mjs';
import { installMirrorObservation, readMirrorObservation, removeMirrorObservation, isRunningMirrorProven } from './mirror-observer.mjs';

export const scenarios = Object.freeze([
  { id: 'connection', maxOutputTokens: 64, timeoutMs: 25_000 },
  { id: 'pilot-stream', maxOutputTokens: 4096, timeoutMs: 75_000 },
  { id: 'pilot-hitl-reject', maxOutputTokens: 4096, timeoutMs: 75_000 },
  { id: 'interview-preparation', maxOutputTokens: 8192, timeoutMs: 100_000 },
  { id: 'resume-structure', maxOutputTokens: 4096, timeoutMs: 75_000 },
  { id: 'offer-negotiation', maxOutputTokens: 8192, timeoutMs: 100_000 },
  // Cancellation can leave upstream usage unknown. Run it last, never spend
  // another case's allowance to repair a cancellation or SDK retry.
  { id: 'pilot-cancel', maxOutputTokens: 4096, timeoutMs: 75_000 },
].map(Object.freeze));

const COMPANY = 'OfferPilot 合成验收公司';
const ROLE = '合成软件测试工程师';
const RESUME_TITLE = 'OfferPilot 合成验收简历';
const RAW_RESUME = '姓名：合成候选人\n求职意向：软件测试工程师\n技能：JavaScript、Python、自动化测试\n项目：为合成订单服务编写自动化测试，发现并修复三个边界问题。';
const JD = '合成岗位资料。招聘软件测试工程师，负责 JavaScript 和 Python 自动化测试、接口边界分析及团队协作。';
const CODES = new Set(['PASSED', 'INVALID_HARNESS', 'SYNTHETIC_PROFILE_INVALID', 'SETTINGS_SAVE_FAILED', 'CONNECTION_FAILED', 'UI_ACTION_FAILED', 'UI_TIMEOUT', 'UI_ASSERTION_FAILED', 'STREAM_NOT_OBSERVED', 'HARU_SYNC_FAILED', 'HITL_NOT_OBSERVED', 'CANCEL_NOT_OBSERVED', 'UNEXPECTED_DIALOG', 'UNEXPECTED_PROVIDER_REQUESTS', 'PROVIDER_BUDGET_BLOCKED', 'SUITE_DEADLINE', 'PREVIOUS_SCENARIO_FAILED', 'UNSAFE_SCREENSHOT', 'AUXILIARY_READBACK_FAILED', 'LIVE_COMPLETION_UNPROVEN']);
class UiFailure extends Error { constructor(code) { super(code); this.code = code; } }
const fail = (code) => { throw new UiFailure(code); };
const check = (value, code = 'UI_ASSERTION_FAILED') => { if (!value) fail(code); };
const exact = (name) => ({ name, exact: true });
const id = (value) => { check(Number.isSafeInteger(value) && value > 0, 'SYNTHETIC_PROFILE_INVALID'); return value; };
export function safeUiCode(error) {
  if ((error instanceof UiFailure || error?.code === 'LIVE_COMPLETION_UNPROVEN') && CODES.has(error.code)) return error.code;
  if (['BUDGET', 'COUNT', 'CLOSED', 'DEADLINE', 'LEDGER', 'USAGE', 'EXPIRED'].includes(error?.code)) return 'PROVIDER_BUDGET_BLOCKED';
  return error?.name === 'TimeoutError' ? 'UI_TIMEOUT' : 'UI_ACTION_FAILED';
}

// This deliberately seeds the ONE synthetic profile, not feature AI endpoints.
// Marking a synthetic text record as upload prepares classification input; it
// makes no claim that PDF selection, extraction or upload was exercised.
export async function prepareSyntheticProfile(api) {
  check(typeof api === 'function', 'INVALID_HARNESS');
  const existing = await api('/api/applications', { method: 'GET' });
  check(Array.isArray(existing) && existing.length === 0, 'SYNTHETIC_PROFILE_INVALID');
  const application = await api('/api/applications', { method: 'POST', body: {
    company_name: COMPANY, position_name: ROLE, status: 'applied', notes: '仅供隔离验收的虚构资料。',
  } });
  const applicationId = id(application.id);
  const jd = await api(`/api/applications/${applicationId}/job-description/versions`, { method: 'POST', body: {
    jd_text: JD, expected_current_version_id: null, idempotency_key: randomUUID(),
  } });
  const resume = await api('/api/resumes', { method: 'POST', body: {
    title: RESUME_TITLE, source: 'manual', content_json: { raw_text: RAW_RESUME },
  } });
  const resumeId = id(resume.id);
  const uploaded = await api(`/api/resumes/${resumeId}`, { method: 'PATCH', body: { source: 'upload' } });
  check(uploaded.id === resumeId && uploaded.source === 'upload', 'SYNTHETIC_PROFILE_INVALID');
  const event = await api('/api/application-events', { method: 'POST', body: {
    application_id: applicationId, event_type: 'interview', subtype: 'technical', tags: [], round: 1,
    scheduled_at: new Date(Date.now() + 2 * 86400_000).toISOString(), duration_minutes: 45,
    status: 'scheduled', location: '合成线上面试', notes: '仅供隔离验收。',
  } });
  const offer = await api('/api/offers', { method: 'POST', body: {
    application_id: applicationId, company_name: COMPANY, position_name: ROLE,
    base_monthly: 20000, months_per_year: 12, signing_bonus: 0,
    status: 'pending', notes: '所有金额与公司均为虚构验收数据。',
  } });
  return Object.freeze({ applicationId, jdVersionId: id(jd.id), resumeId,
    eventId: id(event.id), offerId: id(offer.id), company: COMPANY, resumeTitle: RESUME_TITLE });
}

// Ant Design may space exactly two Han characters (保 存 / 取 消).
function button(scope, label) {
  const escape = (part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = /^[\u3400-\u9fff]{2}$/u.test(label) ? [...label].map(escape).join('\\s*') : escape(label);
  return scope.getByRole('button').filter({ hasText: new RegExp(`^\\s*${pattern}\\s*$`, 'u') })
    .or(scope.getByRole('button', exact(label))).filter({ visible: true });
}
const region = (page, name) => page.getByRole('region', exact(name));
const pilot = (page) => page.locator('[data-onboarding-target="pilot"]').filter({ visible: true });
const composer = (page) => pilot(page).getByPlaceholder('问问领航员，或输入 / 唤起能力', { exact: true });
const settings = (page) => region(page, 'AI 设置');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function operationContext(deadline) {
  const diagnostic = createUiDiagnostic();
  return {
    mark: diagnostic.mark, target: diagnostic.target, mirror: diagnostic.mirror, stream: diagnostic.stream, diagnostic: diagnostic.snapshot,
    timeout(cap = 15_000) { const left = deadline - Date.now(); if (left <= 0) fail('SUITE_DEADLINE'); return Math.max(1, Math.min(left, cap)); },
    async click(locator, diagnosticLocator = locator) { diagnostic.target(diagnosticLocator); await locator.click({ timeout: this.timeout() }); },
    async fill(locator, value) { diagnostic.target(locator); await locator.fill(value, { timeout: this.timeout() }); },
    async visible(locator) { diagnostic.target(locator); await locator.waitFor({ state: 'visible', timeout: this.timeout() }); },
    async hidden(locator) { diagnostic.target(locator); await locator.waitFor({ state: 'hidden', timeout: this.timeout() }); },
    async detached(locator) { diagnostic.target(locator); await locator.waitFor({ state: 'detached', timeout: this.timeout() }); },
    async until(predicate, cap = 15_000, code = 'UI_TIMEOUT') {
      const end = Date.now() + this.timeout(cap);
      do { if (await predicate()) return; await sleep(Math.min(50, Math.max(1, end - Date.now()))); } while (Date.now() < end);
      fail(Date.now() >= deadline ? 'SUITE_DEADLINE' : code);
    },
  };
}
export async function leaveTask(page, ctx) {
  ctx.mark('LEAVE_TASK');
  // Each business scenario closes its own known task once and waits for the
  // outer owner to unmount. Never click a remaining/unknown task as navigation
  // cleanup: it may already be closing or still own an unresolved operation.
  await ctx.detached(page.locator('[data-core-task-owner]'));
  const exit = page.getByRole('button', exact('退出沉浸模式，返回原页面'));
  if (await exit.isVisible()) {
    ctx.mark('PILOT_EXIT');
    await ctx.click(exit);
    await ctx.hidden(exit);
  }
}
export async function closeTaskThroughUi(page, surface, control, ctx, expectedOwner) {
  ctx.mark('TASK_CLOSE_OWNER');
  check(['application-interview-prepare', 'application-offer-review'].includes(expectedOwner));
  const owner = surface.locator('xpath=ancestor::*[@data-core-task-owner][1]');
  check(await owner.count() === 1 && await page.locator('[data-core-task-owner]').count() === 1);
  const generation = await owner.getAttribute('data-core-task-generation', { timeout: ctx.timeout() });
  check(await owner.getAttribute('data-core-task-owner', { timeout: ctx.timeout() }) === expectedOwner);
  check(typeof generation === 'string' && /^[1-9]\d*$/.test(generation)
    && Number.isSafeInteger(Number(generation)));
  const pinnedOwner = page.locator(`[data-core-task-owner="${expectedOwner}"][data-core-task-generation="${generation}"]`);
  check(await pinnedOwner.count() === 1);
  // The action locator itself stays generation-bound during Playwright's
  // actionability auto-wait. A replacement must never inherit this click.
  const pinnedControl = control.and(pinnedOwner.getByRole('button'));
  check(await pinnedControl.count() === 1);
  const physicalControl = await pinnedControl.elementHandle({ timeout: ctx.timeout() });
  check(physicalControl);
  try {
    ctx.mark('TASK_CLOSE_CLICK');
    // Do not retarget even if a remounted controller reuses owner/generation.
    // ElementHandle.click fails on detach instead of clicking a replacement.
    await ctx.click(physicalControl, pinnedControl);
    await ctx.hidden(surface);
    ctx.mark('TASK_OWNER_DETACHED');
    // The child disappears as soon as phase=closing. Its parent remains through
    // the natural exit animation; hidden child alone cannot certify task closure.
    await ctx.detached(pinnedOwner);
    check(await page.locator('[data-core-task-owner]').count() === 0);
  } finally {
    let timer;
    try { await Promise.race([physicalControl.dispose(), new Promise(resolve => { timer = setTimeout(resolve, 1000); })]); }
    catch { /* Handle release must never replace the primary UI outcome. */ }
    finally { clearTimeout(timer); }
  }
}
async function navigate(page, name, ctx) {
  await leaveTask(page, ctx);
  ctx.mark('NAVIGATE');
  await ctx.click(page.getByRole('navigation', exact('主导航')).getByRole('button', exact(name)));
}
async function openPilot(page, ctx) {
  await leaveTask(page, ctx);
  ctx.mark('PILOT_OPEN');
  const quick = page.locator('header.op-topbar .op-topbar-actions').getByRole('button')
    .filter({ hasText: /^\s*快速打开\s+(?:Ctrl\s*K|⌘\s*K)\s*$/u });
  await ctx.click(quick);
  const input = page.getByRole('combobox').and(page.getByPlaceholder('快速打开页面、投递或助手…', { exact: true }));
  await ctx.fill(input, '打开 Pilot 工作区');
  const option = page.getByRole('listbox', exact('命令结果')).getByRole('option')
    .filter({ has: page.getByText('打开 Pilot 工作区', { exact: true }) });
  check(await option.count() === 1);
  await ctx.click(option);
  await ctx.hidden(input);
  await ctx.visible(pilot(page));
  // Scope to the rail: a docked header also has an icon-only New button.
  await ctx.click(pilot(page).locator('aside').getByRole('button').filter({ hasText: /^\s*新建对话\s*$/u }));
  await ctx.visible(composer(page));
}
async function uiResponse(page, pathname, method, action, ctx, timeout = 15_000) {
  const origin = new URL(page.url()).origin;
  const pending = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.origin === origin && url.pathname === pathname && response.request().method() === method;
  }, { timeout: ctx.timeout(timeout) });
  // Always observe the waiter rejection even if click fails; never emit a raw
  // Playwright error containing selectors, input, headers or response bodies.
  pending.catch(() => undefined);
  await action();
  const response = await pending;
  check(response.status() >= 200 && response.status() < 300);
  return response;
}
async function openSettings(page, ctx) {
  await navigate(page, '设置', ctx);
  ctx.mark('SETTINGS_OPEN');
  await ctx.click(button(page, '配置 AI'));
  await ctx.visible(settings(page));
}
async function setSwitch(scope, label, value, ctx) {
  // Ant tooltip icons extend a switch's accessible name; the explicit Form label
  // remains exact. Intersect label and role instead of broad name matching.
  const target = scope.getByLabel(label, { exact: true }).and(scope.getByRole('switch'));
  ctx.target(target);
  if (await target.getAttribute('aria-checked') !== String(value)) await ctx.click(target);
  check(await target.getAttribute('aria-checked') === String(value), 'SETTINGS_SAVE_FAILED');
}
async function configureCase(page, broker, current, ctx) {
  ctx.mark('BROKER_PREPARE');
  const prepared = await broker.prepareCase(current.id);
  check(typeof prepared?.clientToken === 'string' && prepared.clientToken.length >= 16, 'INVALID_HARNESS');
  await openSettings(page, ctx);
  const form = settings(page);
  ctx.mark('PROVIDER_LIST', form.getByTestId('ai-provider-list').locator('.ant-list-item'));
  await ctx.until(async () => await form.getByTestId('ai-provider-list').locator('.ant-list-item').count() === 1, 15_000, 'SETTINGS_SAVE_FAILED');
  ctx.mark('PROVIDER_LABEL');
  await ctx.fill(form.getByLabel('显示名称', { exact: true }), 'Bounded AI Validation');
  ctx.mark('PROVIDER_KEY');
  await ctx.fill(form.getByLabel('API 密钥', { exact: true }), prepared.clientToken);
  const provider = form.getByLabel('模型供应商', { exact: true });
  const selectRoot = provider.locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " ant-select ")][1]');
  ctx.mark('PROVIDER_TYPE_OPEN');
  await ctx.click(selectRoot.locator('.ant-select-selector'));
  const popup = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)');
  await ctx.visible(popup);
  ctx.mark('PROVIDER_TYPE_SELECT');
  await ctx.click(popup.locator('.ant-select-item-option').and(popup.getByTitle('OpenAI 兼容', { exact: true })));
  ctx.mark('PROVIDER_TYPE_CLOSED');
  await ctx.hidden(popup);
  ctx.mark('PROVIDER_ENDPOINT');
  await ctx.fill(form.getByLabel('接口地址', { exact: true }), `${broker.origin}/v1`);
  ctx.mark('PROVIDER_MODEL');
  await ctx.fill(form.getByLabel('模型', { exact: true }), 'deepseek-flash');
  ctx.mark('PROVIDER_CONTEXT');
  await ctx.fill(form.getByLabel('上下文窗口（tokens）', { exact: true }), '131072');
  ctx.mark('PROVIDER_OUTPUT');
  await ctx.fill(form.getByLabel('单次最大输出（tokens）', { exact: true }), String(current.maxOutputTokens));
  ctx.mark('PROVIDER_ENABLED');
  await setSwitch(form, '启用', true, ctx);
  ctx.mark('PROVIDER_JSON_SCHEMA');
  await setSwitch(form, '原生 JSON Schema', false, ctx);
  ctx.mark('PROVIDER_HITL');
  await setSwitch(form, '写操作自动确认', false, ctx);
  ctx.mark('SETTINGS_SAVE');
  const response = await uiResponse(page, '/api/settings', 'PUT', () => ctx.click(button(form, '保存')), ctx);
  ctx.mark('SETTINGS_READBACK');
  const value = await response.json();
  check(value.chat_auto_approve_writes === false && value.fallback_provider_ids?.length === 0 && value.providers?.length === 1
    && value.providers[0].base_url === `${broker.origin}/v1` && value.providers[0].model === 'deepseek-flash'
    && value.providers[0].has_api_key === true && value.providers[0].max_output_tokens === current.maxOutputTokens,
  'SETTINGS_SAVE_FAILED');
  ctx.mark('SETTINGS_CLOSED');
  await ctx.hidden(form);
}

// Observe only existing DOM and the public read-only desktop bridge. No state
// injection, execution command, controller callback, or response interception.
async function mirrorState(surface) {
  return surface.evaluate(async () => {
    const state = await window.offerpilotDesktop?.getState();
    const snapshot = state?.snapshot;
    return { connected: state?.connected === true, conversationId: snapshot?.conversationId ?? null,
      taskState: snapshot?.taskState ?? null, loading: snapshot?.loading === true,
      hasPending: snapshot?.hasPending === true, canStop: snapshot?.canStop === true,
      canSend: snapshot?.canSend === true, failed: Boolean(snapshot?.error), stopped: Boolean(snapshot?.stopMessage),
      assistantCount: snapshot?.messages?.filter((item) => item.role === 'assistant' && item.content.trim()).length ?? 0 };
  });
}
async function waitMirror(page, haru, state, ctx, cap = 15_000) {
  await ctx.until(async () => {
    const [owner, companion] = await Promise.all([mirrorState(page), mirrorState(haru)]);
    return owner.connected && companion.connected && owner.conversationId > 0
      && owner.conversationId === companion.conversationId && owner.taskState === state && companion.taskState === state
      && owner.loading === companion.loading && owner.hasPending === companion.hasPending;
  }, cap, 'HARU_SYNC_FAILED');
  const label = { running: '正在处理', waiting_confirmation: '等待你确认', idle: '随时待命' }[state];
  await ctx.visible(haru.getByRole('status').filter({ hasText: new RegExp(`^${label}$`, 'u') }));
}
export function mirrorDiagnostic(pair, caseId) {
  const owner = pair?.owner, haru = pair?.haru;
  const positive = value => Number.isSafeInteger(value) && value > 0;
  const same = positive(owner?.runningConversationId) && owner.runningConversationId === haru?.runningConversationId;
  const valid = value => value?.healthy === true && value?.identityChanged === false && value?.generationChanged === false &&
    value?.expired === false && value?.caseId === caseId;
  return { observerInstalled: owner?.installed === true && haru?.installed === true,
    ownerBaselineReady: owner?.baselineReady === true, haruBaselineReady: haru?.baselineReady === true,
    ownerConnected: owner?.connected === true, haruConnected: haru?.connected === true,
    ownerIdleNow: owner?.currentTaskState === 'idle' && owner?.loading === false && owner?.hasPending === false,
    haruIdleNow: haru?.currentTaskState === 'idle' && haru?.loading === false && haru?.hasPending === false,
    ownerRunningPositiveSeen: owner?.bridgeRunningObserved === true && positive(owner?.runningConversationId),
    haruRunningPositiveSeen: haru?.bridgeRunningObserved === true && positive(haru?.runningConversationId),
    ownerRunningNullSeen: owner?.runningWithNullObserved === true, haruRunningNullSeen: haru?.runningWithNullObserved === true,
    ownerRunningDomSeen: owner?.domRunningObserved === true, haruRunningDomSeen: haru?.domRunningObserved === true,
    sameRunningConversation: same,
    currentConversationMatches: same && owner?.conversationId === owner.runningConversationId && haru?.conversationId === haru.runningConversationId,
    invalidObservation: !valid(owner) || !valid(haru),
    observationReadFailed: owner?.readTimedOut === true || haru?.readTimedOut === true };
}
async function observedRunning(page, haru, current, ctx) {
  await ctx.until(async () => {
    let pair;
    try { pair = await readMirrorObservation(page, haru); }
    catch { ctx.mirror({ observationReadFailed: true, invalidObservation: true }); fail('HARU_SYNC_FAILED'); }
    ctx.mirror(mirrorDiagnostic(pair, current.id));
    return isRunningMirrorProven(pair);
  }, current.timeoutMs, 'HARU_SYNC_FAILED');
}
export function isFinalMirrorProven(pair) {
  return isRunningMirrorProven(pair) && [pair.owner, pair.haru].every(value =>
    value.currentTaskState === 'idle' && value.loading === false && value.hasPending === false);
}
async function verifyFinalMirrorIdentity(page, haru, current, ctx) {
  ctx.mark('PILOT_FINAL_MIRROR');
  let pair;
  try { pair = await readMirrorObservation(page, haru); }
  catch { ctx.mirror({ observationReadFailed: true, invalidObservation: true }); fail('HARU_SYNC_FAILED'); }
  ctx.mirror(mirrorDiagnostic(pair, current.id));
  check(isFinalMirrorProven(pair), 'HARU_SYNC_FAILED');
}
async function expandHaru(haru, ctx) {
  await ctx.visible(haru.getByRole('main', exact('Haru 桌面小窗')));
  const expand = haru.getByRole('button', exact('展开 Haru 对话'));
  if (await expand.isVisible()) await ctx.click(expand);
  await ctx.visible(haru.getByRole('region', exact('Haru 对话')));
}
export async function installStreamObservation(page) {
  await page.evaluate(() => {
    if (window.__offerpilotBoundedUiObserver) throw new Error('observer-exists');
    const target = document.querySelector('[data-onboarding-target="pilot"]');
    if (!target) throw new Error('pilot-missing');
    const state = { updates: 0, growth: 0, length: 0, observer: null, diagnosticObserver: null };
    let currentLength = 0, currentGrowth = 0, currentGrowthWithStop = 0, replaced = false;
    const increment = value => Math.min(255, value + 1);
    const read = node => {
      const nodes = node?.querySelectorAll('[class*="bubbleAssistant"]') ?? [];
      return { length: nodes.length ? (nodes[nodes.length - 1].textContent || '').trim().length : 0,
        stop: Boolean(node?.querySelector('[aria-label="停止当前回复"]')) };
    };
    const observe = () => {
      const { length, stop } = read(target);
      if (length > state.length) state.growth = increment(state.growth);
      if (length > state.length && stop) state.updates += 1;
      state.length = length;
    };
    const current = () => {
      const nodes = document.querySelectorAll('[data-onboarding-target="pilot"]');
      const node = nodes.length === 1 ? nodes[0] : null;
      if (node && node !== target) replaced = true;
      return { node, unique: nodes.length === 1, ...read(node) };
    };
    const observeCurrent = () => {
      const value = current();
      if (value.length > currentLength) {
        currentGrowth = increment(currentGrowth);
        if (value.stop) currentGrowthWithStop = increment(currentGrowthWithStop);
      }
      currentLength = value.length;
    };
    state.summary = () => {
      const value = current();
      return { installed: true, originalTargetConnected: target.isConnected === true,
        originalTargetCurrent: value.node === target, currentPilotUnique: value.unique,
        targetReplacementObserved: replaced, stopPresentNow: value.stop, readFailed: false,
        originalGrowthCount: state.growth, originalGrowthWithStopCount: Math.min(255, state.updates),
        currentGrowthCount: currentGrowth, currentGrowthWithStopCount: currentGrowthWithStop };
    };
    state.observer = new MutationObserver(observe);
    state.observer.observe(target, { childList: true, subtree: true, characterData: true });
    // Diagnostic-only second observer. It never feeds the >=2 pass assertion,
    // so a replaced container or Stop timing cannot silently broaden coverage.
    state.diagnosticObserver = new MutationObserver(observeCurrent);
    state.diagnosticObserver.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    window.__offerpilotBoundedUiObserver = state;
  });
}
export async function readStreamDiagnostic(page) {
  let timer;
  try {
    return sanitizeStreamDiagnostic(await Promise.race([
      page.evaluate(() => window.__offerpilotBoundedUiObserver?.summary?.() ?? { installed: false }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('STREAM_READ_FAILED')), 5000); }),
    ]));
  } catch { return sanitizeStreamDiagnostic({ readFailed: true }); }
  finally { clearTimeout(timer); }
}
export async function removeStreamObservation(page) {
  let timer;
  try {
    await Promise.race([page.evaluate(() => { window.__offerpilotBoundedUiObserver?.observer.disconnect(); window.__offerpilotBoundedUiObserver?.diagnosticObserver?.disconnect(); delete window.__offerpilotBoundedUiObserver; }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new UiFailure('UI_ACTION_FAILED')), 5000); })]);
  } catch { fail('UI_ACTION_FAILED'); }
  finally { clearTimeout(timer); }
}
export function recordObserverCleanupFailure(results, caseId, kind, diagnostic) {
  check(['mirror', 'stream'].includes(kind), 'INVALID_HARNESS');
  const previous = results.at(-1);
  check(previous?.id === caseId, 'INVALID_HARNESS');
  results[results.length - 1] = { ...previous, checks: { ...previous.checks,
    [kind === 'mirror' ? 'mirrorObserverCleanupFailed' : 'streamObserverCleanupFailed']: true },
    ...(previous.status === 'PASS' ? { status: 'FAIL', code: kind === 'mirror' ? 'HARU_SYNC_FAILED' : 'UI_ACTION_FAILED', diagnostic } : {}) };
}
async function assertHaruRendered(haru, ctx) {
  await ctx.until(() => haru.evaluate(async () => {
    const snapshot = (await window.offerpilotDesktop?.getState())?.snapshot;
    const visible = [...document.querySelectorAll('.desktop-haru-messages article[data-role="assistant"] p')];
    const expected = snapshot?.messages.filter((item) => item.role === 'assistant' && item.content.trim()) ?? [];
    return expected.length > 0 && visible.length === expected.length
      && visible.every((element, index) => element.textContent === expected[index].content);
  }), 15_000, 'HARU_SYNC_FAILED');
}
async function safeCapture(capture, caseId, surface) {
  if (typeof capture !== 'function') return;
  const clean = await surface.evaluate(() => {
    const credentialForm = document.querySelector('section[aria-label="AI 设置"], input[type="password"], input[autocomplete="current-password"], input#api_key');
    const text = document.body?.innerText || '';
    return !credentialForm && !/\b(?:sk-[A-Za-z0-9_-]{10,}|Bearer\s+[A-Za-z0-9_.-]{10,})\b/.test(text);
  });
  check(clean, 'UNSAFE_SCREENSHOT');
  await capture(caseId, surface);
}

const PROMPTS = Object.freeze({
  'pilot-stream': '这是虚构软件验收。不要调用任何工具或读取资料，只分十个小段说明软件测试中的边界值检查，每段约四十字。',
  'pilot-cancel': '这是虚构软件验收。不要调用任何工具或读取资料，请连续写二十段关于软件测试边界条件的说明，每段约六十字。',
  'pilot-hitl-reject': '这是虚构隔离验收。请直接调用 create_application 工具，新建公司“合成待拒绝公司”、岗位“合成待拒绝岗位”的投递，status为applied，notes为空；不要先查询其他记录。只提出这一次写入，等待我确认。',
});
async function runPilot({ page, haru, api, broker, fixture, capture, current, ctx, freshSessionRequired, mode, requestCountBefore }) {
  await openPilot(page, ctx);
  await expandHaru(haru, ctx);
  ctx.mark('PILOT_COMPOSE');
  await ctx.fill(composer(page), PROMPTS[current.id] + (mode === 'live' && current.id === 'pilot-stream'
    ? '请只用纯文本，不使用 Markdown、标题、列表标记或结构化任务卡。' : ''));
  if (current.id === 'pilot-stream') await installStreamObservation(page);
  if (['pilot-stream', 'pilot-cancel'].includes(current.id) || freshSessionRequired) {
    ctx.mark('PILOT_OBSERVER_INSTALL');
    try { await installMirrorObservation(page, haru, current.id); }
    catch { ctx.mirror({ invalidObservation: true, observationReadFailed: true }); fail('HARU_SYNC_FAILED'); }
  }
  if (mode === 'live' && current.id === 'pilot-stream') {
    const outcome = await proveLivePilotCompletion({ mode, page, haru, broker, requestCountBefore, ctx,
      deadlineMs: Date.now() + ctx.timeout(current.timeoutMs), send: async () => {
        ctx.mark('PILOT_ARM'); await broker.armCase(current.id);
        ctx.mark('PILOT_SEND'); await ctx.click(pilot(page).getByRole('button', exact('发送')));
      } });
    return outcome;
  }
  ctx.mark('PILOT_ARM');
  await broker.armCase(current.id);
  ctx.mark('PILOT_SEND');
  await ctx.click(pilot(page).getByRole('button', exact('发送')));
  if (current.id === 'pilot-hitl-reject') {
    const proposal = pilot(page).getByRole('group', exact('AI 修改提议'));
    ctx.mark('PILOT_HITL_VISIBLE', proposal);
    await ctx.until(() => proposal.isVisible(), current.timeoutMs, 'HITL_NOT_OBSERVED');
    await ctx.visible(proposal.getByText('AI 想执行一个修改操作 · 新建投递', { exact: true }));
    await waitMirror(page, haru, 'waiting_confirmation', ctx);
    await ctx.visible(haru.getByRole('button', exact('到 Pilot 查看并确认')));
    const before = await api('/api/applications', { method: 'GET' });
    check(Array.isArray(before) && before.length === 1 && before[0].id === fixture.applicationId, 'AUXILIARY_READBACK_FAILED');
    ctx.mark('PILOT_REJECT');
    await ctx.click(button(proposal, '拒绝建议'));
    const reject = proposal.getByRole('region', exact('拒绝建议确认'));
    // The inline confirmation is an aria-labelled section, never an approval.
    await ctx.visible(reject);
    await ctx.click(button(reject, '最终拒绝'));
    await ctx.hidden(proposal);
    await waitMirror(page, haru, 'idle', ctx);
    const after = await api('/api/applications', { method: 'GET' });
    check(Array.isArray(after) && after.length === 1 && after[0].id === fixture.applicationId, 'AUXILIARY_READBACK_FAILED');
    await safeCapture(capture, current.id, page);
    await safeCapture(capture, `haru-${current.id}`, haru);
    return { hitlVisible: true, rejectedThroughUi: true, syntheticWriteAbsent: true, haruPendingAndIdleMirrored: true };
  }
  ctx.mark('PILOT_RUNNING');
  await observedRunning(page, haru, current, ctx);
  if (current.id === 'pilot-cancel') {
    // Cancellation requires a current actionable control and active request,
    // never merely a historical running observation. Streaming readback below
    // may use genuinely observed transitions after a fast reply has ended.
    const stop = pilot(page).getByRole('button', exact('停止当前回复'));
    await ctx.until(async () => await stop.isVisible() && await stop.isEnabled(), current.timeoutMs);
    await ctx.until(() => {
      const ledger = broker.snapshot();
      const rows = ledger.requests.filter((row) => row.caseId === current.id);
      return rows.length === 1 && rows[0].status === 'RESERVED' && ledger.active === true
        && rows[0].outboundStarted === true && rows[0].upstreamResponded === true;
    }, current.timeoutMs, 'UNEXPECTED_PROVIDER_REQUESTS');
    ctx.mark('PILOT_STOP');
    await ctx.click(stop);
    ctx.mark('PILOT_STOP_READBACK');
    await ctx.until(async () => { const state = await mirrorState(haru); return !state.loading && !state.canStop && state.stopped && !state.failed; }, current.timeoutMs);
    await waitMirror(page, haru, 'idle', ctx);
    // Observe product-driven upstream cancellation BEFORE our cleanup can close
    // the broker. A completed response, upstream-only disconnect, or harness-created
    // CANCELLED is not proof: the product client must actually disconnect.
    await ctx.until(() => {
      const ledger = broker.snapshot();
      const rows = ledger.requests.filter((row) => row.caseId === current.id);
      if (rows.length === 1 && !['RESERVED', 'DISCONNECT'].includes(rows[0].status)) fail('CANCEL_NOT_OBSERVED');
      return rows.length === 1 && rows[0].status === 'DISCONNECT' && ledger.active === false
        && rows[0].clientDisconnectObserved === true;
    }, current.timeoutMs, 'CANCEL_NOT_OBSERVED');
    await verifyFinalMirrorIdentity(page, haru, current, ctx);
    await safeCapture(capture, current.id, page);
    await safeCapture(capture, `haru-${current.id}`, haru);
    return { ownerRunningTransitionObserved: true, haruRunningTransitionObserved: true, positiveRunningConversationMatched: true, finalRunningConversationMatched: true, stopClickedWhileRunning: true, stopAcknowledged: true, providerDisconnectObserved: true, haruRunningAndStoppedMirrored: true };
  }
  ctx.mark('PILOT_STREAM_READBACK');
  await ctx.until(() => page.evaluate(() => (window.__offerpilotBoundedUiObserver?.updates ?? 0) >= 2), current.timeoutMs, 'STREAM_NOT_OBSERVED');
  await ctx.until(async () => { const state = await mirrorState(haru); return !state.loading && state.assistantCount > 0 && !state.failed && !state.hasPending; }, current.timeoutMs);
  await waitMirror(page, haru, 'idle', ctx);
  await assertHaruRendered(haru, ctx);
  await verifyFinalMirrorIdentity(page, haru, current, ctx);
  await safeCapture(capture, current.id, page);
  await safeCapture(capture, `haru-${current.id}`, haru);
  return { ownerRunningTransitionObserved: true, haruRunningTransitionObserved: true, positiveRunningConversationMatched: true, finalRunningConversationMatched: true, incrementalAssistantRendering: true, haruRunningAndIdleMirrored: true, haruVisibleAssistantMatchesSnapshot: true };
}
async function runConnection({ page, broker, current, ctx }) {
  ctx.mark('CONNECTION_REOPEN');
  await openSettings(page, ctx);
  ctx.mark('CONNECTION_ARM');
  await broker.armCase(current.id);
  const response = await uiResponse(page, '/api/settings/providers/test', 'POST', () => {
    ctx.mark('CONNECTION_CLICK'); return ctx.click(button(settings(page), '测试连接'));
  }, ctx, current.timeoutMs);
  ctx.mark('CONNECTION_RESPONSE');
  check((await response.json()).ok === true, 'CONNECTION_FAILED');
  ctx.mark('CONNECTION_SUCCESS');
  await ctx.visible(settings(page).getByText('连接成功', { exact: true }));
  ctx.mark('CONNECTION_RETURN');
  await ctx.click(button(settings(page), '返回设置'));
  return { settingsSavedThroughUi: true, connectionTestClicked: true, connectionSucceeded: true };
}
async function runInterview({ page, broker, fixture, current, capture, ctx }) {
  await navigate(page, '面试', ctx);
  ctx.mark('INTERVIEW_OPEN');
  await ctx.click(page.getByRole('tab', exact('即将进行')));
  ctx.mark('INTERVIEW_CARD');
  await ctx.click(page.getByTestId(`interview-event-card-${fixture.eventId}`).locator('[data-interview-primary="true"]'));
  ctx.mark('INTERVIEW_READINESS');
  const readiness = page.getByTestId('locked-real-preparation');
  await ctx.visible(readiness);
  const resume = readiness.locator('#locked-readiness-resume').and(readiness.getByRole('combobox'));
  const selectRoot = resume.locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " ant-select ")][1]');
  ctx.mark('INTERVIEW_RESUME_OPEN');
  await ctx.click(selectRoot.locator('.ant-select-selector'));
  const popup = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)');
  await ctx.visible(popup);
  const title = fixture.resumeTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const option = popup.locator('.ant-select-item-option').filter({ hasText: new RegExp(`^${title} · (?:基础简历|其他简历)$`, 'u') });
  ctx.mark('INTERVIEW_RESUME_SELECT');
  await ctx.visible(option); check(await option.count() === 1);
  await ctx.click(option); await ctx.hidden(popup);
  ctx.mark('INTERVIEW_START');
  await ctx.click(readiness.getByRole('button', exact('开始准备')));
  ctx.mark('INTERVIEW_PROPOSAL');
  const surface = region(page, '面试准备建议');
  await ctx.visible(surface);
  ctx.mark('INTERVIEW_PROPOSAL_RESUME', surface.getByTestId('interview-preparation-resume-select'));
  await surface.getByTestId('interview-preparation-resume-select').selectOption(String(fixture.resumeId), { timeout: ctx.timeout() });
  const jdInput = surface.getByRole('textbox', exact('粘贴 JD'));
  ctx.mark('INTERVIEW_SOURCE', jdInput);
  await ctx.until(async () => (await jdInput.inputValue({ timeout: ctx.timeout() })).trim() === JD);
  let dialogAccepted = false;
  const handleDialog = async (dialog) => {
    const expected = '仅 JD、所选简历和已确认 Knowledge Evidence 会发送给 AI；用户断言仅保存于本次快照，不会发送给 AI，也不作为建议依据。是否继续？';
    if (dialog.type() === 'confirm' && dialog.message() === expected) { dialogAccepted = true; await dialog.accept(); }
    else { await dialog.dismiss(); }
  };
  page.on('dialog', handleDialog);
  try {
    await broker.armCase(current.id);
    ctx.mark('INTERVIEW_GENERATE');
    await uiResponse(page, `/api/applications/${fixture.applicationId}/interview-preparation-proposals`, 'POST', () => ctx.click(surface.getByTestId('interview-preparation-generate')), ctx, current.timeoutMs);
    check(dialogAccepted, 'UNEXPECTED_DIALOG');
    await ctx.visible(surface.getByRole('heading', exact('准备方向')));
    const articles = surface.locator('article');
    await ctx.visible(articles.locator('p').first());
    check(await articles.evaluateAll(nodes => nodes.length > 0
      && nodes.every(node => Boolean(node.querySelector('p')?.textContent?.trim()))));
    await safeCapture(capture, current.id, page);
    await closeTaskThroughUi(page, surface, button(surface, '关闭'), ctx, 'application-interview-prepare');
    return { sourceAndResumeSelected: true, disclosureAccepted: true, generatedProposalVisible: true };
  } finally { page.off('dialog', handleDialog); }
}
async function runResume({ page, api, broker, fixture, current, capture, ctx }) {
  await navigate(page, '素材库', ctx);
  ctx.mark('RESUME_OPEN');
  await ctx.click(page.locator('.op-module-tabs').getByRole('tab', exact('简历')));
  ctx.mark('RESUME_SEARCH');
  await ctx.fill(page.getByPlaceholder('搜索简历', { exact: true }), fixture.resumeTitle);
  const card = page.locator('.ant-card').filter({ has: page.getByText(fixture.resumeTitle, { exact: true }) });
  await ctx.visible(card);
  check(await card.count() === 1);
  ctx.mark('RESUME_EDIT');
  await ctx.click(card.getByRole('button', exact('编辑')));
  ctx.mark('RESUME_CLASSIFY_OPEN');
  await ctx.click(button(region(page, '编辑简历'), 'AI 分类并核对'));
  const modal = page.getByRole('dialog', exact('AI 简历分类与核对'));
  await ctx.visible(modal);
  await broker.armCase(current.id);
  ctx.mark('RESUME_GENERATE');
  await uiResponse(page, `/api/resumes/${fixture.resumeId}/structure-preview`, 'POST', () => ctx.click(button(modal, '开始分类')), ctx, current.timeoutMs);
  await ctx.visible(modal.getByRole('region', exact('分类候选')));
  check(await modal.getByRole('textbox').count() > 0);
  await safeCapture(capture, current.id, page);
  await ctx.click(button(modal, '取消'));
  await ctx.hidden(modal);
  const unchanged = await api(`/api/resumes/${fixture.resumeId}`, { method: 'GET' });
  check(unchanged.id === fixture.resumeId && unchanged.content_json?.raw_text === RAW_RESUME
    && Object.keys(unchanged.content_json).every((key) => key === 'raw_text'), 'AUXILIARY_READBACK_FAILED');
  const editor = region(page, '编辑简历');
  ctx.mark('RESUME_RETURN');
  await ctx.click(button(editor, '返回简历库'));
  await ctx.detached(editor);
  return { classificationPreviewVisible: true, cancelledThroughUi: true, sourceUnchanged: true };
}
async function runOffer({ page, broker, fixture, current, capture, ctx }) {
  await navigate(page, 'Offer', ctx);
  ctx.mark('OFFER_OPEN');
  const card = page.locator('.ant-card').filter({ has: page.getByText(fixture.company, { exact: true }) });
  await ctx.visible(card);
  check(await card.count() === 1);
  ctx.mark('OFFER_CARD');
  await ctx.click(card.locator('[data-action="start-negotiation"]'));
  const surface = page.getByTestId('offer-negotiation-drawer');
  await ctx.visible(surface);
  await ctx.fill(surface.getByLabel('本次沟通目标', { exact: true }), '确认虚构 Offer 的固定薪资构成');
  await ctx.fill(surface.getByLabel('本次顾虑', { exact: true }), '已知月薪两万元、十二薪；希望先确认是否有额外奖金');
  await ctx.fill(surface.getByLabel('沟通场景', { exact: true }), '虚构 HR 电话演练，不联系任何人');
  ctx.mark('OFFER_REVIEW');
  await ctx.click(button(surface, '下一步：检查输入'));
  await ctx.visible(surface.getByRole('region', exact('确认本次 AI 输入')));
  await broker.armCase(current.id);
  ctx.mark('OFFER_GENERATE');
  await uiResponse(page, `/api/offers/${fixture.offerId}/negotiation/proposals`, 'POST', () => ctx.click(button(surface, '确认生成谈薪准备草稿')), ctx, current.timeoutMs);
  await ctx.visible(surface.locator('[aria-label="谈薪准备草稿"]'));
  check(await surface.getByTestId('offer-negotiation-confirm').isVisible());
  await safeCapture(capture, current.id, page);
  await closeTaskThroughUi(page, surface, page.getByRole('button', exact('关闭任务')), ctx, 'application-offer-review');
  return { inputReviewedThroughUi: true, generatedDraftVisible: true, finalSaveNotSubmitted: true };
}

// One result cannot be certified by a reservation, synthetic UI success, a
// different case's row, or cleanup-generated cancellation.
export function assertProviderCase(snapshot, caseId, previousCount) {
  check(snapshot && Array.isArray(snapshot.requests), 'UNEXPECTED_PROVIDER_REQUESTS');
  const rows = snapshot.requests.filter((row) => row.caseId === caseId);
  check(rows.length === 1 && snapshot.sentRequests === previousCount + 1
    && snapshot.requests.length === snapshot.sentRequests && snapshot.sentRequests <= 8
    && !snapshot.active && rows[0].outboundStarted === true && rows[0].upstreamResponded === true,
  'UNEXPECTED_PROVIDER_REQUESTS');
  check(caseId === 'pilot-cancel' ? rows[0].status === 'DISCONNECT' && rows[0].clientDisconnectObserved === true
    : rows[0].status === 'SETTLED',
    caseId === 'pilot-cancel' ? 'CANCEL_NOT_OBSERVED' : 'PROVIDER_BUDGET_BLOCKED');
}

export function canContinueMockScenario({ mode, result, proof, cleanupPassed, ledgerBefore, ledgerAfter }) {
  return mode === 'mock' && cleanupPassed === true && result?.id === 'pilot-stream'
    && result.status === 'FAIL' && result.code === 'STREAM_NOT_OBSERVED'
    && result.diagnostic?.stage === 'PILOT_STREAM_READBACK'
    && proof?.status === 'PROVEN' && proof.code === 'MOCK_CONTINUATION_PROVEN'
    && ['eligible', 'ledgerSafe', 'mirrorProven', 'domEqual', 'noNewRequests'].every(key => proof[key] === true)
    && ledgerBefore !== undefined && JSON.stringify(ledgerBefore) === JSON.stringify(ledgerAfter);
}
export async function runUiScenarios({ page, haru, api, broker, fixture, capture, mode = 'live', mockContinuation, deadlineMs = Date.now() + 600_000 } = {}) {
  const results = [];
  const continuedSettledFailures = new Set();
  let blocked = null;
  const valid = page && haru && typeof api === 'function' && broker && typeof broker.prepareCase === 'function'
    && typeof broker.armCase === 'function' && typeof broker.cancelCase === 'function'
    && typeof broker.snapshot === 'function' && fixture && Number.isFinite(deadlineMs)
    && ['live', 'mock'].includes(mode)
    && (mockContinuation === undefined || (mode === 'mock' && broker.mode === 'MOCK' && typeof mockContinuation === 'function'));
  if (!valid) blocked = 'INVALID_HARNESS';
  const deadline = Math.min(deadlineMs, Date.now() + 600_000);
  const ctx = operationContext(deadline);
  for (const current of scenarios) {
    if (blocked) { results.push({ id: current.id, status: 'BLOCKED', code: blocked, checks: {} }); continue; }
    if (Date.now() >= deadline) { blocked = 'SUITE_DEADLINE'; results.push({ id: current.id, status: 'BLOCKED', code: blocked, checks: {} }); continue; }
    ctx.mark('CASE_START');
    let requestCountBefore = 0;
    let continuationProof, continuationLedger, liveFingerprint;
    let caseCleanupPassed = true;
    // The immediate next Pilot case cannot inherit the completed stream's
    // positive ID. Prove both real windows reached a fresh idle/null baseline
    // after New Conversation, before arming or sending the new-token request.
    const freshSessionRequired = mode === 'mock' && current.id === 'pilot-hitl-reject'
      && continuedSettledFailures.has('pilot-stream');
    try {
      const initialLedger = broker.snapshot();
      const accounted = results.filter(row => row.status === 'PASS' || continuedSettledFailures.has(row.id));
      check(Array.isArray(initialLedger.requests) && initialLedger.sentRequests === initialLedger.requests.length
        && initialLedger.sentRequests <= 8 && initialLedger.sentRequests === accounted.length
        && initialLedger.requests.every((row) => accounted.some((done) => done.id === row.caseId))
        && new Set(initialLedger.requests.map((row) => row.caseId)).size === initialLedger.requests.length, 'UNEXPECTED_PROVIDER_REQUESTS');
      check(!initialLedger.closed && !initialLedger.active
        && initialLedger.settledMicroCny + initialLedger.retainedMicroCny + initialLedger.reserveMicroCny <= initialLedger.budgetMicroCny, 'PROVIDER_BUDGET_BLOCKED');
      requestCountBefore = initialLedger.sentRequests;
      // Broker is the final spending authority. prepare/arm throws on a sealed
      // or insufficient budget; no retry, alternative model, or direct API.
      await configureCase(page, broker, current, ctx);
      const args = { page, haru, api, broker, fixture, capture, current, ctx, freshSessionRequired, mode, requestCountBefore };
      const outcome = current.id === 'connection' ? await runConnection(args)
        : current.id.startsWith('pilot-') ? await runPilot(args)
          : current.id === 'interview-preparation' ? await runInterview(args)
            : current.id === 'resume-structure' ? await runResume(args) : await runOffer(args);
      // Settle the one admitted call before exposing PASS; cancellation is the
      // only case allowed to retain an unresolved cost reservation.
      ctx.mark('PROVIDER_TERMINAL');
      const livePilot = mode === 'live' && current.id === 'pilot-stream';
      const checks = livePilot ? outcome.checks : outcome;
      if (livePilot) liveFingerprint = liveLedgerFingerprint(broker.snapshot());
      await broker.cancelCase();
      if (livePilot) check(liveLedgerFingerprint(broker.snapshot()) === liveFingerprint, 'LIVE_COMPLETION_UNPROVEN');
      assertProviderCase(broker.snapshot(), current.id, requestCountBefore);
      results.push({ id: current.id, status: 'PASS', code: livePilot ? outcome.code : 'PASSED', checks: { ...checks, oneProviderRequestVerified: true } });
    } catch (error) {
      const code = safeUiCode(error);
      const isBlocked = ['SUITE_DEADLINE', 'PROVIDER_BUDGET_BLOCKED', 'LIVE_COMPLETION_UNPROVEN'].includes(code);
      if (current.id === 'pilot-stream') {
        ctx.stream(await readStreamDiagnostic(page));
        try { ctx.mirror(mirrorDiagnostic(await readMirrorObservation(page, haru), current.id)); }
        catch { ctx.mirror({ observationReadFailed: true, invalidObservation: true }); }
      }
      const diagnostic = await ctx.diagnostic();
      if (!isBlocked && current.id !== 'connection' && typeof capture === 'function') {
        for (const [screen, surface] of [['failure-owner', page], ['failure-haru', haru]]) {
          try { await capture(screen, surface, current.id); } catch { /* Never replace the primary failure. */ }
        }
      }
      const failedResult = { id: current.id, status: isBlocked ? 'BLOCKED' : 'FAIL', code, checks: {}, diagnostic };
      results.push(failedResult);
      blocked = isBlocked ? code : 'PREVIOUS_SCENARIO_FAILED';
      if (typeof mockContinuation === 'function' && current.id === 'pilot-stream'
        && code === 'STREAM_NOT_OBSERVED' && diagnostic.stage === 'PILOT_STREAM_READBACK') {
        try {
          continuationProof = await mockContinuation({ page, haru, broker, result: failedResult,
            previousCount: requestCountBefore, deadlineMs: deadline });
          continuationLedger = broker.snapshot();
          failedResult.continuation = { ...continuationProof, cleanupPassed: false, continued: false };
        } catch { /* Keep the original failure and stop; no fallback approval. */ }
      }
    } finally {
      try { await broker.cancelCase(); } catch {
        caseCleanupPassed = false;
        blocked = 'PROVIDER_BUDGET_BLOCKED';
        const previous = results.at(-1);
        results[results.length - 1] = { ...previous, checks: { ...previous.checks, brokerCleanupFailed: true },
          ...(previous.status === 'PASS' ? { status: 'BLOCKED', code: blocked } : {}) };
      }
      if (['pilot-stream', 'pilot-cancel'].includes(current.id) || freshSessionRequired) {
        try { await removeMirrorObservation(page, haru); }
        catch {
          caseCleanupPassed = false;
          blocked = 'PREVIOUS_SCENARIO_FAILED';
          ctx.mark('CASE_CLEANUP');
          recordObserverCleanupFailure(results, current.id, 'mirror', await ctx.diagnostic());
        }
      }
      if (current.id === 'pilot-stream') { try { await removeStreamObservation(page); } catch {
        caseCleanupPassed = false;
        blocked = 'PREVIOUS_SCENARIO_FAILED';
        ctx.mark('CASE_CLEANUP');
        recordObserverCleanupFailure(results, current.id, 'stream', await ctx.diagnostic());
      } }
      if (liveFingerprint !== undefined && results.at(-1)?.status === 'PASS') {
        try { check(liveLedgerFingerprint(broker.snapshot()) === liveFingerprint, 'LIVE_COMPLETION_UNPROVEN'); }
        catch { caseCleanupPassed = false; blocked = 'LIVE_COMPLETION_UNPROVEN';
          results[results.length - 1] = { ...results.at(-1), status: 'BLOCKED', code: blocked }; }
      }
      const failedResult = results.at(-1);
      if (failedResult?.continuation) {
        failedResult.continuation.cleanupPassed = caseCleanupPassed;
        try {
          if (canContinueMockScenario({ mode, result: failedResult, proof: continuationProof,
            cleanupPassed: caseCleanupPassed, ledgerBefore: continuationLedger, ledgerAfter: broker.snapshot() })) {
            failedResult.continuation.continued = true;
            continuedSettledFailures.add(current.id);
            blocked = null;
          }
        } catch { /* Unreadable ledger is not permission to continue. */ }
      }
    }
  }
  return { schemaVersion: 1, results, allPassed: results.length === scenarios.length && results.every((item) => item.status === 'PASS') };
}
