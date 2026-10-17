import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { Ledger, POLICY } from '../broker-core.cjs';
import { readHaruSessionRenderer, assertHaruSessionPair, mockSessionLedger, createMockHaruSession, assertHaruRestartFacts, readStoredHaruConversation, HARU_SESSION_DIAGNOSTIC_FLAGS } from '../mock-haru-session.mjs';
import { executeValidation } from '../validation-runner.mjs';
import { runUiScenarios, pilotPrompt } from '../ui-scenarios.mjs';
import { safeResults, safeHaruSessionDiagnostic } from '../safe-evidence.mjs';
import { CASES } from '../contract.mjs';
const ORIGIN = 'http://127.0.0.1:49321';
const BODY = '【MOCK 合成回复】边界值检查正文。';
function renderer(role = 'owner') {
  const owner = role === 'owner';
  const node = (extra = {}) => ({ isConnected: true, hidden: false, parentElement: null,
    style: { display: 'block', visibility: 'visible', opacity: '1', overflowX: 'visible', overflowY: 'visible' },
    getClientRects: () => [{}], getBoundingClientRect: () => ({ left: 10, top: 10, right: 130, bottom: 90 }), ...extra });
  const root = node({ getAttribute: () => 'light' }), input = node({ value: '' });
  const body = node({ textContent: BODY, parentElement: root });
  const roots = [root];
  root.querySelector = selector => selector.startsWith('article') ? null : input;
  root.querySelectorAll = () => body.isConnected ? [body] : [];
  body.remove = () => { body.isConnected = false; };
  root.cloneNode = () => node();
  const document = { visibilityState: 'visible', documentElement: { getAttribute: () => 'light' },
    body: { append: value => roots.push(value) }, querySelector: () => root, querySelectorAll: () => roots };
  const state = { connected: true, generation: 2, snapshot: { conversationId: 7, taskState: 'idle', loading: false,
    hasPending: false, canStop: false, stopping: false, canSend: true, error: '', stopMessage: '', messages: [{ role: 'assistant', content: BODY }] } };
  const window = { document, offerpilotDesktop: { role, getState: async () => state } };
  let location = new URL(`${ORIGIN}/${owner ? '?view=pilot' : '?desktopSurface=haru'}`);
  const read = () => vm.runInNewContext(`(${readHaruSessionRenderer.toString()})(args)`, {
    window, document, location, URLSearchParams, innerWidth: 1008, innerHeight: 689,
    getComputedStyle: value => value.style, args: { role, origin: ORIGIN },
  });
  return { window, state, body, read, reconfigure: value => { location = new URL(value.url); }, close() {} };
}
test('real reader accepts Pilot query routing and the exact Haru role, then proves matching visible body', async () => {
  const owner = renderer(), haru = renderer('haru');
  try {
    const pair = { owner: await owner.read(), haru: await haru.read() };
    assert.deepEqual(assertHaruSessionPair(pair), { conversationId: 7, text: BODY, theme: 'light' });
    for (const content of ['', 'wrong body']) {
      haru.state.snapshot.messages[0].content = content;
      const mismatched = await haru.read();
      assert.throws(() => assertHaruSessionPair({ owner: pair.owner, haru: mismatched }), { safeCode: 'HARU_SYNC_FAILED' });
    }
    assert.throws(() => assertHaruSessionPair(pair, { conversationId: 7, text: 'different persisted body' }), { safeCode: 'HARU_SYNC_FAILED' });
    assert.throws(() => assertHaruSessionPair(pair, { conversationId: 8, text: BODY }), { safeCode: 'HARU_SYNC_FAILED' });
  } finally { owner.close(); haru.close(); }
});
for (const role of ['owner', 'haru']) {
  for (const hidden of ['display', 'opacity', 'offscreen', 'wrong-surface', 'duplicate-root', 'wrong-role', 'foreign-origin']) {
    test(`${role} rejects ${hidden} instead of using the other window or snapshot as evidence`, async () => {
      const value = renderer(role);
      try {
        if (hidden === 'display') value.body.style.display = 'none';
        if (hidden === 'opacity') value.body.parentElement.style.opacity = '0';
        if (hidden === 'offscreen') value.body.getBoundingClientRect = () => ({ left: 2000, top: 2000, right: 2120, bottom: 2080 });
        if (hidden === 'wrong-surface') value.body.remove();
        if (hidden === 'duplicate-root') value.window.document.body.append(value.window.document.querySelector('main').cloneNode(true));
        if (hidden === 'wrong-role') value.window.offerpilotDesktop.role = role === 'owner' ? 'haru' : 'owner';
        if (hidden === 'foreign-origin') value.reconfigure({ url: 'http://127.0.0.1:49322/' });
        assert.equal((await value.read()).ready, false);
      } finally { value.close(); }
    });
  }
}
function brokerFixture() {
  const value = { ...new Ledger().snapshot(), mode: 'MOCK', journalFailed: false, active: false, closed: false,
    sentRequests: 2, requests: [{ caseId: 'connection', status: 'SETTLED' }, { caseId: 'pilot-stream', status: 'SETTLED',
      outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: false }],
    mock: { transport: 'IN_PROCESS_SYNTHETIC_HTTPS', externalNetwork: 'DENIED', realProviderCalls: 0, usageIsSynthetic: true, fakeRequests: 2 } };
  return { value, broker: { mode: 'MOCK', snapshot: () => value } };
}
test('the session gate rejects active, unverified or extra-denied MOCK activity and detects extra sent requests', () => {
  const { broker, value } = brokerFixture();
  const before = mockSessionLedger(broker);
  for (const patch of [{ mode: 'LIVE' }, { active: true }, { closed: true }, { model: 'other' },
    { budgetMicroCny: POLICY.budgetMicroCny + 1 }, { denied: { ...value.denied, AUTH: 1 } }]) {
    const altered = { ...value, ...patch };
    assert.throws(() => mockSessionLedger({ mode: 'MOCK', snapshot: () => altered }));
  }
  value.requests.push({ caseId: 'extra', status: 'SETTLED' }); value.sentRequests++; value.mock.fakeRequests++;
  assert.notEqual(mockSessionLedger(broker), before);
});
test('the live entrypoint rejects the new callback before host or broker access', async () => {
  let called = false;
  const result = await runUiScenarios({ mode: 'live', page: {}, haru: {}, fixture: {}, api: async () => {},
    broker: { mode: 'MOCK', prepareCase() { called = true; }, armCase() {}, cancelCase() {}, snapshot() {} },
    mockHaruSession() { called = true; } });
  assert.ok(result.results.every(row => row.code === 'INVALID_HARNESS'));
  await assert.rejects(executeValidation({ mode: 'live', brokerFactory() { called = true; },
    mockHaruSessionFactory() { called = true; } }), { safeCode: 'INVALID_HARNESS' });
  assert.equal(called, false);
  assert.throws(() => createMockHaruSession({ mode: 'live', broker: brokerFixture().broker }), { safeCode: 'INVALID_HARNESS' });
});
function launches() {
  const first = { identity: { exe: 'C:\\mock\\OfferPilot Desktop.exe', profile: 'C:\\mock\\profile' }, port: 49321,
    owned: { main: { pid: 10, created: 'first-main' }, backend: { pid: 11, created: 'first-backend' } } };
  const second = { ...first, identity: { ...first.identity }, owned: { main: { pid: 20, created: 'second-main' }, backend: { pid: 21, created: 'second-backend' } } };
  return { first, second };
}
test('restart proof requires disappeared processes/ports and a new same-EXE same-profile process pair', () => {
  const { first, second } = launches();
  assert.equal(assertHaruRestartFacts(first, second, { processes: [] }, true).sameProfileAndExe, true);
  assert.throws(() => assertHaruRestartFacts(first, second, { processes: [{ pid: 10 }] }, true));
  assert.throws(() => assertHaruRestartFacts(first, second, { processes: [] }, false));
  for (const patch of [{ identity: { ...second.identity, profile: 'C:\\other-profile' } },
    { identity: { ...second.identity, exe: 'C:\\other.exe' } }, { owned: first.owned }, { port: 49322 }]) {
    assert.throws(() => assertHaruRestartFacts(first, { ...second, ...patch }, { processes: [] }, true));
  }
});

