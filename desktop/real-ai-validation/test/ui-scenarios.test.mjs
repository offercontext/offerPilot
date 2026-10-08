import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { prepareSyntheticProfile, runUiScenarios, safeUiCode, scenarios, assertProviderCase, canContinueMockScenario } from '../ui-scenarios.mjs';

// These are local fake-Page contract tests. They do not run an installed EXE,
// launch Playwright, contact a provider, or establish a real UI pass.
const fixture = { applicationId: 1, eventId: 2, resumeId: 3, offerId: 4,
  jdVersionId: 5, company: 'OfferPilot 合成验收公司', resumeTitle: 'OfferPilot 合成验收简历' };
function fakeSeed(existing = []) {
  const calls = [];
  let next = 0;
  const api = async (path, options) => {
    calls.push({ path, ...options });
    if (path === '/api/applications' && options.method === 'GET') return existing;
    if (options.method === 'PATCH') return { id: Number(path.split('/').pop()), source: 'upload' };
    return { id: ++next, ...options.body };
  };
  return { api, calls };
}
function fakeHarness({ testOk = true, saveOk = true, forward = true, failClick = false, budget = false,
  terminal = 'SETTLED', streamFailure = false, cleanupFailure, ledgerChanged = false, denyFreshBaseline = false } = {}) {
  const events = [];
  const filled = new Map();
  let active = null;
  const records = [];
  let current = null;
  let mutated = false;
  const broker = {
    ...(streamFailure ? { mode: 'MOCK' } : {}),
    origin: 'http://127.0.0.1:49321',
    prepareCase(caseId) { events.push(`prepare:${caseId}`); current = caseId; return { clientToken: `local-fixture-token-${caseId}-not-a-provider-key` }; },
    armCase(caseId) { events.push(`arm:${caseId}`); if (streamFailure && caseId === 'pilot-hitl-reject') throw new Error('end synthetic integration test'); active = caseId; },
    cancelCase() { events.push('cancel'); if (current === 'pilot-stream' && cleanupFailure === 'broker') throw new Error('cleanup failed'); active = null; },
    snapshot() { return { model: 'deepseek-flash', budgetMicroCny: 10_000_000, reserveMicroCny: 3_000_000,
      sentRequests: records.length, settledMicroCny: 10, retainedMicroCny: budget ? 9_000_000 : 0,
      active: false, closed: false, denied: mutated ? { AUTH: 1 } : {}, requests: records.map((row) => ({ ...row })) }; },
  };
  class Locator {
    constructor(path = '') { this.path = path; }
    locator(selector) { return new Locator(`${this.path}|locator:${selector}`); }
    getByRole(role, options = {}) { return new Locator(`${this.path}|role:${role}:${options.name ?? ''}`); }
    getByLabel(label) { return new Locator(`${this.path}|label:${label}`); }
    getByTestId(label) { return new Locator(`${this.path}|test:${label}`); }
    getByText(label) { return new Locator(`${this.path}|text:${label}`); }
    getByTitle(label) { return new Locator(`${this.path}|title:${label}`); }
    getByPlaceholder(label) { return new Locator(`${this.path}|placeholder:${label}`); }
    filter(value) { return new Locator(`${this.path}|filter:${String(value.hasText ?? '')}`); }
    or(other) { return new Locator(`${this.path}|or:${other.path}`); }
    and(other) { return new Locator(`${this.path}|and:${other.path}`); }
    async count() { return 1; }
    async isVisible() { return !this.path.includes('关闭任务') && !this.path.includes('退出沉浸模式，返回原页面'); }
    async getAttribute(name) {
      if (this.path.includes('role:switch:原生 JSON Schema') && !this.path.includes('label:原生 JSON Schema'))
        throw Object.assign(new Error('tooltip accessible-name mismatch'), { name: 'TimeoutError' });
      return name === 'aria-checked' ? String(this.path.includes(':启用')) : null;
    }
    async fill(value) { filled.set(this.path, value); events.push(`fill:${this.path.split('|').pop()}`); }
    async waitFor() {}
    async click() {
      if (!streamFailure && this.path.includes('header.op-topbar')) throw Object.assign(new Error('private-provider-output must never be recorded'), { name: 'TimeoutError' });
      if (failClick && this.path.includes('配置 AI')) throw new Error('private-secret-example');
      if (this.path.includes('role:button:保存')) events.push(`save:${current}`);
      if (this.path.includes('role:button:测试连接')) {
        events.push(`test-click:${current}`);
        assert.equal(active, current);
        if (forward) records.push({ caseId: current, status: terminal, cap: 64, outboundStarted: true, upstreamResponded: true });
      }
      if (streamFailure && this.path.endsWith('role:button:发送')) {
        assert.equal(active, 'pilot-stream');
        records.push({ caseId: current, status: 'SETTLED', cap: 4096, outboundStarted: true, upstreamResponded: true });
      }
      if (streamFailure && this.path.includes('新建对话')) events.push(`new-conversation:${current}`);
    }
  }
  class Page extends Locator {
    constructor(role) { super(); this.role = role; }
    url() { return 'http://127.0.0.1:41111/?view=settings'; }
    async waitForResponse(predicate) {
      const isTest = events.includes(`save:${current}`);
      events.push(isTest ? 'wait-test' : 'wait-save');
      const path = isTest ? '/api/settings/providers/test' : '/api/settings';
      const response = { url: () => `http://127.0.0.1:41111${path}`,
        request: () => ({ method: () => isTest ? 'POST' : 'PUT' }), status: () => 200,
        json: async () => isTest ? { ok: testOk } : { chat_auto_approve_writes: !saveOk,
          fallback_provider_ids: [], providers: [{ base_url: `${broker.origin}/v1`, model: 'deepseek-flash', has_api_key: true,
            max_output_tokens: Number([...filled].find(([key]) => key.endsWith('label:单次最大输出（tokens）'))?.[1] ?? 64) }] } };
      assert.equal(predicate(response), true);
      return response;
    }
    async evaluate(fn, arg) {
      if (!streamFailure) return true;
      if (arg?.operation) {
        events.push(`observer-${arg.operation}:${current}:${this.role}`);
        if (arg.operation === 'remove') {
          if (cleanupFailure === 'mirror' && current === 'pilot-stream') throw new Error('cleanup failed');
          return true;
        }
        return { role: this.role, caseId: arg.caseId, installed: true,
          baselineReady: !(denyFreshBaseline && current === 'pilot-hitl-reject'), healthy: true, connected: true,
          currentTaskState: 'idle', loading: false, hasPending: false,
          bridgeRunningObserved: true, domRunningObserved: true, runningWithNullObserved: false,
          conversationId: 73, runningConversationId: 73, identityChanged: false,
          generationChanged: false, readTimedOut: false, expired: false };
      }
      const source = fn.toString();
      if (source.includes('__offerpilotBoundedUiObserver?.updates')) return false;
      if (source.includes('__offerpilotBoundedUiObserver?.summary')) return { installed: true };
      if (source.includes('__offerpilotBoundedUiObserver?.observer.disconnect')) {
        events.push('stream-cleanup');
        if (cleanupFailure === 'stream') throw new Error('cleanup failed');
        if (ledgerChanged) mutated = true;
      }
      return true;
    }
  }
  const page = new Page('owner');
  return { page, haru: new Page('haru'), api: async () => { throw new Error('unexpected direct API'); }, broker, fixture,
    capture: async (screenId, _page, caseId) => { events.push(`capture:${screenId}:${caseId || screenId}`); }, events };
}

