import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { prepareSyntheticProfile, runUiScenarios, safeUiCode, scenarios, assertProviderCase } from '../ui-scenarios.mjs';

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
function fakeHarness({ testOk = true, saveOk = true, forward = true, failClick = false, budget = false, terminal = 'SETTLED' } = {}) {
  const events = [];
  const filled = new Map();
  let active = null;
  const records = [];
  let current = null;
  const broker = {
    origin: 'http://127.0.0.1:49321',
    prepareCase(caseId) { events.push(`prepare:${caseId}`); current = caseId; return { clientToken: 'local-fixture-token-not-a-provider-key' }; },
    armCase(caseId) { events.push(`arm:${caseId}`); active = caseId; },
    cancelCase() { events.push('cancel'); active = null; },
    snapshot() { return { model: 'deepseek-flash', budgetMicroCny: 10_000_000, reserveMicroCny: 3_000_000,
      sentRequests: records.length, settledMicroCny: 10, retainedMicroCny: budget ? 9_000_000 : 0,
      active: false, closed: false, denied: {}, requests: records.map((row) => ({ ...row })) }; },
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
    async getAttribute(name) { return name === 'aria-checked' ? String(this.path.includes(':启用')) : null; }
    async fill(value) { filled.set(this.path, value); events.push(`fill:${this.path.split('|').pop()}`); }
    async waitFor() {}
    async click() {
      if (this.path.includes('header.op-topbar')) throw Object.assign(new Error('private-provider-output must never be recorded'), { name: 'TimeoutError' });
      if (failClick && this.path.includes('配置 AI')) throw new Error('private-secret-example');
      if (this.path.includes('role:button:保存')) events.push(`save:${current}`);
      if (this.path.includes('role:button:测试连接')) {
        events.push(`test-click:${current}`);
        assert.equal(active, current);
        if (forward) records.push({ caseId: current, status: terminal, cap: 64, outboundStarted: true, upstreamResponded: true });
      }
    }
  }
  class Page extends Locator {
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
    async evaluate(fn) { return true; }
  }
  const page = new Page();
  return { page, haru: new Page(), api: async () => { throw new Error('unexpected direct API'); }, broker, fixture,
    capture: async () => { events.push('capture'); }, events };
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
  assert.equal(fake.events.includes('capture'), false, 'settings never captured');
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