test('session evidence permits only fixed boolean keys in MOCK pilot-stream', () => {
  const rows = CASES.map(id => ({ id, status: 'BLOCKED', code: 'NOT_STARTED', checks: {} }));
  const stream = rows.find(row => row.id === 'pilot-stream');
  stream.checks.haruBrokerHistoryVisible = true;
  assert.equal(safeResults(rows, true).find(row => row.id === 'pilot-stream').checks.haruBrokerHistoryVisible, true);
  assert.throws(() => safeResults(rows));
  stream.checks.haruBrokerHistoryVisible = BODY;
  assert.throws(() => safeResults(rows, true));
  delete stream.checks.haruBrokerHistoryVisible;
  rows[0].checks.haruBrokerHistoryVisible = true;
  assert.throws(() => safeResults(rows, true));
});

// Exercises the actual session orchestration with local fake Page/native ports.
// This proves control ordering and rejection, never a Windows product pass.
function sessionHarness({ storedTitleUnique = true, counts = [], wrongBody = false, wrongTheme = false } = {}) {
  const { broker, value } = brokerFixture();
  const events = [], inputs = { owner: '', haru: '' };
  let theme = 'light', restarted = false;
  class Surface {
    constructor(role, selector = '') { this.role = role; this.selector = selector; }
    locator(selector) { return new Surface(this.role, `${this.selector}|${selector}`); }
    getByRole(_role, options) { return this.locator(options.name); }
    getByPlaceholder(label) { return this.locator(label); }
    getByText(label) { return this.locator(`title:${label}`); }
    last() { return this; }
    async count() { return counts.length ? counts.shift() : 1; }
    async getAttribute() { return theme; }
    async inputValue() { return inputs[this.role]; }
    async fill(text) { inputs[this.role] = text; }
    async waitFor() { events.push(`wait:${this.selector}`); }
    async scrollIntoViewIfNeeded() {}
    async click() {
      events.push(this.selector);
      if (this.selector.includes('切换明暗模式')) theme = theme === 'light' ? 'dark' : 'light';
    }
    async evaluate(fn) {
      if (fn === readHaruSessionRenderer) return { ready: true, conversationId: 7, generation: 2,
        theme: restarted && wrongTheme ? 'light' : theme, draft: inputs[this.role], text: restarted && wrongBody ? 'wrong body' : BODY, bridgeText: BODY };
      restarted = true; return { id: 7, title: '合成历史标题', titleUnique: storedTitleUnique };
    }
    async waitForResponse(predicate) {
      assert.equal(predicate({ url: () => `${ORIGIN}/api/chat/conversations/7`, request: () => ({ method: () => 'GET' }), status: () => 200 }), true);
    }
  }
  const page = new Surface('owner'), haru = new Surface('haru');
  const native = { origin: ORIGIN, async assertIdentity() { events.push('identity'); },
    async prepareTray() { events.push('prepare-tray'); }, async hideOwner() { events.push('hide-owner'); },
    async waitVisible(role) { events.push(`visible:${role}`); }, async waitHidden(role) { events.push(`hidden:${role}`); },
    async hideHaru() { events.push('tray-hide'); },
    async restoreHaru() { events.push('tray-show'); } };
  const capture = async (id, surface, stage) => {
    assert.equal(stage, 'pilot-stream');
    assert.equal(surface.role, id.startsWith('haru-') ? 'haru' : 'owner');
    events.push(`capture:${id}`); return { status: 'captured' };
  };
  const args = { page, haru, native, current: { id: 'pilot-stream' }, capture, deadlineMs: Date.now() + 2000 };
  return { broker, value, events, inputs, args, session: createMockHaruSession({ mode: 'mock', broker }) };
}
test('session matrix retains two drafts, captures both themes, clears only its inputs and restores history by real title click', async () => {
  const value = sessionHarness();
  const first = await value.session.afterPilot(value.args);
  assert.ok(Object.values(first).every(flag => flag === true));
  assert.equal(Object.keys(first).length, 5);
  assert.deepEqual(value.inputs, { owner: '', haru: '' });
  assert.ok(value.events.indexOf('hide-owner') < value.events.indexOf('visible:owner'));
  const close = value.events.indexOf('|关闭 Haru 对话');
  assert.ok(close >= 0);
  assert.deepEqual(value.events.slice(close, close + 7), [
    '|关闭 Haru 对话', 'wait:|.desktop-haru.collapsed', 'visible:haru',
    '|展开 Haru 对话', 'tray-hide', 'hidden:haru', 'tray-show',
  ]);
  const checks = await value.session.afterRestart({ ...value.args,
    restart: { productionTrayExit: true, processAndPortsReleased: true, sameProfileAndExe: true } });
  assert.equal(Object.keys(checks).length, 10);
  assert.ok(Object.values(checks).every(flag => flag === true));
  assert.equal(Object.hasOwn(checks, 'haruDraftsProcessLocalOnly'), false);
  assert.deepEqual(value.events.filter(event => event.startsWith('capture:')),
    ['capture:haru-session-light', 'capture:haru-session-dark', 'capture:pilot-session-restored']);
  assert.ok(value.events.some(event => event.endsWith('title:合成历史标题')));
  assert.equal(value.value.sentRequests, 2);
  assert.equal(value.session.diagnostic().stage, 'COMPLETE');
  assert.ok(HARU_SESSION_DIAGNOSTIC_FLAGS.every(key => value.session.diagnostic()[key] === true));
});
for (const extra of ['sent', 'denied']) {
  test(`session matrix refuses an extra ${extra} request even though the visible history and drafts match`, async () => {
    const value = sessionHarness();
    value.args.native.restoreHaru = async () => {
      if (extra === 'denied') value.value.denied.AUTH++;
      else { value.value.sentRequests++; value.value.mock.fakeRequests++;
        value.value.requests.push({ caseId: 'extra', status: 'SETTLED' }); }
    };
    await assert.rejects(value.session.afterPilot(value.args));
  });
}
test('session matrix fails closed when mandatory safe screenshots are skipped', async () => {
  const value = sessionHarness();
  value.args.capture = async () => ({ status: 'skipped', code: 'SCREEN_VISUAL_UNSETTLED' });
  await assert.rejects(value.session.afterPilot(value.args), { safeCode: 'UNSAFE_SCREENSHOT' });
});