test('one synthetic profile uses only explicit seed APIs and returns no raw record', async () => {
  const fake = fakeSeed();
  const result = await prepareSyntheticProfile(fake.api);
  assert.deepEqual(fake.calls.map(({ method, path }) => `${method} ${path}`), [
    'GET /api/applications', 'POST /api/applications', 'POST /api/applications/1/job-description/versions',
    'POST /api/resumes', 'PATCH /api/resumes/3', 'POST /api/application-events', 'POST /api/offers',
  ]);
  assert.equal(result.applicationId, 1);
  assert.equal(result.resumeId, 3);
  assert.equal(result.eventId, 4);
  assert.equal(result.offerId, 5);
  assert.equal(fake.calls[5].body.event_type, 'interview');
  assert.ok(Date.parse(fake.calls[5].body.scheduled_at) > Date.now());
  assert.deepEqual(Object.keys(result).sort(), ['applicationId', 'company', 'eventId', 'jdVersionId', 'offerId', 'resumeId', 'resumeTitle']);
  assert.equal(JSON.stringify(result).includes('raw_text'), false);
});

test('nonempty profile is refused before any mutation', async () => {
  const fake = fakeSeed([{ id: 99 }]);
  await assert.rejects(() => prepareSyntheticProfile(fake.api), { code: 'SYNTHETIC_PROFILE_INVALID' });
  assert.equal(fake.calls.length, 1);
});