const restartProof = { productionTrayExit: true, processAndPortsReleased: true, sameProfileAndExe: true };
test('MOCK stream has a distinct fixed first sentence while both original LIVE prompts and request constraints remain intact', () => {
  const liveStream = '这是虚构软件验收。不要调用任何工具或读取资料，只分十个小段说明软件测试中的边界值检查，每段约四十字。';
  const liveCancel = '这是虚构软件验收。不要调用任何工具或读取资料，请连续写二十段关于软件测试边界条件的说明，每段约六十字。';
  assert.equal(pilotPrompt('pilot-stream', 'live'), liveStream);
  assert.equal(pilotPrompt('pilot-cancel', 'live'), liveCancel);
  assert.equal(pilotPrompt('pilot-cancel', 'mock'), liveCancel);
  assert.equal(pilotPrompt('pilot-stream', 'mock'), liveStream.replace('这是虚构软件验收。', '这是虚构软件流式验收。'));
  assert.notEqual(pilotPrompt('pilot-stream', 'mock').split('。')[0], pilotPrompt('pilot-cancel', 'mock').split('。')[0]);
});
test('the actual stored-title reader preserves ID lookup and refuses first-sentence collisions', async () => {
  const read = rows => vm.runInNewContext(`(${readStoredHaruConversation.toString()})(7)`, {
    fetch: async url => { assert.equal(url, '/api/chat/conversations'); return { ok: true, json: async () => rows }; },
  });
  const colliding = await read([{ id: 7, title: '这是虚构软件验收。' }, { id: 9, title: '这是虚构软件验收。' }]);
  assert.equal(colliding.id, 7); assert.equal(colliding.titleUnique, false);
  const unique = await read([{ id: 7, title: '这是虚构软件流式验收。' }, { id: 9, title: '这是虚构软件验收。' }]);
  assert.equal(unique.id, 7); assert.equal(unique.titleUnique, true);
  assert.equal(await read([{ id: 8, title: '这是虚构软件流式验收。' }]), null);
});
test('restart refuses stored-title collisions before clicking any conversation and retains native proof', async () => {
  const value = sessionHarness({ storedTitleUnique: false });
  await value.session.afterPilot(value.args);
  await assert.rejects(value.session.afterRestart({ ...value.args, restart: restartProof }), { safeCode: 'HARU_SYNC_FAILED' });
  assert.equal(value.events.some(event => event.includes('title:')), false);
  const diagnostic = value.session.diagnostic();
  assert.equal(diagnostic.stage, 'STORED_CONVERSATION');
  assert.equal(diagnostic.stored, true); assert.equal(diagnostic.storedTitleUnique, false);
  assert.equal(diagnostic.productionTrayExit, true); assert.equal(diagnostic.sameProfileAndExe, true);
});
test('restart waits for asynchronous unique row and title appearance before their real click', async () => {
  const value = sessionHarness({ counts: [0, 1, 0, 1] });
  await value.session.afterPilot(value.args);
  await value.session.afterRestart({ ...value.args, restart: restartProof });
  assert.equal(value.events.filter(event => event.includes('title:')).length, 1);
  assert.equal(value.session.diagnostic().stage, 'COMPLETE');
});
for (const problem of ['duplicate-row', 'wrong-body', 'wrong-theme']) {
  test(`restart preserves fail-closed ${problem} and exposes only bounded stage/boolean diagnosis`, async () => {
    const value = sessionHarness({ counts: problem === 'duplicate-row' ? [2, 2, 2] : [],
      wrongBody: problem === 'wrong-body', wrongTheme: problem === 'wrong-theme' });
    await value.session.afterPilot(value.args);
    await assert.rejects(value.session.afterRestart({ ...value.args, restart: restartProof, deadlineMs: Date.now() + 60 }),
      { safeCode: 'HARU_SYNC_FAILED' });
    const diagnostic = safeHaruSessionDiagnostic(value.session.diagnostic(), true);
    assert.equal(diagnostic.stage, problem === 'duplicate-row' ? 'UNIQUE_ROW' : problem === 'wrong-body' ? 'BODY_PAIR' : 'THEME');
    assert.doesNotMatch(JSON.stringify(diagnostic), /合成|wrong body|title:|conversationId/);
  });
}
test('diagnostic serialization refuses LIVE, raw fields, unknown stages and non-boolean flags', () => {
  const diagnostic = sessionHarness().session.diagnostic();
  assert.throws(() => safeHaruSessionDiagnostic(diagnostic, false));
  assert.throws(() => safeHaruSessionDiagnostic({ ...diagnostic, title: 'private' }, true));
  assert.throws(() => safeHaruSessionDiagnostic({ ...diagnostic, stage: 'private-stage' }, true));
  assert.throws(() => safeHaruSessionDiagnostic({ ...diagnostic, bodyEqual: BODY }, true));
});