test('untrusted setup IDs stop seeding rather than reaching derived endpoints', async () => {
  const calls = [];
  await assert.rejects(() => prepareSyntheticProfile(async (path) => {
    calls.push(path); return calls.length === 1 ? [] : { id: '1/../../settings' };
  }), { code: 'SYNTHETIC_PROFILE_INVALID' });
  assert.deepEqual(calls, ['/api/applications', '/api/applications']);
});

test('connection is UI save then arm then actual click and exactly one settled provider request', async () => {
  const fake = fakeHarness();
  const result = await runUiScenarios(fake);
  assert.equal(result.results[0].status, 'PASS');
  assert.equal(result.results[0].checks.oneProviderRequestVerified, true);
  const at = (value) => fake.events.indexOf(value);
  assert.ok(at('prepare:connection') < at('save:connection'));
  assert.ok(at('save:connection') < at('arm:connection'));
  assert.ok(at('arm:connection') < at('test-click:connection'));
  assert.equal(fake.events.some(event => event.startsWith('capture:') && event.endsWith(':connection')), false, 'settings never captured');
  assert.equal(result.results[1].status, 'FAIL');
  assert.equal(result.results[1].code, 'UI_TIMEOUT');
  assert.ok(result.results.slice(2).every(({ status }) => status === 'BLOCKED'));
  assert.equal(result.allPassed, false);
  assert.doesNotMatch(JSON.stringify(result), /private|local-fixture-token|49321/);
});

test('UI connection success without an outbound broker row can never pass', async () => {
  const fake = fakeHarness({ forward: false });
  const result = await runUiScenarios(fake);
  assert.equal(result.results[0].status, 'FAIL');
  assert.equal(result.results[0].code, 'UNEXPECTED_PROVIDER_REQUESTS');
  assert.ok(result.results.slice(1).every(({ status }) => status === 'BLOCKED'));
});

test('unsettled provider usage never passes a normal scenario', async () => {
  const fake = fakeHarness({ terminal: 'USAGE' });
  const result = await runUiScenarios(fake);
  assert.equal(result.results[0].status, 'BLOCKED');
  assert.equal(result.results[0].code, 'PROVIDER_BUDGET_BLOCKED');
  assert.ok(result.results.every(({ code }) => code === 'PROVIDER_BUDGET_BLOCKED'));
});

test('insufficient reserved budget blocks every remaining scenario before UI or prepare', async () => {
  const fake = fakeHarness({ budget: true });
  const result = await runUiScenarios(fake);
  assert.ok(result.results.every(({ status, code }) => status === 'BLOCKED' && code === 'PROVIDER_BUDGET_BLOCKED'));
  assert.ok(fake.events.every((value) => value === 'cancel'));
});

test('saved settings must keep HITL enabled before the connection is armed', async () => {
  const fake = fakeHarness({ saveOk: false });
  const result = await runUiScenarios(fake);
  assert.equal(result.results[0].code, 'SETTINGS_SAVE_FAILED');
  assert.equal(fake.events.some((value) => value.startsWith('arm:')), false);
});

test('connection failure is explicit and aborts later spending', async () => {
  const result = await runUiScenarios(fakeHarness({ testOk: false }));
  assert.equal(result.results[0].code, 'CONNECTION_FAILED');
  assert.ok(result.results.slice(1).every(({ status }) => status === 'BLOCKED'));
});

test('unsafe errors are reduced to a fixed enum and no raw exception fields escape', async () => {
  const result = await runUiScenarios(fakeHarness({ failClick: true }));
  assert.equal(result.results[0].code, 'UI_ACTION_FAILED');
  assert.doesNotMatch(JSON.stringify(result), /private-secret/);
  assert.equal(safeUiCode({ code: 'stolen-key', message: 'private' }), 'UI_ACTION_FAILED');
  assert.equal(safeUiCode({ code: 'BUDGET', message: 'private' }), 'PROVIDER_BUDGET_BLOCKED');
});

test('expired global deadline never starts a UI action or provider case', async () => {
  const fake = fakeHarness();
  const result = await runUiScenarios({ ...fake, deadlineMs: Date.now() - 1 });
  assert.ok(result.results.every(({ status, code }) => status === 'BLOCKED' && code === 'SUITE_DEADLINE'));
  assert.deepEqual(fake.events, []);
});

test('missing harness inputs fail closed and fixed case inventory stays within 8 requests', async () => {
  const result = await runUiScenarios();
  assert.equal(scenarios.length, 7);
  assert.equal(new Set(scenarios.map(({ id }) => id)).size, 7);
  assert.equal(scenarios.at(-1).id, 'pilot-cancel');
  assert.ok(result.results.every(({ status, code }) => status === 'BLOCKED' && code === 'INVALID_HARNESS'));
});

test('implementation observes actual renderer state and contains no direct AI API or export shortcut', async () => {
  const source = await fs.readFile(new URL('../ui-scenarios.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /settings-export|fetch\(|\.publish\(|console\.|\.headers\(|\.postData\(|\.route\(|\.goto\(|\.tracing\./);
  assert.match(source, /getState\(\)/);
  assert.match(source, /MutationObserver/);
  assert.match(source, /rows\[0\]\.status === 'RESERVED' && ledger\.active === true/);
  assert.match(source, /rows\[0\]\.status === 'DISCONNECT' && ledger\.active === false/);
  assert.doesNotMatch(source, /\['SETTLED', 'CANCELLED', 'DISCONNECT'\]/);
  assert.match(source, /const clean = await surface\.evaluate/);
  assert.match(source, /api_key/);
  assert.match(source, /最终拒绝/);
  assert.match(source, /interview-preparation-generate/);
  assert.match(source, /开始分类/);
  assert.match(source, /确认生成谈薪准备草稿/);
});


test('provider proof requires both transport finish and real upstream response', () => {
  const good = { sentRequests: 1, active: false, requests: [{ caseId: 'connection', status: 'SETTLED', outboundStarted: true, upstreamResponded: true }] };
  assert.doesNotThrow(() => assertProviderCase(good, 'connection', 0));
  for (const field of ['outboundStarted', 'upstreamResponded']) {
    const missing = structuredClone(good); missing.requests[0][field] = false;
    assert.throws(() => assertProviderCase(missing, 'connection', 0), { code: 'UNEXPECTED_PROVIDER_REQUESTS' });
  }
  assert.throws(() => assertProviderCase(good, 'pilot-stream', 0), { code: 'UNEXPECTED_PROVIDER_REQUESTS' });
  assert.throws(() => assertProviderCase({ ...good, sentRequests: 2 }, 'connection', 0), { code: 'UNEXPECTED_PROVIDER_REQUESTS' });
});

test('cancel proof rejects harness cancellation, completed work and an active request', () => {
  const make = (status) => ({ sentRequests: 1, active: false, requests: [{ caseId: 'pilot-cancel', status, outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: true }] });
  assert.doesNotThrow(() => assertProviderCase(make('DISCONNECT'), 'pilot-cancel', 0));
  for (const status of ['CANCELLED', 'SETTLED', 'RESERVED', 'TIMEOUT', 'USAGE']) {
    assert.throws(() => assertProviderCase(make(status), 'pilot-cancel', 0), { code: 'CANCEL_NOT_OBSERVED' });
  }
  assert.throws(() => assertProviderCase({ ...make('DISCONNECT'), active: true }, 'pilot-cancel', 0), { code: 'UNEXPECTED_PROVIDER_REQUESTS' });
});


test('an upstream-only disconnect cannot certify product cancellation', () => {
  for (const clientDisconnectObserved of [false, undefined]) {
    const snapshot = { sentRequests: 1, active: false, requests: [{ caseId: 'pilot-cancel', status: 'DISCONNECT',
      outboundStarted: true, upstreamResponded: true, clientDisconnectObserved }] };
    assert.throws(() => assertProviderCase(snapshot, 'pilot-cancel', 0), { code: 'CANCEL_NOT_OBSERVED' });
  }
  const normallySettled = { sentRequests: 1, active: false, requests: [{ caseId: 'connection', status: 'SETTLED',
    outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: false }] };
  assert.doesNotThrow(() => assertProviderCase(normallySettled, 'connection', 0));
});


test('tooltip-decorated switch uses exact form label intersected with role', async () => {
  const fake = fakeHarness();
  const result = await runUiScenarios(fake);
  assert.equal(result.results[0].status, 'PASS');
  const source = await fs.readFile(new URL('../ui-scenarios.mjs', import.meta.url), 'utf8');
  assert.ok(source.includes("scope.getByLabel(label, { exact: true }).and(scope.getByRole('switch'))"));
  assert.equal(source.includes("scope.getByRole('switch', exact(label))"), false);
});

test('failed UI action preserves a fixed stage and bounded target booleans', async () => {
  const result = await runUiScenarios(fakeHarness({ failClick: true }));
  assert.equal(result.results[0].diagnostic.stage, 'SETTINGS_OPEN');
  assert.equal(result.results[0].diagnostic.targetProbed, true);
  assert.equal(result.results[0].diagnostic.targetUnique, true);
  assert.doesNotMatch(JSON.stringify(result), /private-secret-example|local-fixture-token/);
});

test('running proof is installed before send and stream does not re-wait a vanished Stop button', async () => {
  const source = await fs.readFile(new URL('../ui-scenarios.mjs', import.meta.url), 'utf8');
  const pilot = source.slice(source.indexOf('async function runPilot('), source.indexOf('async function runConnection('));
  assert.ok(pilot.indexOf('installMirrorObservation(page, haru, current.id)') < pilot.indexOf('await broker.armCase(current.id)'));
  assert.ok(pilot.indexOf('installMirrorObservation(page, haru, current.id)') < pilot.indexOf("ctx.mark('PILOT_SEND')"));
  assert.equal(pilot.includes("waitMirror(page, haru, 'running'"), false);
  assert.ok(pilot.includes('await observedRunning(page, haru, current, ctx)'));
  const cancellation = pilot.slice(pilot.indexOf("if (current.id === 'pilot-cancel') {"));
  assert.ok(cancellation.includes('await stop.isVisible() && await stop.isEnabled()'));
  assert.ok(cancellation.includes("rows[0].status === 'RESERVED' && ledger.active === true"));
  assert.ok(cancellation.includes("rows[0].status === 'DISCONNECT' && ledger.active === false"));
  assert.ok(cancellation.includes('rows[0].clientDisconnectObserved === true'));
  assert.equal(pilot.slice(0, pilot.indexOf("if (current.id === 'pilot-cancel') {")).includes('await stop.isVisible()'), false);
  assert.ok(pilot.includes('await verifyFinalMirrorIdentity(page, haru, current, ctx)'));
  assert.ok(pilot.includes('await assertHaruRendered(haru, ctx)'));
  assert.ok(source.includes('await removeMirrorObservation(page, haru)'));
});

test('mirror diagnostic reports only bounded facts and never persists conversation IDs', async () => {
  const { mirrorDiagnostic } = await import('../ui-scenarios.mjs');
  const one = { caseId: 'pilot-stream', installed: true, baselineReady: true, healthy: true, connected: true,
    bridgeRunningObserved: true, domRunningObserved: true, runningWithNullObserved: false,
    conversationId: 37, runningConversationId: 37, identityChanged: false, generationChanged: false, readTimedOut: false, expired: false };
  const value = mirrorDiagnostic({ owner: { ...one, raw: 'private-key' }, haru: { ...one, message: 'private-response' } }, 'pilot-stream');
  assert.equal(value.sameRunningConversation, true); assert.equal(value.currentConversationMatches, true);
  assert.equal(value.ownerRunningPositiveSeen, true); assert.equal(value.haruRunningDomSeen, true);
  assert.equal(value.invalidObservation, false);
  assert.ok(Object.values(value).every(item => typeof item === 'boolean'));
  assert.doesNotMatch(JSON.stringify(value), /37|private|conversationId|message/);
  assert.equal(mirrorDiagnostic({ owner: one, haru: { ...one, runningConversationId: null } }, 'pilot-stream').sameRunningConversation, false);
  assert.equal(mirrorDiagnostic({ owner: one, haru: { ...one, identityChanged: true } }, 'pilot-stream').invalidObservation, true);
});

test('final mirror proof requires current idle on both windows after a real running transition', async () => {
  const { isFinalMirrorProven } = await import('../ui-scenarios.mjs');
  const value = { caseId: 'pilot-stream', installed: true, baselineReady: true, healthy: true, connected: true,
    bridgeRunningObserved: true, domRunningObserved: true, runningWithNullObserved: false,
    conversationId: 37, runningConversationId: 37, identityChanged: false, generationChanged: false,
    readTimedOut: false, expired: false, currentTaskState: 'idle', loading: false, hasPending: false };
  const good = { owner: { ...value, role: 'owner' }, haru: { ...value, role: 'haru' } };
  assert.equal(isFinalMirrorProven(good), true);
  for (const role of ['owner', 'haru']) for (const changes of [{ currentTaskState: 'running', loading: true },
    { currentTaskState: 'waiting_confirmation', hasPending: true }, { currentTaskState: 'completed' },
    { loading: true }, { hasPending: true }, { conversationId: 38 }])
    assert.equal(isFinalMirrorProven({ ...good, [role]: { ...good[role], ...changes } }), false);
});

test('both observer cleanup failures preserve the original UI failure and diagnosis', async () => {
  const { recordObserverCleanupFailure } = await import('../ui-scenarios.mjs');
  const diagnostic = { stage: 'PILOT_RUNNING', targetProbed: false };
  const rows = [{ id: 'pilot-stream', status: 'FAIL', code: 'HARU_SYNC_FAILED', diagnostic, checks: {} }];
  recordObserverCleanupFailure(rows, 'pilot-stream', 'mirror', { stage: 'CASE_CLEANUP' });
  recordObserverCleanupFailure(rows, 'pilot-stream', 'stream', { stage: 'CASE_CLEANUP' });
  assert.equal(rows[0].code, 'HARU_SYNC_FAILED'); assert.equal(rows[0].diagnostic, diagnostic);
  assert.equal(rows[0].checks.mirrorObserverCleanupFailed, true); assert.equal(rows[0].checks.streamObserverCleanupFailed, true);
  const passed = [{ id: 'pilot-stream', status: 'PASS', code: 'PASSED', checks: {} }];
  recordObserverCleanupFailure(passed, 'pilot-stream', 'stream', { stage: 'CASE_CLEANUP' });
  assert.equal(passed[0].status, 'FAIL'); assert.equal(passed[0].code, 'UI_ACTION_FAILED');
});

test('stream observer cleanup is bounded to five seconds', async t => {
  const { removeStreamObservation } = await import('../ui-scenarios.mjs');
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const pending = removeStreamObservation({ evaluate: () => new Promise(() => {}) });
  const rejected = assert.rejects(pending, { code: 'UI_ACTION_FAILED' });
  t.mock.timers.tick(5000);
  await rejected;
});

test('only the reviewed MOCK proof and unchanged cleanup ledger permit the next case', () => {
  const args = { mode: 'mock', result: { id: 'pilot-stream', status: 'FAIL', code: 'STREAM_NOT_OBSERVED',
    diagnostic: { stage: 'PILOT_STREAM_READBACK' } }, proof: { status: 'PROVEN', code: 'MOCK_CONTINUATION_PROVEN',
    eligible: true, ledgerSafe: true, mirrorProven: true, domEqual: true, noNewRequests: true },
    cleanupPassed: true, ledgerBefore: { sentRequests: 2, active: false }, ledgerAfter: { sentRequests: 2, active: false } };
  assert.equal(canContinueMockScenario(args), true);
  for (const change of [{ mode: 'live' }, { mode: undefined }, { cleanupPassed: false }, { proof: undefined },
    { ledgerBefore: undefined }, { ledgerAfter: { sentRequests: 3, active: false } },
    { ledgerAfter: { sentRequests: 2, active: true } }])
    assert.equal(canContinueMockScenario({ ...args, ...change }), false);
  for (const key of ['eligible', 'ledgerSafe', 'mirrorProven', 'domEqual', 'noNewRequests'])
    assert.equal(canContinueMockScenario({ ...args, proof: { ...args.proof, [key]: false } }), false);
  for (const change of [{ id: 'pilot-cancel' }, { code: 'CANCEL_NOT_OBSERVED' }, { code: 'HITL_NOT_OBSERVED' },
    { code: 'HARU_SYNC_FAILED' }, { status: 'PASS' }, { status: 'BLOCKED' }, { diagnostic: { stage: 'PILOT_RUNNING' } }])
    assert.equal(canContinueMockScenario({ ...args, result: { ...args.result, ...change } }), false);
});

test('LIVE and an unmarked broker reject continuation before any UI or provider preparation', async () => {
  for (const mode of ['live', 'mock']) {
    const fake = fakeHarness(); let called = false;
    const result = await runUiScenarios({ ...fake, mode, mockContinuation: async () => { called = true; } });
    assert.ok(result.results.every(row => row.status === 'BLOCKED' && row.code === 'INVALID_HARNESS'));
    assert.equal(called, false); assert.equal(fake.events.length, 0);
  }
});

// This fake-Page test exercises the real case loop, cleanup and accounting.
// The independently tested terminal gate is injected as a fixed local proof;
// neither these synthetic pages nor the proof certify Windows E2E coverage.
async function runContinuedLoop(t, options = {}, accepted = true) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const fake = fakeHarness({ streamFailure: true, ...options });
  let done = false, failure;
  const pending = runUiScenarios({ ...fake, mode: 'mock', mockContinuation: async args => {
    fake.events.push('prove-terminal');
    assert.equal(args.previousCount, 1); assert.equal(args.result.status, 'FAIL');
    return { status: accepted ? 'PROVEN' : 'BLOCKED', code: accepted ? 'MOCK_CONTINUATION_PROVEN' : 'MIRROR_UNPROVEN',
      eligible: true, ledgerSafe: true, mirrorProven: accepted, domEqual: accepted, noNewRequests: accepted };
  } });
  pending.then(() => { done = true; }, error => { done = true; failure = error; });
  for (let n = 0; !done && n < 2000; n += 1) {
    for (let i = 0; i < 40; i += 1) await Promise.resolve();
    t.mock.timers.tick(100);
  }
  assert.equal(done, true, 'bounded synthetic integration loop');
  if (failure) throw failure;
  return { result: await pending, events: fake.events };
}

test('settled failed stream continues only after cleanup, next token and fresh two-window baseline', async t => {
  const { result, events } = await runContinuedLoop(t);
  assert.equal(result.results[0].status, 'PASS');
  assert.equal(result.results[1].status, 'FAIL'); assert.equal(result.results[1].code, 'STREAM_NOT_OBSERVED');
  assert.equal(result.results[1].continuation.continued, true); assert.equal(result.allPassed, false);
  for (const event of ['observer-remove:pilot-stream:owner', 'observer-remove:pilot-stream:haru', 'stream-cleanup'])
    assert.ok(events.indexOf(event) < events.indexOf('prepare:pilot-hitl-reject'));
  assert.ok(events.indexOf('prepare:pilot-hitl-reject') < events.indexOf('new-conversation:pilot-hitl-reject'));
  for (const role of ['owner', 'haru'])
    assert.ok(events.indexOf(`observer-install:pilot-hitl-reject:${role}`) < events.indexOf('arm:pilot-hitl-reject'));
  assert.equal(result.results[2].status, 'FAIL', 'deliberate stop at next independent arm');
  assert.ok(result.results.slice(3).every(row => row.status === 'BLOCKED'));
});

for (const options of [{ cleanupFailure: 'broker' }, { cleanupFailure: 'mirror' }, { cleanupFailure: 'stream' },
  { ledgerChanged: true }]) test(`post-proof boundary ${JSON.stringify(options)} prevents all later preparation`, async t => {
  const { result, events } = await runContinuedLoop(t, options);
  assert.equal(result.results[1].status, 'FAIL'); assert.equal(result.results[1].code, 'STREAM_NOT_OBSERVED');
  assert.equal(result.results[1].continuation.continued, false);
  assert.ok(result.results.slice(2).every(row => row.status === 'BLOCKED'));
  assert.equal(events.includes('prepare:pilot-hitl-reject'), false);
});

test('failed terminal proof cannot clear the fail-closed barrier', async t => {
  const { result, events } = await runContinuedLoop(t, {}, false);
  assert.equal(result.results[1].continuation.continued, false);
  assert.equal(events.includes('prepare:pilot-hitl-reject'), false);
});

test('failed fresh-session baseline cannot arm the next request after diagnostic continuation', async t => {
  const { result, events } = await runContinuedLoop(t, { denyFreshBaseline: true });
  assert.equal(result.results[1].continuation.continued, true);
  assert.equal(result.results[2].code, 'HARU_SYNC_FAILED');
  assert.equal(events.includes('arm:pilot-hitl-reject'), false);
  assert.ok(result.results.slice(3).every(row => row.status === 'BLOCKED'));
});
