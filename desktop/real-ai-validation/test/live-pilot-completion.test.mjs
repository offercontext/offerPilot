import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { readPinnedSource } from './pinned-source.mjs';
import { Ledger } from '../broker-core.cjs';
import { RECOVERY, verifyPriorZip, createMockCarryForward, carryForwardEvidence } from '../carry-forward-policy.cjs';
import { PIN, CASES } from '../contract.mjs';
import { safeResults, saveEvidence } from '../safe-evidence.mjs';
import { installMirrorObservation, removeMirrorObservation } from '../mirror-observer.mjs';
import { compactPlainMessage, normalizePlainText, projectAdmission, projectUiRequest, projectRequestExecution, projectTerminal, readLivePilotJson,
  observeLiveAdmission, isLiveLedgerProven, liveLedgerFingerprint, offlineSnapshot, proveLivePilotCompletion, readLiveTerminalRenderer, observePilotCompletion } from '../live-pilot-completion.mjs';
// Offline synthetic mechanism tests. No EXE, real provider or release claim.
const SECRET = 'PRIVATE_SYNTHETIC_TERMINAL_CONTENT';
const recoveryCarry = verifyPriorZip(await fs.readFile(new URL('./fixtures/prior-paid-38694575612.zip', import.meta.url)));
const flush = async () => { for (let n = 0; n < 30; n += 1) await Promise.resolve(); };
const state = (taskState = 'idle', conversationId = null) => ({ connected: true, generation: 0,
  snapshot: { version: 1, theme: 'light', animationLevel: 'full', contextLabel: 'synthetic', aiConfigured: true,
    taskState, conversationId, loading: taskState === 'running', hasPending: false,
    stopping: false, canStop: taskState === 'running', canSend: taskState === 'idle', error: '', stopMessage: '',
    messages: conversationId ? [{ role: 'assistant', content: SECRET }] : [] } });
function renderer(role) {
  let current = state(), nextTimer = 0, hook;
  const listeners = new Set(), mutations = new Set(), timers = new Set(), calls = [];
  const makeNode = text => ({ isConnected: true, hidden: false, parentElement: null,
    nodeType: 1, tagName: 'DIV', childNodes: [], textContent: text, get innerText() { return this.textContent; }, set innerText(value) { this.textContent = value; }, style: { display: 'block', visibility: 'visible', opacity: '1' },
    getClientRects: () => [{ width: 100, height: 40 }], getAttribute: () => null, disabled: false });
  const root = makeNode(''), body = makeNode(SECRET), running = makeNode('正在处理');
  body.parentElement = root;
  const proseText = { nodeType: 3, childNodes: [], get textContent() { return body.textContent; } };
  const paragraph = makeNode(''); paragraph.tagName = 'P'; paragraph.childNodes = [proseText];
  const markdown = makeNode(''); markdown.className = '_markdown_pinned'; markdown.childNodes = [paragraph];
  paragraph.parentElement = markdown; markdown.parentElement = body;
  body.childNodes = role === 'owner' ? [markdown] : [proseText];
  if (role === 'haru') body.tagName = 'P';
  let structured = false, secondBody, duplicateRoot = false, currentBody = body;
  root.querySelectorAll = selector => {
    if (selector.startsWith('article[aria-label^="本轮任务："]')) return structured ? [makeNode('')] : [];
    const expected = role === 'owner' ? '[class*="bubbleAssistant"]' : '.desktop-haru-messages article[data-role="assistant"] p';
    return selector === expected ? [currentBody, ...(secondBody ? [secondBody] : [])] : [];
  };
  const schedule = () => { const id = ++nextTimer; timers.add(id); return id; };
  class RendererDate extends Date { static now() { return Date.now(); } }
  const context = vm.createContext({ Date: RendererDate, TextEncoder, TextDecoder, AbortController, URL, location: { origin: 'http://127.0.0.1:41000', href: 'http://127.0.0.1:41000/' },
    setTimeout: schedule, clearTimeout: id => timers.delete(id), setInterval: schedule, clearInterval: id => timers.delete(id),
    getComputedStyle: node => node.style,
    MutationObserver: class { constructor(fn) { this.fn = fn; } observe() { mutations.add(this.fn); } disconnect() { mutations.delete(this.fn); } },
    document: { visibilityState: 'visible', documentElement: {}, querySelectorAll(selector) {
      const rootSelector = role === 'owner' ? '[data-onboarding-target="pilot"]' : 'main[aria-label="Haru 桌面小窗"]';
      if (selector === rootSelector) return duplicateRoot ? [root, root] : [root];
      const runningSelector = role === 'owner'
        ? '[data-onboarding-target="pilot"] button[aria-label="停止当前回复"]'
        : 'main[aria-label="Haru 桌面小窗"] .desktop-haru-avatar [role="status"]';
      return selector === runningSelector && current.snapshot.taskState === 'running' ? [running] : [];
    } },
    window: { offerpilotDesktop: { role, getState: async () => structuredClone(current),
      onState(fn) { listeners.add(fn); return () => listeners.delete(fn); } } },
  });
  const responses = new Set(), frame = {};
  const page = { url: () => 'http://127.0.0.1:41000/', mainFrame: () => frame,
    on: (event, fn) => responses.add(fn), off: (event, fn) => responses.delete(fn),
    async evaluate(fn, arg) {
    calls.push(arg?.operation ?? 'terminal');
    await hook?.(arg);
    context.__argument = structuredClone(arg);
    return vm.runInContext(`(${fn.toString()})(__argument)`, context);
  } };
  return { page, context, root, body, paragraph, markdown, calls, responses, frame,
    replaceBody() { currentBody = { ...body }; body.isConnected = false; },
    notifyMutation(records) { for (const callback of mutations) callback(records); },
    duplicateRoot() { duplicateRoot = true; },
    onEvaluate(fn) { hook = fn; },
    alterState(fn) { fn(current); },
    setStructured(value) { structured = value; },
    hiddenLast() { secondBody = makeNode(SECRET); secondBody.style.visibility = 'hidden'; },
    async publish(value) {
      current = structuredClone(value);
      if (role === 'haru') for (const callback of listeners) callback(structuredClone(current));
      for (const callback of mutations) callback();
      await flush();
    },
  };
}
const IDS = ['request_id', 'turn_id', 'conversation_id', 'execution_generation'];
const ID = Object.freeze({ request_id: '26e756a3-f74c-4e63-8ae0-3d42ff3976f9', turn_id: 'turn-synthetic-73', conversation_id: 73, execution_generation: 1 });
const admission = () => ({ ...ID, protocol_version: 'pilot-runtime-v1', state: 'running', replayed: false,
  execution: { ...ID, protocol_version: 'pilot-runtime-v1', state: 'running' } });
const terminal = () => ({ ...ID, protocol_version: 'pilot-runtime-v1', state: 'completed', worker_done: true,
  actual_worker_alive: false, recovery: { requires_resync: false, auto_resume: false },
  execution: { ...ID, protocol_version: 'pilot-runtime-v1', state: 'completed', worker_done: true, actual_worker_alive: false },
  terminal: { response: { turn_id: ID.turn_id, conversation_id: ID.conversation_id,
    execution_generation: ID.execution_generation, type: 'message', message: SECRET, write_status: 'none' } } });
function ledger(stream = true) {
  const book = new Ledger();
  for (const caseId of ['connection', ...(stream ? ['pilot-stream'] : [])]) {
    book.armCase(caseId); const ticket = book.claim(); book.reserve(ticket, ticket.maxTokens, 'UNCHANGED');
    book.markTransport(ticket, 'outboundStarted'); book.markTransport(ticket, 'upstreamResponded');
    book.finish(ticket, 'SETTLED', { prompt_tokens: 20, completion_tokens: 3, prompt_cache_hit_tokens: 0,
      prompt_cache_miss_tokens: 20, total_tokens: 23 });
  }
  return { ...book.snapshot(), journalFailed: false };
}
function recoveryLedger(stream = true, mode = 'live') {
  const book = new Ledger(undefined, { carryForward: mode === 'live' ? recoveryCarry : createMockCarryForward(), mode });
  if (stream) {
    book.armCase('pilot-stream'); const ticket = book.claim(); book.reserve(ticket, ticket.maxTokens, 'UNCHANGED');
    book.markTransport(ticket, 'outboundStarted'); book.markTransport(ticket, 'upstreamResponded');
    book.finish(ticket, 'SETTLED', { prompt_tokens: 20, completion_tokens: 3, prompt_cache_hit_tokens: 0,
      prompt_cache_miss_tokens: 20, total_tokens: 23 });
  }
  return { ...book.snapshot(), journalFailed: false,
    ...(mode === 'mock' ? { mode: 'MOCK', mock: { transport: 'IN_PROCESS_SYNTHETIC_HTTPS', externalNetwork: 'DENIED',
      realProviderCalls: 0, usageIsSynthetic: true, fakeRequests: stream ? 1 : 0, clockScope: 'BROKER_VM_ONLY' } } : {}) };
}
async function harness(t, { running = 'none', growth = 0, content = SECRET, deadline = 3000, initial, recovery = false } = {}) {
  const owner = renderer('owner'), haru = renderer('haru');
  const makeLedger = recovery ? recoveryLedger : ledger;
  const prior = makeLedger(false), final = makeLedger(); let snapshot = prior, sends = 0, status = terminal(), history = [];
  status.terminal.response.message = content;
  const transport = { historyOptions: {}, statusOptions: {}, admissionOptions: {}, afterSend: null, terminalHook: null };
  function reply(value, options = {}) {
    const bytes = new TextEncoder().encode(typeof value === 'string' ? value : JSON.stringify(value)); let consumed = false;
    return { status: options.status ?? 200, redirected: options.redirected ?? false,
      url: options.url, body: { getReader() { return { async read() { if (options.never) return new Promise(() => {});
        if (consumed) return { done: true }; consumed = true; return { value: bytes, done: false }; }, async cancel() {} }; } } };
  }
  let currentAdmissionResponse;
  owner.context.fetch = async (route, options) => {
    if (options.method === 'POST') {
      if (transport.admissionOptions.abort) return Promise.reject(new Error('synthetic-abort'));
      if (transport.admissionOptions.hanging) return new Promise(() => {});
      return { body: () => assert.fail('POST body must never be read') };
    }
    assert.equal(options.method, 'GET'); assert.equal(options.credentials, 'same-origin'); assert.equal(options.redirect, 'error');
    assert.ok(route === '/api/chat/conversations?include_archived=true' || route === `/api/pilot/runtime/v1/requests/${ID.request_id}`);
    const config = route.includes('conversations') ? transport.historyOptions : transport.statusOptions;
    return reply(route.includes('conversations') ? history : status, { url: `http://127.0.0.1:41000${route}`, ...config });
  };
  owner.context.window.fetch = owner.context.fetch;
  owner.context.window.__offerpilotBoundedUiObserver = { summary: () => ({ installed: true, originalTargetConnected: true,
    originalTargetCurrent: true, currentPilotUnique: true, targetReplacementObserved: false, readFailed: false,
    originalGrowthWithStopCount: growth }) };
  t.after(() => removeMirrorObservation(owner.page, haru.page));
  if (initial) await Promise.all([owner.publish(initial), haru.publish(initial)]);
  await installMirrorObservation(owner.page, haru.page, 'pilot-stream');
  const emit = (value = admission(), options = {}) => {
    const body = options.requestBody ?? JSON.stringify({ message: 'synthetic', request_id: value.request_id, conversation_id: 0 });
    const request = { method: () => options.method ?? 'POST', frame: () => options.frame ?? owner.frame,
      redirectedFrom: () => options.redirectedFrom ?? null,
      headers: () => assert.fail('headers forbidden'), postData: () => body,
      url: () => options.url ?? 'http://127.0.0.1:41000/api/pilot/runtime/v1/turns' };
    if ((options.method ?? 'POST') === 'POST') void owner.context.window.fetch(request.url(), { method: 'POST', body }).catch(() => {});
    for (const listener of [...owner.responses]) listener(request);
  };
  const brokerStarted = performance.now();
  const broker = { ...(recovery ? { remainingMs: () => Math.max(0, 120000 - (performance.now() - brokerStarted)) } : {}),
    snapshot: () => structuredClone(snapshot), cancelCase: () => assert.fail('proof cannot cancel'),
    armCase: () => assert.fail('proof cannot arm'), prepareCase: () => assert.fail('proof cannot prepare') };
  const args = { mode: 'live', page: owner.page, haru: haru.page, broker, requestCountBefore: 1,
    deadlineMs: Date.now() + deadline, ctx: { mark() {} }, send: async () => {
      sends += 1; assert.equal(owner.responses.size, 1); emit(admission(), transport.admissionOptions);
      if (running !== 'none') {
        if (running !== 'haru') await owner.publish(state('running', 73));
        if (running !== 'owner') await haru.publish(state('running', 73));
      }
      const finalState = state('idle', 73); finalState.snapshot.messages[0].content = compactPlainMessage(content);
      owner.body.innerText = content; haru.body.innerText = compactPlainMessage(content);
      await Promise.all([owner.publish(finalState), haru.publish(finalState)]);
      snapshot = final; await transport.afterSend?.();
    } };
  owner.onEvaluate(async arg => { if (arg?.role === 'owner' && !arg.operation) await transport.terminalHook?.(); });
  return { owner, haru, args, prior, final, transport, emit, status, setHistory(value) { history = value; },
    setStatus(value) { status = value; }, sends: () => sends, prove: () => proveLivePilotCompletion(args) };
}
const blocked = async fake => {
  await assert.rejects(fake.prove(), { code: 'LIVE_COMPLETION_UNPROVEN' });
  assert.equal(fake.owner.responses.size, 0, 'listener detached on every unsuccessful path');
};

for (const [running, growth, code] of [['none', 0, 'LIVE_COMPLETION_ONLY'], ['owner', 2, 'LIVE_COMPLETION_ONLY'],
  ['haru', 2, 'LIVE_COMPLETION_ONLY'], ['both', 1, 'LIVE_COMPLETION_ONLY'], ['both', 2, 'LIVE_STREAMING_PROVEN']]) {
  test(`${running} running, ${growth} deltas: honest ${code}`, async t => {
    const fake = await harness(t, { running, growth }); const proof = await fake.prove();
    assert.equal(proof.code, code); assert.equal(proof.checks.sustainedStreamingObserved, code === 'LIVE_STREAMING_PROVEN');
    assert.equal(proof.checks.ownerRunningTransitionObserved, ['both', 'owner'].includes(running));
    assert.equal(proof.checks.haruRunningTransitionObserved, ['both', 'haru'].includes(running));
    assert.equal(fake.sends(), 1); assert.equal(fake.owner.responses.size, 0);
    assert.doesNotMatch(JSON.stringify(proof), /PRIVATE_|26e756|turn-synthetic|conversation_id|digest|generation/);
  });
}
for (const count of [519, 520, 521]) test(`${count} emoji respects exact Unicode compact boundary`, async t => {
  const text = '😀'.repeat(count); assert.equal(compactPlainMessage(` \n${text}\t `), '😀'.repeat(Math.min(count, 520)) + (count > 520 ? '…' : ''));
  const fake = await harness(t, { content: text }); assert.equal((await fake.prove()).code, 'LIVE_COMPLETION_ONLY');
});
test('ASCII whitespace normalization does not remove Markdown, punctuation, or Unicode whitespace', () => {
  assert.equal(normalizePlainText(' \na\t\rb '), 'a b');
  assert.notEqual(normalizePlainText('a\u00a0b'), 'a b');
  assert.notEqual(normalizePlainText('**hello**'), 'hello');
  assert.notEqual(normalizePlainText('hello。'), 'hello');
});
for (const [name, change] of [
  ['old owner content', f => { f.owner.body.innerText = 'old content'; }],
  ['compact not full owner', f => { f.owner.body.innerText = compactPlainMessage(SECRET + 'x'.repeat(600)); }],
  ['hidden latest', f => f.haru.hiddenLast()], ['hidden root', f => { f.owner.root.style.opacity = '0'; }],
  ['duplicate root', f => f.owner.duplicateRoot()], ['structured card', f => f.owner.setStructured(true)],
  ['bridge mismatch', f => f.haru.alterState(s => { s.snapshot.messages[0].content = 'old'; })],
  ['stopping', f => f.owner.alterState(s => { s.snapshot.stopping = true; })],
  ['canSend false', f => f.haru.alterState(s => { s.snapshot.canSend = false; })],
  ['error', f => f.haru.alterState(s => { s.snapshot.error = SECRET; })],
]) test(`${name} cannot prove completion`, async t => {
  const fake = await harness(t, { deadline: 140 }); fake.transport.afterSend = () => change(fake); await blocked(fake);
});
test('rendered Markdown is unproven rather than regex-repaired', async t => {
  const fake = await harness(t, { content: '**hello**', deadline: 140 }); fake.transport.afterSend = () => { fake.owner.body.innerText = 'hello'; }; await blocked(fake);
});
for (const history of [{ items: [], next: null }, { total: 0, conversations: [] }, [{ id: 999 }], null, '[]garbage', 'x'.repeat(8193)]) {
  test('only complete empty array history is fresh', async t => {
    const fake = await harness(t); fake.setHistory(history); await blocked(fake); assert.equal(fake.sends(), 0);
  });
}
for (const options of [{ status: 500 }, { redirected: true }, { url: 'http://127.0.0.1:41000/other' }]) test('history transport fails closed', async t => {
  const fake = await harness(t); fake.transport.historyOptions = options; await blocked(fake); assert.equal(fake.sends(), 0);
});
for (const [name, mutate] of [
  ['UUID not v4', v => { v.request_id = '26e756a3-f74c-1e63-8ae0-3d42ff3976f9'; }],
  ['turn path injection', v => { v.turn_id = '../other'; }], ['nonpositive ID', v => { v.conversation_id = 0; }],
  ['replayed', v => { v.replayed = true; }], ['protocol', v => { v.protocol_version = 'wrong'; }],
  ['state conflict', v => { v.execution.state = 'completed'; }],
  ...IDS.map(key => [`nested ${key} conflict`, v => { v.execution[key] = 'wrong'; }]),
]) test(`admission ${name} rejected`, () => {
  const value = admission(); mutate(value); assert.throws(() => projectAdmission(value, 202), { code: 'LIVE_COMPLETION_UNPROVEN' });
});
test('admission non-202 or oversize fails', async t => {
  assert.throws(() => projectAdmission(admission(), 200));
  const fake = await harness(t); fake.transport.admissionOptions = { requestBody: 'x'.repeat(65537) }; await blocked(fake);
});
test('exactly one admission, even duplicate identical identity, is required', async t => {
  const fake = await harness(t); fake.transport.afterSend = () => fake.emit(); await blocked(fake);
});
for (const options of [{ frame: {} }, { url: 'https://evil.example/api/pilot/runtime/v1/turns' },
  { url: 'http://127.0.0.1:41000/api/pilot/runtime/v1/turns?query=1' }, { method: 'GET' }]) {
  test('wrong frame, origin, URL or verb cannot be admission', async t => {
    const fake = await harness(t, { deadline: 120 }); fake.transport.admissionOptions = options; await blocked(fake);
  });
}
for (const location of ['top', 'execution', 'response']) for (const key of IDS) {
  test(`terminal ${location} ${key} conflict cannot pass`, () => {
    const value = terminal(), target = location === 'top' ? value : location === 'execution' ? value.execution : value.terminal.response;
    target[key] = 'wrong'; assert.throws(() => projectTerminal(value, ID), { code: 'LIVE_COMPLETION_UNPROVEN' });
  });
}
for (const [name, mutate] of [
  ['unknown', v => { v.state = 'result_unknown'; }], ['pending', v => { v.state = 'waiting_confirmation'; }],
  ['failed', v => { v.state = 'failed'; }], ['stopped', v => { v.state = 'stopped'; }],
  ['interrupted', v => { v.state = 'interrupted'; }], ['no worker_done', v => { delete v.worker_done; }],
  ['alive', v => { v.actual_worker_alive = true; }], ['execution alive', v => { v.execution.actual_worker_alive = true; }],
  ['missing response', v => { v.terminal = {}; }], ['empty body', v => { v.terminal.response.message = ' '; }],
  ['oversize body', v => { v.terminal.response.message = 'x'.repeat(12001); }],
  ['degraded', v => { v.terminal.response.degraded = true; }], ['replayed', v => { v.terminal.response.replayed = true; }],
  ['recovered manager', v => { v.recovery.requires_resync = true; }], ['auto resume', v => { v.recovery.auto_resume = true; }],
  ['turn_recovered', v => { v.terminal.response.type = 'turn_recovered'; }],
  ['confirmation required', v => { v.terminal.response.type = 'confirmation_required'; }],
  ...['operation_id', 'undo', 'write_status', 'write_error', 'pending_action'].map(key => [key, v => { v.terminal.response[key] = null; }]),
]) test(`terminal ${name} is unproven`, () => {
  const value = terminal(); mutate(value); assert.throws(() => projectTerminal(value, ID), { code: 'LIVE_COMPLETION_UNPROVEN' });
});
test('completed without body cannot substitute for trusted status body', async t => {
  const fake = await harness(t); fake.status.terminal = {}; await blocked(fake);
});
for (const [name, change] of [
  ['active', v => { v.active = true; }], ['closed', v => { v.closed = true; }],
  ['journal failed', v => { v.journalFailed = true; }], ['denied', v => { v.denied.AUTH = 1; }],
  ['extra row', v => { v.requests.push(v.requests[1]); v.sentRequests++; }],
  ['request count', v => { v.sentRequests++; }], ['count cap', v => { v.sentRequests = 9; }],
  ['outbound absent', v => { v.requests[1].outboundStarted = false; }],
  ['upstream absent', v => { v.requests[1].upstreamResponded = false; }],
  ['client disconnect', v => { v.requests[1].clientDisconnectObserved = true; }],
  ['unsettled', v => { v.requests[1].status = 'RESERVED'; }],
  ['usage missing', v => { delete v.requests[1].promptTokens; }],
  ['usage conflict', v => { v.requests[1].cacheMissTokens++; }],
  ['cost mismatch', v => { v.requests[1].micro++; }],
  ['sum mismatch', v => { v.settledMicroCny++; }],
  ['budget', v => { v.budgetMicroCny++; }], ['reserve', v => { v.reserveMicroCny++; }],
  ['retained', v => { v.retainedMicroCny++; }], ['prior row altered', v => { v.requests[0].cap++; }],
  ['unknown row field', v => { v.requests[1].rawContent = SECRET; }],
  ['mock', v => { v.mode = 'MOCK'; }],
]) test(`ledger ${name} cannot certify completion`, () => {
  const value = ledger(); change(value); assert.equal(isLiveLedgerProven(value, ledger(false), 1), false);
});
test('prior denial cannot be adopted as clean baseline', () => {
  const before = ledger(false), after = ledger(); before.denied.AUTH = after.denied.AUTH = 1;
  assert.equal(isLiveLedgerProven(after, before, 1), false);
});
test('strict carry-forward proof binds a historical connection and exactly one current Pilot', async t => {
  const fake = await harness(t, { recovery: true, running: 'both', growth: 2 });
  assert.equal(isLiveLedgerProven(fake.final, fake.prior, 1), true);
  assert.equal((await fake.prove()).code, 'LIVE_STREAMING_PROVEN');
  assert.equal(fake.sends(), 1);
  assert.equal(fake.prior.sentRequests, 1); assert.equal(fake.prior.currentRequests, 0);
  assert.equal(fake.prior.settledMicroCny, 188); assert.equal(fake.final.settledMicroCny, 252);
});
for (const [name, mutate] of [
  ['source product', v => { v.carryForward.source.productCommit = PIN.commit; }],
  ['source run', v => { v.carryForward.source.runId++; }],
  ['source price', v => { v.carryForward.source.priceCheckedAt = '2026-10-16'; }],
  ['scope', v => { v.carryForward.scope = 'full-suite'; }],
  ['prefix count', v => { v.carryForward.priorRequestCount = 0; }],
  ['prior spend', v => { v.carryForward.priorSettledMicroCny = 189; }],
  ['delta count', v => { v.currentRequests = 0; }],
  ['prefix cost', v => { v.requests[0].micro = 0; }],
  ['source erased', v => { delete v.carryForward; }],
]) test(`carry ${name} changing during renderer reads invalidates strict proof`, async t => {
  const fake = await harness(t, { recovery: true });
  // Snapshot mutation is synthetic test input, never a broker mutation API.
  fake.final.carryForward = structuredClone(fake.final.carryForward);
  const priorFingerprint = liveLedgerFingerprint(fake.final);
  fake.transport.terminalHook = () => mutate(fake.final);
  await blocked(fake);
  assert.notEqual(liveLedgerFingerprint(fake.final), priorFingerprint);
  assert.equal(isLiveLedgerProven(fake.final, fake.prior, 1), false);
});
for (const [name, mutate] of [
  ['corrupt source', v => { v.carryForward.source.productCommit = PIN.commit; }],
  ['unknown carry mode', v => { v.carryForward.mode = 'unknown'; }],
  ['MOCK source', v => { v.carryForward = structuredClone(carryForwardEvidence(createMockCarryForward())); }],
  ['zero historical cost', v => { v.settledMicroCny = 0; }],
  ['historical retained cost', v => { v.retainedMicroCny = 3000000; }],
  ['already used current request', v => { v.currentRequests = 1; }],
]) test(`carry baseline ${name} blocks before UI sends`, async t => {
  const fake = await harness(t, { recovery: true }); fake.prior.carryForward = structuredClone(fake.prior.carryForward);
  mutate(fake.prior); await blocked(fake); assert.equal(fake.sends(), 0);
});
test('explicit synthetic recovery counts only new fake HTTP calls and remains excluded from LIVE proof', async t => {
  const before = recoveryLedger(false, 'mock'), after = recoveryLedger(true, 'mock');
  assert.equal(offlineSnapshot(before), true); assert.equal(offlineSnapshot(after), true);
  assert.equal(before.mock.fakeRequests, 0); assert.equal(after.mock.fakeRequests, 1);
  assert.equal(isLiveLedgerProven(after, before, 1), false);
  const wrong = structuredClone(after); wrong.mock.fakeRequests = wrong.sentRequests;
  assert.equal(offlineSnapshot(wrong), false);
  const fake = await harness(t, { recovery: true, running: 'both', growth: 2 });
  Object.assign(fake.prior, before); Object.assign(fake.final, after);
  fake.args.mode = 'mock'; fake.args.broker.mode = 'MOCK';
  assert.equal((await observePilotCompletion(fake.args)).code, 'STREAMING_PROVEN');
  assert.equal(fake.sends(), 1);
});
const deadlineBlocked = async fake => {
  await assert.rejects(fake.prove(), error => {
    assert.equal(error.code, 'LIVE_COMPLETION_UNPROVEN');
    assert.equal(error.pilotProofDiagnostic.fault, 'OBSERVE_DEADLINE');
    return true;
  });
  assert.equal(fake.owner.responses.size, 0);
};
for (const invalid of [undefined, null, NaN, Infinity, -1, 120001, '75000', 0]) {
  test(`recovery proof rejects invalid or expired broker time ${String(invalid)} before sending`, async t => {
    const fake = await harness(t, { recovery: true });
    if (invalid === undefined) delete fake.args.broker.remainingMs;
    else fake.args.broker.remainingMs = () => invalid;
    await deadlineBlocked(fake); assert.equal(fake.sends(), 0);
  });
}
for (const [limit, deadline] of [[75000, 120000], [3000, 3000]]) {
  test(`wall-clock rollback cannot extend recovery observation past its ${limit}ms monotonic limit`, async t => {
    const fake = await harness(t, { recovery: true, running: 'both', growth: 2, deadline });
    let elapsed = 0;
    t.mock.method(performance, 'now', () => elapsed);
    fake.args.broker.remainingMs = () => 120000;
    const wall = Date.now();
    fake.transport.terminalHook = () => { elapsed = limit + 1; t.mock.method(Date, 'now', () => wall - 86400000); };
    await deadlineBlocked(fake); assert.equal(fake.sends(), 1);
  });
}
test('wall-clock rollback cannot permit PASS after the real broker time reaches zero', async t => {
  const fake = await harness(t, { recovery: true, running: 'both', growth: 2, deadline: 75000 });
  let remaining = 75000; fake.args.broker.remainingMs = () => remaining;
  const wall = Date.now();
  fake.transport.terminalHook = () => { remaining = 0; t.mock.method(Date, 'now', () => wall - 86400000); };
  await deadlineBlocked(fake); assert.equal(fake.sends(), 1);
});
test('recovery broker time cannot increase or be reset while observing', async t => {
  const fake = await harness(t, { recovery: true, running: 'both', growth: 2 });
  let remaining = 74000; fake.args.broker.remainingMs = () => remaining;
  fake.transport.terminalHook = () => { remaining = 75000; };
  await deadlineBlocked(fake); assert.equal(fake.sends(), 1);
});
test('recovery completion cannot return PASS when time expires during admission cleanup', async t => {
  const fake = await harness(t, { recovery: true, running: 'both', growth: 2 });
  let remaining = 75000; fake.args.broker.remainingMs = () => remaining;
  const wall = Date.now();
  fake.owner.onEvaluate(arg => {
    if (arg?.operation === 'dispose') { remaining = 0; t.mock.method(Date, 'now', () => wall - 86400000); }
  });
  await deadlineBlocked(fake); assert.equal(fake.sends(), 1);
});
for (const change of [f => { f.args.mode = 'mock'; }, f => { f.args.broker.mode = 'MOCK'; },
  f => { f.prior.mock = {}; }, f => { f.args.requestCountBefore = 0; }]) test('mode or baseline misuse fails before sending', async t => {
  const fake = await harness(t); change(fake); await blocked(fake); assert.equal(fake.sends(), 0);
});
for (const name of ['generation drift', 'A-B-A identity', 'historic pending', 'historic error']) test(`${name} invalidates final idle`, async t => {
  const fake = await harness(t); fake.transport.afterSend = async () => {
    const transient = state('idle', name === 'A-B-A identity' ? 74 : 73);
    if (name === 'generation drift') transient.generation = 1;
    if (name === 'historic pending') { transient.snapshot.taskState = 'waiting_confirmation'; transient.snapshot.hasPending = true; }
    if (name === 'historic error') transient.snapshot.error = SECRET;
    await fake.haru.publish(transient); await fake.haru.publish(state('idle', 73));
  }; await blocked(fake);
});
test('ledger changed while reading DOM blocks proof', async t => {
  const fake = await harness(t); fake.transport.terminalHook = () => { fake.final.denied.AUTH++; }; await blocked(fake);
});
test('listener disposal failure revokes a would-be pass', async t => {
  const fake = await harness(t), off = fake.owner.page.off;
  fake.owner.page.off = (...args) => { off(...args); throw new Error(SECRET); }; await blocked(fake);
});
for (const mode of ['hanging', 'abort']) test(`original POST ${mode} still proves exact natural GET completion without a second send`, async t => {
  const fake = await harness(t); fake.transport.admissionOptions[mode] = true;
  assert.equal((await fake.prove()).code, 'LIVE_COMPLETION_ONLY'); assert.equal(fake.sends(), 1);
});
test('late terminal readback after timeout cannot return success', async t => {
  const fake = await harness(t, { deadline: 100 }); let release;
  const evaluate = fake.owner.page.evaluate;
  fake.owner.page.evaluate = async (fn, arg) => {
    if (fn.name === 'readLivePilotJson' && arg.requestId) await new Promise(resolve => { release = resolve; });
    return evaluate(fn, arg);
  };
  await blocked(fake); release?.(); await flush(); assert.equal(fake.owner.responses.size, 0);
});
test('running status remains bounded until natural terminal; no second send', async t => {
  const fake = await harness(t, { deadline: 120 }); const pending = terminal();
  pending.state = pending.execution.state = 'running'; pending.terminal = {}; fake.setStatus(pending);
  await blocked(fake); assert.equal(fake.sends(), 1);
});
test('artifact cannot relabel MOCK or omit a LIVE proof check', async t => {
  const fake = await harness(t), proof = await fake.prove();
  const rows = CASES.map(id => ({ id, status: 'BLOCKED', code: 'NOT_STARTED', checks: {} }));
  rows[1] = { id: 'pilot-stream', status: 'PASS', ...proof, checks: { ...proof.checks, oneProviderRequestVerified: true } };
  assert.equal(safeResults(rows)[1].code, 'LIVE_COMPLETION_ONLY');
  assert.throws(() => safeResults(rows, true));
  for (const key of ['sustainedStreamingObserved', 'freshConversationProven', 'uiAdmissionProven', 'naturalCompletionProven',
    'terminalIdentityProven', 'terminalMirrorProven', 'terminalTextProven', 'ledgerUnchanged', 'admissionObserverCleanupPassed']) {
    const altered = structuredClone(rows); delete altered[1].checks[key]; assert.throws(() => safeResults(altered));
  }
  const forged = structuredClone(rows); forged[1].code = 'PASSED'; delete forged[1].checks.sustainedStreamingObserved;
  assert.throws(() => safeResults(forged));
  const extra = structuredClone(rows); extra[1].checks.content = SECRET; assert.throws(() => safeResults(extra));
  assert.doesNotMatch(JSON.stringify(safeResults(rows)), /PRIVATE_|26e756|turn-synthetic|digest/);
});
test('PIN locks complete unpaginated history, terminal identity envelope and compact algorithm', async () => {
  const source = readPinnedSource;
  const api = source('src/offerpilot/api.py');
  const history = api.slice(api.indexOf('    @app.get("/api/chat/conversations")'), api.indexOf('    @app.get("/api/chat/conversations/{conversation_id}")'));
  assert.match(history, /def list_conversations\(include_archived: bool = False\) -> list\[dict\[str, Any\]\]:/);
  assert.match(history, /return \[/); assert.match(history, /for item in chat.list_conversations\(include_archived=include_archived\)/);
  assert.doesNotMatch(history, /limit|offset|cursor|page|next/);
  const terminalFn = api.slice(api.indexOf('    def _runtime_terminal_payload('), api.indexOf('    def _runtime_execution_readable('));
  for (const field of ['conversation_id', 'turn_id', 'execution_generation']) assert.ok(terminalFn.includes(`value.setdefault("${field}"`));
  assert.doesNotMatch(terminalFn, /request_id/);
  assert.equal(projectTerminal(terminal(), ID), SECRET);
  const presentation = source('web/src/features/assistantSurface/assistantPresentation.ts');
  assert.match(presentation, /const HARU_TEXT_LIMIT = 520;/);
  const exactBranch = "  const text = turn.content.trim();\n  const characters = Array.from(text);\n  return characters.length > HARU_TEXT_LIMIT\n    ? `${characters.slice(0, HARU_TEXT_LIMIT).join('')}…`\n    : text;";
  assert.ok(presentation.includes(exactBranch));
  const helper = await fs.readFile(new URL('../live-pilot-completion.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(helper, /\.headers\(|response\.body\(/);
});
for (const [name, mutate] of [
  ['old messages', s => { s.snapshot.messages = [{ role: 'assistant', content: SECRET }]; }],
  ['no messages field', s => { delete s.snapshot.messages; }],
  ['no stopping field', s => { delete s.snapshot.stopping; }],
  ['no error field', s => { delete s.snapshot.error; }],
  ['canStop true', s => { s.snapshot.canStop = true; }],
  ['canSend false', s => { s.snapshot.canSend = false; }],
  ['old stopMessage', s => { s.snapshot.stopMessage = SECRET; }],
]) test(`fresh baseline ${name} cannot be adopted`, async t => {
  const initial = state(); mutate(initial); const fake = await harness(t, { initial }); await blocked(fake); assert.equal(fake.sends(), 0);
});
test('second identical admission detaches immediately, even while first body remains pending', () => {
  const listeners = new Set(), frame = {}, origin = 'http://127.0.0.1:41000';
  const page = { mainFrame: () => frame, on: (_, fn) => listeners.add(fn), off: (_, fn) => listeners.delete(fn) };
  const gate = observeLiveAdmission(page, origin, () => new Promise(() => {}));
  const response = { url: () => `${origin}/api/pilot/runtime/v1/turns`, method: () => 'POST', frame: () => frame, redirectedFrom: () => null, postData: () => JSON.stringify({request_id: ID.request_id, conversation_id: 0}) };
  const listener = [...listeners][0]; listener(response); listener(response);
  assert.equal(listeners.size, 0); assert.throws(() => gate.read()); gate.dispose();
});
test('queued and completed-but-worker-alive are observation-only until natural worker exit', () => {
  const queued = terminal(); queued.state = queued.execution.state = 'queued'; queued.terminal = {};
  assert.equal(projectTerminal(queued, ID), null);
  const finishing = terminal(); finishing.worker_done = finishing.execution.worker_done = false;
  finishing.actual_worker_alive = finishing.execution.actual_worker_alive = true;
  assert.equal(projectTerminal(finishing, ID), null);
  assert.equal(projectTerminal(terminal(), ID), SECRET);
});
test('one pending provider row naturally settles without cleanup-generated completion', async t => {
  const fake = await harness(t); const saved = structuredClone(fake.final);
  fake.final.active = true; fake.final.requests[1].status = 'RESERVED';
  const timer = setTimeout(() => Object.assign(fake.final, saved), 35); t.after(() => clearTimeout(timer));
  assert.equal((await fake.prove()).code, 'LIVE_COMPLETION_ONLY'); assert.equal(fake.sends(), 1);
});
test('admission errors and status oversize/redirect/error never leak raw data', async t => {
  for (const options of [{ status: 503 }, { redirected: true }, { url: 'http://127.0.0.1:41000/other' }]) {
    const fake = await harness(t, { deadline: 90 }); fake.transport.statusOptions = options; await blocked(fake);
  }
  const fake = await harness(t, { deadline: 90 }); fake.setStatus('x'.repeat(131073)); await blocked(fake);
});
function fullLedger() {
  const book = new Ledger();
  for (const caseId of ['connection', 'pilot-stream', 'pilot-hitl-reject', 'interview-preparation', 'resume-structure', 'offer-negotiation', 'pilot-cancel']) {
    book.armCase(caseId); const ticket = book.claim(); book.reserve(ticket, ticket.maxTokens, 'UNCHANGED');
    book.markTransport(ticket, 'outboundStarted'); book.markTransport(ticket, 'upstreamResponded');
    if (caseId === 'pilot-cancel') { book.markTransport(ticket, 'clientDisconnectObserved'); book.finish(ticket, 'DISCONNECT'); }
    else book.finish(ticket, 'SETTLED', { prompt_tokens: 20, completion_tokens: 3, prompt_cache_hit_tokens: 0,
      prompt_cache_miss_tokens: 20, total_tokens: 23 });
  }
  book.close();
  return { ...book.snapshot(), journalFailed: false, provenance: { priceCheckedAt: '2026-10-16',
    validUntilUtc: '2026-10-16T23:59:59.999Z', priceBasis: 'PEAK_CNY',
    sessionId: 'offerpilot-fixed-exe-real-ai-20261008', mode: 'FIXED_EXE_UI_BUDGET_BROKER',
    productCommit: PIN.commit, buildRunId: String(PIN.runId),
    artifactId: String(PIN.artifactId), installerSha256: PIN.installerSha256, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), runId: '1', runAttempt: 1 } };
}
test('saved LIVE completion-only artifact carries coverage limit and rejects inconsistent reports or ledgers', async t => {
  const fake = await harness(t), proof = await fake.prove();
  const report = { mode: 'live', status: 'PASS', code: 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY', cleanupCode: 'CLEANUP_PASSED', cleanupPassed: true,
    scenarios: CASES.map(id => id === 'pilot-stream' ? { id, status: 'PASS', ...proof, checks: { ...proof.checks, oneProviderRequestVerified: true } }
      : { id, status: 'PASS', code: 'PASSED', checks: {} }) };
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'live-completion-evidence-')); t.after(() => fs.rm(directory, { recursive: true, force: true }));
  await saveEvidence(directory, report, fullLedger(), [SECRET, ID.request_id, ID.turn_id]);
  const artifact = JSON.parse(await fs.readFile(path.join(directory, 'result.json'), 'utf8'));
  assert.deepEqual(artifact.coverageLimitations, ['PILOT_SUSTAINED_STREAMING_NOT_PROVEN']);
  assert.equal(artifact.scenarios[1].checks.sustainedStreamingObserved, false);
  assert.equal(artifact.releaseReady, false); assert.equal(artifact.independentFullGateCertified, false);
  for (const mutate of [v => { v.code = 'ALL_UI_CASES_PASSED'; }, v => { v.mode = 'mock'; },
    v => { v.cleanupPassed = false; }, v => { v.scenarios[1].code = 'LIVE_STREAMING_PROVEN'; }]) {
    const altered = structuredClone(report); mutate(altered); await assert.rejects(saveEvidence(directory, altered, fullLedger()));
  }
  for (const mutate of [v => { v.requests[1].status = 'CANCELLED'; }, v => { v.requests[1].outboundStarted = false; },
    v => { v.requests[1].upstreamResponded = false; }, v => { v.requests[1].clientDisconnectObserved = true; },
    v => { v.requests[1].micro++; }, v => { v.denied.AUTH++; }, v => { v.journalFailed = true; },
    v => { v.closed = false; }, v => { v.active = true; }, v => { v.mode = 'MOCK'; }, v => { v.mock = {}; }]) {
    const altered = fullLedger(); mutate(altered); await assert.rejects(saveEvidence(directory, report, altered));
  }
  const failedLater = structuredClone(report); failedLater.status = 'BLOCKED'; failedLater.code = 'PROVIDER_BUDGET_BLOCKED';
  failedLater.scenarios[2] = { id: 'pilot-cancel', status: 'BLOCKED', code: 'PROVIDER_BUDGET_BLOCKED', checks: {} };
  const failedLedger = fullLedger(); failedLedger.denied.AUTH = 1; failedLedger.journalFailed = true;
  await saveEvidence(directory, failedLater, failedLedger);
  const failureArtifact = JSON.parse(await fs.readFile(path.join(directory, 'result.json'), 'utf8'));
  assert.equal(failureArtifact.status, 'BLOCKED');
  assert.deepEqual(failureArtifact.coverageLimitations, ['PILOT_SUSTAINED_STREAMING_NOT_PROVEN']);
  const rows = structuredClone(report.scenarios); rows[1].code = 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY';
  assert.throws(() => safeResults(rows)); assert.throws(() => safeResults(rows, true));
  rows[1] = { id: 'pilot-stream', status: 'FAIL', code: 'STREAM_NOT_OBSERVED', checks: { naturalCompletionProven: true } };
  assert.throws(() => safeResults(rows, true));
});

for (const key of ['unknown denial', 'unknown row field']) test(`${key} introduced during DOM read invalidates ledger fingerprint`, async t => {
  const fake = await harness(t); fake.transport.terminalHook = () => {
    if (key === 'unknown denial') fake.final.denied.NEW = 0; else fake.final.requests[1].unreviewed = true;
  }; await blocked(fake);
});

test('LIVE diagnostic distinguishes immediate status identity rejection from observation timeout', async t => {
  const fake = await harness(t, { deadline: 75000 });
  fake.status.execution.conversation_id += 1;
  let failure;
  try { await fake.prove(); } catch (error) { failure = error; }
  assert.equal(failure?.code, 'LIVE_COMPLETION_UNPROVEN');
  assert.equal(failure.pilotProofDiagnostic.fault, 'STATUS_IDENTITY_INVALID');
  assert.equal(failure.pilotProofDiagnostic.admissionObserved, true);
  assert.equal(failure.pilotProofDiagnostic.admissionValid, null);
  assert.equal(failure.pilotProofDiagnostic.lastValidState, null);
  assert.ok(failure.pilotProofDiagnostic.elapsedMs < 5000);
  assert.equal(fake.sends(), 1); assert.equal(fake.owner.responses.size, 0);
  assert.doesNotMatch(JSON.stringify(failure.pilotProofDiagnostic), /PRIVATE_|26e756|turn-synthetic|http|conversation_id|token/);
});
test('LIVE diagnostic retains valid pending state and distinguishes deadline without a second send', async t => {
  const fake = await harness(t, { deadline: 120 });
  const pending = terminal(); pending.state = pending.execution.state = 'running';
  pending.worker_done = pending.execution.worker_done = false;
  pending.actual_worker_alive = pending.execution.actual_worker_alive = true;
  pending.terminal = {}; pending.budget = { model_calls: 1 }; fake.setStatus(pending);
  let failure;
  try { await fake.prove(); } catch (error) { failure = error; }
  assert.equal(failure?.pilotProofDiagnostic.fault, 'OBSERVE_DEADLINE');
  assert.equal(failure.pilotProofDiagnostic.lastValidState, 'running');
  assert.equal(failure.pilotProofDiagnostic.workerDone, false);
  assert.equal(failure.pilotProofDiagnostic.actualWorkerAlive, true);
  assert.equal(failure.pilotProofDiagnostic.modelCallsReserved, 1, 'runtime reservation count, not HTTP count');
  assert.ok(failure.pilotProofDiagnostic.pollCount > 0);
  assert.equal(fake.sends(), 1); assert.equal(fake.owner.responses.size, 0);
});
test('LIVE diagnostic rejects non-404 status failure immediately without leaking body', async t => {
  const fake = await harness(t, { deadline: 120 });
  fake.transport.statusOptions = { status: 503 };
  let failure;
  try { await fake.prove(); } catch (error) { failure = error; }
  assert.equal(failure?.pilotProofDiagnostic.fault, 'READ_FAILED');
  assert.equal(failure.pilotProofDiagnostic.lastReadOutcome, 'HTTP_STATUS');
  assert.equal(failure.pilotProofDiagnostic.lastHttpStatus, 503);
  assert.equal(failure.pilotProofDiagnostic.lastValidState, null);
  assert.equal(fake.sends(), 1); assert.equal(fake.owner.responses.size, 0);
});
test('first LIVE diagnostic survives admission cleanup failure', async t => {
  const fake = await harness(t), off = fake.owner.page.off;
  fake.status.execution.conversation_id += 1;
  fake.owner.page.off = (...args) => { off(...args); throw new Error(SECRET); };
  let failure;
  try { await fake.prove(); } catch (error) { failure = error; }
  assert.equal(failure?.code, 'LIVE_COMPLETION_UNPROVEN');
  assert.equal(failure.pilotProofDiagnostic.fault, 'STATUS_IDENTITY_INVALID');
  assert.equal(fake.owner.responses.size, 0);
  assert.doesNotMatch(JSON.stringify(failure.pilotProofDiagnostic), /PRIVATE_/);
});

test('shared strict observer keeps MOCK identity and cannot certify a LIVE wrapper', async t => {
  const { observePilotCompletion } = await import('../live-pilot-completion.mjs');
  const fake = await harness(t);
  fake.args.mode = 'mock'; fake.args.broker.mode = 'MOCK';
  for (const value of [fake.prior, fake.final]) Object.assign(value, { mode: 'MOCK', mock: {
    transport: 'IN_PROCESS_SYNTHETIC_HTTPS', externalNetwork: 'DENIED', realProviderCalls: 0,
    usageIsSynthetic: true, fakeRequests: value.sentRequests, clockScope: 'BROKER_VM_ONLY' } });
  await assert.rejects(fake.prove(), { code: 'LIVE_COMPLETION_UNPROVEN' });
  assert.equal(fake.sends(), 0);
  const result = await observePilotCompletion(fake.args);
  assert.equal(result.code, 'COMPLETION_ONLY'); assert.equal(fake.sends(), 1);
  assert.equal(fake.prior.mode, 'MOCK'); assert.equal(fake.final.mode, 'MOCK');
  assert.equal(isLiveLedgerProven(fake.final, fake.prior, 1), false);
});

test('strict offline proof results are MOCK-only and cannot carry LIVE-exclusive checks', async t => {
  const { MOCK_PILOT_CHECK_MAP } = await import('../ui-diagnostics.mjs');
  const fake = await harness(t, { running: 'both', growth: 2 }), live = await fake.prove();
  const mockChecks = Object.fromEntries(Object.entries(live.checks).map(([key, value]) => [MOCK_PILOT_CHECK_MAP[key] ?? key, value]));
  const rows = CASES.map(id => ({ id, status: 'BLOCKED', code: 'NOT_STARTED', checks: {} }));
  rows[1] = { id: 'pilot-stream', status: 'PASS', code: 'MOCK_PILOT_STREAMING_PROVEN',
    checks: { ...mockChecks, oneProviderRequestVerified: true } };
  assert.equal(safeResults(rows, true)[1].code, 'MOCK_PILOT_STREAMING_PROVEN');
  const completionOnly = structuredClone(rows); completionOnly[1].code = 'MOCK_PILOT_COMPLETION_ONLY';
  completionOnly[1].checks.mockPilotSustainedStreamingObserved = false;
  completionOnly[1].checks.incrementalAssistantRendering = false;
  assert.throws(() => safeResults(completionOnly, true), 'MOCK streaming cannot be relaxed to completion-only PASS');
  const cleanupFailed = structuredClone(rows);
  cleanupFailed[1] = { ...cleanupFailed[1], status: 'BLOCKED', code: 'PROVIDER_BUDGET_BLOCKED',
    checks: { ...cleanupFailed[1].checks, brokerCleanupFailed: true } };
  assert.equal(safeResults(cleanupFailed, true)[1].status, 'BLOCKED', 'failed cleanup preserves bounded prior proof evidence');
  assert.throws(() => safeResults(rows, false));
  for (const key of Object.values(MOCK_PILOT_CHECK_MAP)) {
    const missing = structuredClone(rows); delete missing[1].checks[key]; assert.throws(() => safeResults(missing, true));
  }
  const mixed = structuredClone(rows); mixed[1].checks.naturalCompletionProven = true;
  assert.throws(() => safeResults(mixed, true));
  const relabelled = structuredClone(rows); relabelled[1].code = 'PASSED'; assert.throws(() => safeResults(relabelled, true));
  const misplaced = structuredClone(rows); misplaced[0] = { ...misplaced[1], id: 'connection' };
  assert.throws(() => safeResults(misplaced, true));
});

for (const recovery of [false, true]) test(`${recovery ? 'recovery ' : ''}fixed five-second read bound fails before the independent 75-second observation deadline`, async t => {
  const fake = await harness(t, { deadline: 75000, recovery });
  let entered, didEnter = false;
  const reading = new Promise(resolve => { entered = resolve; });
  const evaluate = fake.owner.page.evaluate;
  fake.owner.page.evaluate = (fn, arg) => {
    if (fn === readLivePilotJson && arg.requestId !== null) { didEnter = true; entered(); return new Promise(() => {}); }
    return evaluate(fn, arg);
  };
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const result = fake.prove().then(() => assert.fail('stalled read cannot pass'), error => error);
  for (let n = 0; n < 50 && !didEnter; n += 1) { await flush(); t.mock.timers.tick(20); }
  assert.equal(didEnter, true, 'exact submitted request must bind before timing the stalled status read');
  await reading; t.mock.timers.tick(5000);
  const error = await result;
  assert.equal(error.pilotProofDiagnostic.fault, 'OPERATION_TIMEOUT');
  assert.equal(error.pilotProofDiagnostic.faultStage, 'STATUS_AND_MIRROR_READ');
  assert.equal(error.pilotProofDiagnostic.admissionValid, null);
  assert.equal(error.pilotProofDiagnostic.lastValidState, null);
  assert.equal(fake.owner.responses.size, 0); assert.equal(fake.sends(), 1);
});

test('LIVE runner and scenario boundary reject offline proof injection before preparation or broker creation', async () => {
  const { executeValidation } = await import('../validation-runner.mjs');
  const { runUiScenarios } = await import('../ui-scenarios.mjs');
  const forbidden = () => assert.fail('LIVE cannot reach offline capability');
  await assert.rejects(executeValidation({ mode: 'live', brokerFactory: forbidden,
    mockPilotProofFactory: forbidden }), { safeCode: 'INVALID_HARNESS' });
  const report = await runUiScenarios({ mode: 'live', page: {}, haru: {}, api: forbidden, fixture: {},
    mockPilotProof: forbidden, broker: { prepareCase: forbidden, armCase: forbidden, cancelCase: forbidden, snapshot: forbidden } });
  assert.equal(report.allPassed, false);
  assert.ok(report.results.every(row => row.status === 'BLOCKED' && row.code === 'INVALID_HARNESS'));
});

for (const [sustained, recovery] of [[false, false], [true, false], [false, true], [true, true]]) test(`actual branded offline ${recovery ? 'carry-forward ' : ''}proof ${sustained ? 'preserves streaming PASS' : 'blocks completion-only'}`, async t => {
  const diagnosticNames = ['NODE_DEBUG', 'NODE_DEBUG_NATIVE', 'NODE_OPTIONS', 'NODE_EXTRA_CA_CERTS',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_TLS_REJECT_UNAUTHORIZED'];
  const savedDiagnostics = Object.fromEntries(diagnosticNames.map(name => [name, process.env[name]]));
  for (const name of diagnosticNames) delete process.env[name];
  t.after(() => { for (const name of diagnosticNames) if (savedDiagnostics[name] !== undefined) process.env[name] = savedDiagnostics[name]; });
  const { createOfflineBroker, createOfflineRecoveryBroker } = await import('../mock-provider.cjs');
  const { createOfflinePilotProof } = await import('../offline-pilot-completion.mjs');
  const { request } = await import('node:http');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offline-strict-proof-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const broker = await (recovery ? createOfflineRecoveryBroker : createOfflineBroker)({ ledgerPath: path.join(directory, 'MOCK-ledger.jsonl'),
    sessionId: 'offerpilot-fixed-exe-real-ai-20261008', runId: '1', runAttempt: 1, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40) });
  t.after(() => broker.close());
  const send = (token, stream = false) => new Promise((resolve, reject) => {
    const req = request(`${broker.origin}/v1/chat/completions`, { method: 'POST', agent: false,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } }, res => {
      assert.equal(res.statusCode, 200); res.resume(); res.on('end', resolve); res.on('error', reject);
    });
    req.on('error', reject);
    req.end(JSON.stringify({ model: 'deepseek-flash', messages: [{ role: 'user', content: 'MOCK only' }], ...(stream ? { stream: true } : {}) }));
  });
  if (!recovery) { const connection = broker.prepareCase('connection'); broker.armCase('connection'); await send(connection.clientToken); }
  const pilot = broker.prepareCase('pilot-stream');
  const fake = await harness(t, { running: sustained ? 'both' : 'none', growth: sustained ? 2 : 0, deadline: 75000 });
  const prove = createOfflinePilotProof({ mode: 'mock', broker });
  const sendUi = fake.args.send;
  Object.assign(fake.args, { mode: 'mock', broker, send: async () => { broker.armCase('pilot-stream'); await sendUi(); } });
  let providerDone;
  fake.transport.afterSend = async () => {
    // UI send returns at admission, not at the end of the asynchronous stream.
    providerDone = send(pilot.clientToken, true);
    const stop = Date.now() + 2000;
    while (broker.snapshot().sentRequests !== 2) {
      assert.ok(Date.now() < stop, 'synthetic request was not admitted');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
  };
  const stages = []; fake.args.ctx.mark = stage => stages.push(stage);
  let outcome;
  if (sustained) {
    outcome = await prove(fake.args);
    assert.equal(outcome.code, 'MOCK_PILOT_STREAMING_PROVEN');
    assert.equal(outcome.checks.mockPilotNaturalCompletionProven, true);
    assert.equal(outcome.checks.mockPilotSustainedStreamingObserved, true);
    assert.equal(outcome.checks.naturalCompletionProven, undefined);
  } else {
    await assert.rejects(prove(fake.args), error => {
      assert.equal(error.code, 'MOCK_PILOT_COMPLETION_UNPROVEN');
      assert.equal(error.pilotProofDiagnostic.fault, 'MOCK_STREAMING_UNPROVEN');
      assert.equal(error.pilotProofDiagnostic.naturalCompletionProven, true); return true;
    });
  }
  await providerDone;
  assert.equal(broker.snapshot().mode, 'MOCK');
  assert.equal(broker.snapshot().mock.realProviderCalls, 0);
  assert.equal(broker.snapshot().sentRequests, 2);
  assert.equal(broker.snapshot().requests[1].status, 'SETTLED');
  if (recovery) {
    assert.equal(broker.snapshot().currentRequests, 1); assert.equal(broker.snapshot().mock.fakeRequests, 1);
    assert.equal(broker.snapshot().carryForward.source.synthetic, true);
    assert.equal(broker.snapshot().requests[0].micro, 188);
  }
  assert.equal(fake.owner.responses.size, 0); assert.equal(fake.sends(), 1);
  assert.ok(stages.length >= 4 && stages.every(stage => stage.startsWith('MOCK_PILOT_PROOF_')));
  if (sustained) {
    await broker.close();
    const ledger = broker.snapshot();
    const scenarios = CASES.map(id => ({ id, status: 'BLOCKED', code: 'NOT_STARTED', checks: {} }));
    scenarios[0] = { id: 'connection', status: 'PASS', code: 'PASSED', checks: {} };
    scenarios[1] = { id: 'pilot-stream', status: 'PASS', ...outcome,
      checks: { ...outcome.checks, oneProviderRequestVerified: true } };
    await saveEvidence(path.join(directory, 'MOCK-evidence'), { mode: 'mock',
      status: recovery ? 'PASS' : 'BLOCKED', code: recovery ? 'PILOT_HARU_RECOVERY_PASSED' : 'UI_CASES_INCOMPLETE',
      ...(recovery ? { scope: RECOVERY.scope } : {}),
      cleanupCode: 'CLEANUP_PASSED', cleanupPassed: true,
      scenarios: recovery ? scenarios.filter(row => row.id === 'pilot-stream') : scenarios }, ledger, [SECRET, ID.request_id, ID.turn_id]);
    const artifact = JSON.parse(await fs.readFile(path.join(directory, 'MOCK-evidence', 'result.json'), 'utf8'));
    assert.equal(artifact.mode, 'MOCK'); assert.equal(artifact.realProviderCalled, false);
    assert.equal(artifact.scenarios[recovery ? 0 : 1].code, 'MOCK_PILOT_STREAMING_PROVEN');
    assert.doesNotMatch(JSON.stringify(artifact), /LIVE_|PRIVATE_|26e756|turn-synthetic/);
  }
});

for (const body of ['invalid', '{"request_id":"x","conversation_id":0}',
  JSON.stringify({ request_id: ID.request_id, conversation_id: 73 }),
  '{"request_id":"' + ID.request_id + '","request_id":"' + ID.request_id + '","conversation_id":0}',
  JSON.stringify({ request_id: ID.request_id, conversation_id: 0, message: '界'.repeat(23000) })]) {
  test('noncanonical, wrong or oversized synthetic request identity fails closed', async t => {
    assert.throws(() => projectUiRequest(body));
    const fake = await harness(t); fake.transport.admissionOptions.requestBody = body; await blocked(fake);
  });
}
for (const [name, change] of [
  ['different original request', v => { v.request_id = '86e756a3-f74c-4e63-8ae0-3d42ff3976f9'; }],
  ['different nested request', v => { v.execution.request_id = '86e756a3-f74c-4e63-8ae0-3d42ff3976f9'; }],
  ['old generation', v => { v.execution_generation = v.execution.execution_generation = 2; }],
  ['replayed', v => { v.replayed = true; }], ['manager recovery', v => { v.recovery.requires_resync = true; }],
  ['recovered terminal', v => { v.terminal.response.type = 'turn_recovered'; }],
]) test(`request recovery rejects ${name}`, async t => {
  const fake = await harness(t); change(fake.status); await blocked(fake);
});

test('PIN ordinary message reports write_status none and is a no-write terminal', async () => {
  const service = await readPinnedSource('src/offerpilot/pilot_runtime/service.py');
  const sink = await readPinnedSource('src/offerpilot/pilot_runtime/event_sink.py');
  assert.match(service, /if not attempted:\s+return "none", ""/);
  assert.match(service, /MessageOutcome\(\s+message=reply,\s+conversation_id=conversation_id,\s+write_status=cast\(Any, write_status\)/);
  assert.match(sink, /_optional\(payload, "write_status", outcome\.write_status\)/);
  const value = terminal(); value.terminal.response.write_status = 'none';
  assert.equal(projectTerminal(value, ID), SECRET);
});

for (const writeStatus of [null, false, 0, '', 'NONE', 'success', 'failed', 'cancelled', {}, ['none']]) {
  test(`terminal no-write exception rejects non-exact value ${JSON.stringify(writeStatus)}`, () => {
    const value = terminal(); value.terminal.response.write_status = writeStatus;
    assert.throws(() => projectTerminal(value, ID));
  });
}
for (const [name, mutate] of [
  ['top-level duplicate', v => { v.write_status = 'none'; }],
  ['execution duplicate', v => { v.execution.write_status = 'none'; }],
  ['nested duplicate', v => { v.terminal.response.extra = { write_status: 'none' }; }],
  ['array duplicate', v => { v.terminal.response.extra = [{ write_status: 'none' }]; }],
  ['operation', v => { v.terminal.response.operation_id = null; }],
  ['non-message', v => { v.terminal.response.type = 'other'; }],
  ['inherited type', v => { const r = v.terminal.response; delete r.type; Object.setPrototypeOf(r, { type: 'message' }); }],
  ['inherited operation', v => { Object.setPrototypeOf(v.terminal.response, { operation_id: 'private' }); }],
  ['own proto key', v => { Object.defineProperty(v.terminal.response, '__proto__', { value: { write_status: 'none' }, enumerable: true }); }],
  ['constructor key', v => { v.terminal.response.constructor = { write_status: 'none' }; }],
]) test(`terminal no-write exception rejects ${name}`, () => {
  const value = terminal(); value.terminal.response.write_status = 'none'; mutate(value);
  assert.throws(() => projectTerminal(value, ID));
});
test('admission cannot adopt terminal no-write exception', () => {
  const value = admission(); value.terminal = { response: { type: 'message', write_status: 'none' } };
  assert.throws(() => projectAdmission(value, 202));
});
test('dotted property cannot impersonate the exact terminal response path', () => {
  const value = terminal(); value['terminal.response'] = { type: 'message', write_status: 'none' };
  assert.throws(() => projectTerminal(value, ID));
});

test('PIN intentionally aborts hanging POST only after exact request GET recovery', () => {
  const controller = readPinnedSource('web/src/features/assistantSurface/usePilotConversationController.ts');
  const start = controller.indexOf('const target = await getRuntimeRequestExecution(request.requestId, lookup.signal);');
  const recovery = controller.slice(start, controller.indexOf('}, [observationEnabled, requestLoading', start));
  assert.ok(start > 0); assert.match(recovery, /request.execution = target;[\s\S]*request.controller.abort\(\);/);
  assert.match(recovery, /setTimeout\(\(\) => void read\(\), 2000\)/);
  const runtime = readPinnedSource('web/src/services/pilotRuntime.ts');
  assert.match(runtime, /requests\/\$\{encodeURIComponent\(requestId\)\}/);
  assert.match(runtime, /value.request_id !== requestId/);
  const chat = readPinnedSource('web/src/services/chat.ts');
  assert.match(chat, /submitRuntimeTurn\(\{\s+message, request_id: requestId, conversation_id: conversationId \?\? 0/);
  const api = readPinnedSource('src/offerpilot/api.py');
  assert.match(api, /get_runtime_execution_by_request\(checked\)/);
  assert.match(api, /row.get\("submission_request_id"\) != checked/);
  assert.match(api, /"requires_resync": manager_status is None/);
  const control = readPinnedSource('src/offerpilot/pilot_control.py');
  assert.match(control, /PilotExecution.submission_request_id == request_id/);
  const existing = readPinnedSource('web/src/features/assistantSurface/AssistantSurfaceProvider.test.tsx');
  assert.match(existing, /releases a hanging POST only with exact admission proof/);
  assert.match(existing, /request.controller.signal.aborted\).toBe\(proof\)/);
});

test('request GET must keep all four IDs stable until final observation', async t => {
  const fake = await harness(t); let reads = 0;
  const evaluate = fake.owner.page.evaluate;
  fake.owner.page.evaluate = async (fn, arg) => {
    if (fn === readLivePilotJson && arg.requestId && ++reads === 1) {
      const running = terminal(); running.state = running.execution.state = 'running'; running.terminal = {};
      return { value: running, readOutcome: 'OK', httpStatus: 200 };
    }
    if (fn === readLivePilotJson && arg.requestId) fake.status.turn_id = fake.status.execution.turn_id = 'different-turn';
    return evaluate(fn, arg);
  };
  await blocked(fake); assert.equal(fake.sends(), 1);
});

for (const mode of ['initial-404', 'continuous-404', 'bound-404', '401', '503', 'wrong-id', 'malformed']) {
  test(`exact request lookup handles ${mode} without POST retry`, async t => {
    const fake = await harness(t, { deadline: 180 }); let reads = 0;
    const evaluate = fake.owner.page.evaluate;
    fake.owner.page.evaluate = async (fn, arg) => {
      if (fn === readLivePilotJson && arg.requestId) {
        reads++;
        if ((mode === 'initial-404' && reads === 1) || mode === 'continuous-404' || (mode === 'bound-404' && reads === 2)) {
          return { value: null, readOutcome: 'HTTP_STATUS', httpStatus: 404 };
        }
        if (mode === 'bound-404' && reads === 1) {
          const value = terminal(); value.state = value.execution.state = 'running'; value.terminal = {};
          return { value, readOutcome: 'OK', httpStatus: 200 };
        }
        if (['401', '503'].includes(mode)) return { value: null, readOutcome: 'HTTP_STATUS', httpStatus: Number(mode) };
        if (mode === 'malformed') return { value: null, readOutcome: 'DECODE_OR_JSON', httpStatus: 200 };
        if (mode === 'wrong-id') fake.status.request_id = '76e756a3-f74c-4e63-8ae0-3d42ff3976f9';
      }
      return evaluate(fn, arg);
    };
    if (mode === 'initial-404') { assert.equal((await fake.prove()).code, 'LIVE_COMPLETION_ONLY'); assert.equal(reads, 2); }
    else {
      const expected = mode === 'continuous-404' ? 'OBSERVE_DEADLINE' : mode === 'wrong-id' ? 'STATUS_IDENTITY_INVALID' : 'READ_FAILED';
      await assert.rejects(fake.prove(), error => { assert.equal(error.pilotProofDiagnostic.fault, expected); return true; });
      if (['401', '503', 'malformed'].includes(mode)) assert.equal(reads, 1);
      if (mode === 'bound-404') assert.equal(reads, 2);
    }
    assert.equal(fake.sends(), 1); assert.equal(fake.owner.responses.size, 0);
  });
}

for (const config of [{ status: 404, redirected: true }, { status: 404, url: 'http://127.0.0.1:41000/other' }]) {
  test('404 from a redirected or wrong route is not an admissible pending lookup', async t => {
    const fake = await harness(t); fake.transport.statusOptions = config;
    await assert.rejects(fake.prove(), error => {
      assert.equal(error.pilotProofDiagnostic.fault, 'READ_FAILED');
      assert.equal(error.pilotProofDiagnostic.lastReadOutcome, 'ROUTE_MISMATCH'); return true;
    }); assert.equal(fake.sends(), 1);
  });
}

function preManager() {
  const value = terminal(); value.terminal = {}; value.recovery.requires_resync = true;
  for (const v of [value, value.execution]) Object.assign(v, { state: 'running', worker_done: true, actual_worker_alive: false, budget: {} });
  return value;
}
function managerPhase(state) {
  const value = terminal();
  for (const v of [value, value.execution]) Object.assign(v, { state, worker_done: false, actual_worker_alive: true, budget: { model_calls: 1 } });
  if (state !== 'completed') value.terminal = {};
  return value;
}
test('full original request sequence: 404, durable lease, manager queue/run, draining, terminal', async t => {
  const fake = await harness(t, { deadline: 1000 }); const values = [null, preManager(), managerPhase('queued'), managerPhase('running'), managerPhase('completed'), terminal()];
  let reads = 0; const evaluate = fake.owner.page.evaluate;
  fake.owner.page.evaluate = async (fn, arg) => {
    if (fn === readLivePilotJson && arg.requestId) {
      const value = values[reads++]; assert.ok(reads <= values.length);
      return { value, readOutcome: value ? 'OK' : 'HTTP_STATUS', httpStatus: value ? 200 : 404 };
    }
    return evaluate(fn, arg);
  };
  assert.equal((await fake.prove()).code, 'LIVE_COMPLETION_ONLY'); assert.equal(reads, values.length); assert.equal(fake.sends(), 1);
});
for (const [name, mutate] of [
  ['terminal', v => { v.terminal = terminal().terminal; }],
  ['completed', v => { v.state = v.execution.state = 'completed'; }],
  ['queued', v => { v.state = v.execution.state = 'queued'; }],
  ['failed', v => { v.state = v.execution.state = 'failed'; }],
  ['pending confirmation', v => { v.pending_action = null; }],
  ['write', v => { v.operation_id = null; }],
  ['budget', v => { v.budget.model_calls = 1; }],
  ['nested budget', v => { v.execution.budget.model_calls = 1; }],
  ['worker alive', v => { v.actual_worker_alive = true; }],
  ['worker unfinished', v => { v.execution.worker_done = false; }],
  ['accepted timestamp', v => { v.accepted_at = 1; }],
  ['auto resume', v => { v.recovery.auto_resume = true; }],
  ['old generation', v => { v.execution_generation = v.execution.execution_generation = 2; }],
]) test(`pre-manager waiting refuses ${name}`, () => {
  const value = preManager(); mutate(value); assert.throws(() => projectRequestExecution(value, ID));
});
test('manager loss after its first observation never becomes startup waiting', () => {
  assert.deepEqual(projectRequestExecution(preManager(), ID), ID);
  assert.throws(() => projectRequestExecution(preManager(), ID, true), { proofFault: 'STATUS_RECOVERY_INVALID' });
});
test('permanent pre-manager lease remains bounded and cannot prove completion', async t => {
  const fake = await harness(t, { deadline: 140 }); fake.setStatus(preManager());
  await assert.rejects(fake.prove(), error => { assert.equal(error.pilotProofDiagnostic.fault, 'OBSERVE_DEADLINE'); return true; });
  assert.equal(fake.sends(), 1);
});
test('PIN persistence, manager registration and pre-manager payload ordering are fixed', () => {
  const api = readPinnedSource('src/offerpilot/api.py');
  const start = api.indexOf('    def _submit_runtime_turn(');
  const end = api.indexOf('    @app.get(f"{runtime_base_path}/turns/{{turn_id}}")', start);
  assert.ok(start > 0 && end > start, 'exact PIN submit function boundaries must exist');
  const submit = api.slice(start, end);
  const admit = submit.indexOf('_runtime_admit_start('), register = submit.indexOf('runtime_manager.submit(');
  assert.ok(admit > 0 && register > admit);
  assert.match(api, /on_admitted=claim/);
  const timeline = readPinnedSource('src/offerpilot/pilot_timeline.py');
  assert.match(timeline, /on_admitted\(session, turn\)[\s\S]*?session.commit\(\)\s+return result/);
  const controls = readPinnedSource('src/offerpilot/pilot_control.py');
  assert.match(controls, /owner_token=lease.owner_token, state="running"/);
  assert.match(api, /actual_worker_alive = False\s+worker_done = True\s+accepted_at = None\s+deadline_at = None\s+budget = \{\}/);
  assert.match(api, /payload\["terminal"\] = \{\}/);
  const manager = readPinnedSource('src/offerpilot/pilot_runtime/managed_execution.py');
  assert.match(manager, /handle.state = "queued"[\s\S]*?self._runs\[key\] = handle/);
  assert.match(manager, /handle.state = "running"/);
  assert.match(manager, /actual_worker_alive=not handle.worker_done/);
});

for (const mode of ['manager-loss', 'pre-manager-identity-change']) test(`stateful sequence refuses ${mode}`, async t => {
  const fake = await harness(t, { deadline: 1000 }); let reads = 0;
  const evaluate = fake.owner.page.evaluate;
  fake.owner.page.evaluate = async (fn, arg) => {
    if (fn === readLivePilotJson && arg.requestId) {
      reads++;
      const value = reads === 1 ? (mode === 'manager-loss' ? managerPhase('running') : preManager()) : preManager();
      if (reads > 1 && mode === 'pre-manager-identity-change') value.turn_id = value.execution.turn_id = 'changed-turn';
      return { value, readOutcome: 'OK', httpStatus: 200 };
    }
    return evaluate(fn, arg);
  };
  await assert.rejects(fake.prove(), error => {
    assert.equal(error.pilotProofDiagnostic.fault, mode === 'manager-loss' ? 'STATUS_RECOVERY_INVALID' : 'STATUS_IDENTITY_INVALID'); return true;
  }); assert.equal(reads, 2); assert.equal(fake.sends(), 1);
});

test('CJK soft-break layout does not turn exact plain DOM text into wrong output', async t => {
  const content = '一、检查边界。\n二、检查零值。'; const fake = await harness(t, { content });
  Object.defineProperty(fake.owner.body, 'innerText', { get: () => content.replace(/\n/g, ''), set: value => { fake.owner.body.textContent = value; } });
  assert.equal((await fake.prove()).code, 'LIVE_COMPLETION_ONLY');
});
for (const mode of ['empty', 'textpending', 'controls']) test(`asynchronous ${mode} projection waits inside original deadline and verifies exact final text`, async t => {
  const fake = await harness(t, { deadline: 1000 }); let comparisons = 0;
  fake.transport.afterSend = () => {
    if (mode === 'controls') fake.haru.alterState(s => { s.snapshot.canSend = false; });
    else fake.owner.body.textContent = mode === 'empty' ? '' : 'OLDER_SYNTHETIC_CONTENT';
  };
  fake.transport.terminalHook = () => {
    if (++comparisons === 3) {
      fake.owner.body.textContent = SECRET;
      fake.haru.alterState(s => { s.snapshot.canSend = true; });
    }
  };
  assert.equal((await fake.prove()).code, 'LIVE_COMPLETION_ONLY'); assert.equal(comparisons, 3); assert.equal(fake.sends(), 1);
});
for (const mode of ['strong', 'button', 'hidden-paragraph', 'duplicate', 'identity']) test(`renderer ${mode} is immediately blocked, never classified as pending text`, async t => {
  const fake = await harness(t, { deadline: 1000 }); let comparisons = 0;
  fake.transport.terminalHook = () => { comparisons++; };
  fake.transport.afterSend = () => {
    if (mode === 'strong' || mode === 'button') fake.owner.paragraph.childNodes = [{ nodeType: 1, tagName: mode === 'strong' ? 'STRONG' : 'BUTTON', childNodes: [], textContent: SECRET }];
    if (mode === 'hidden-paragraph') fake.owner.paragraph.style.visibility = 'hidden';
    if (mode === 'duplicate') fake.owner.hiddenLast();
    if (mode === 'identity') fake.haru.alterState(s => { s.snapshot.conversationId = 74; });
  };
  await assert.rejects(fake.prove(), error => {
    assert.equal(error.pilotProofDiagnostic.fault, ['strong', 'button'].includes(mode) ? 'RENDERER_FORMAT_UNSUPPORTED' : mode === 'identity' ? 'MIRROR_INVALID' : 'RENDERER_UNPROVEN'); return true;
  }); assert.equal(comparisons, mode === 'identity' ? 0 : 1); assert.equal(fake.sends(), 1);
});
test('PIN plain renderer templates preserve Markdown text nodes and literal Haru compact text', () => {
  const bubble = readPinnedSource('web/src/components/ChatPanel/MessageBubble.tsx');
  assert.match(bubble, /<div className=\{styles.markdown\}>\s+<ReactMarkdown remarkPlugins=\{\[remarkGfm\]\}/);
  assert.match(bubble, /\{turn.content\}\s+<\/ReactMarkdown>/);
  const haru = readPinnedSource('web/src/features/assistantSurface/DesktopHaruWindow.tsx');
  assert.match(haru, /<p>\{message.content\}<\/p>/);
  const css = readPinnedSource('web/src/features/assistantSurface/DesktopHaruWindow.css');
  assert.match(css, /\.desktop-haru-chat p \{[^}]*white-space: pre-wrap/);
});


for (const [name, mutate, expected] of [
  ['missing bridge', f => { f.context.window.offerpilotDesktop = null; }, 'BRIDGE'],
  ['disconnected', f => f.alterState(s => { s.connected = false; }), 'CONNECTION'],
  ['invalid generation', f => f.alterState(s => { s.generation = -1; }), 'GENERATION'],
  ['missing snapshot', f => f.alterState(s => { s.snapshot = null; }), 'SNAPSHOT'],
  ['invalid conversation', f => f.alterState(s => { s.snapshot.conversationId = null; }), 'CONVERSATION'],
  ['pending', f => f.alterState(s => { s.snapshot.hasPending = true; }), 'PENDING_ACTION'],
  ['stopping', f => f.alterState(s => { s.snapshot.stopping = true; }), 'STOPPING'],
  ['missing error', f => f.alterState(s => { delete s.snapshot.error; }), 'ERROR'],
  ['null stop message', f => f.alterState(s => { s.snapshot.stopMessage = null; }), 'STOP_MESSAGE'],
  ['invalid messages', f => f.alterState(s => { s.snapshot.messages = null; }), 'MESSAGES'],
  ['failed task', f => f.alterState(s => { s.snapshot.taskState = 'failed'; }), 'TASK_STATE'],
  ['invalid loading', f => f.alterState(s => { s.snapshot.loading = null; }), 'LOADING'],
  ['stop still allowed', f => f.alterState(s => { s.snapshot.canStop = true; }), 'STOP_CONTROL'],
  ['duplicate root', f => f.duplicateRoot(), 'ROOT_COUNT'],
  ['hidden document', f => { f.context.document.visibilityState = 'hidden'; }, 'ROOT_VISIBILITY'],
  ['transparent root', f => { f.root.style.opacity = '0'; }, 'OPACITY_ZERO'],
  ['duplicate body', f => f.hiddenLast(), 'DUPLICATE_BODY'],
  ['hidden body', f => { f.body.style.visibility = 'hidden'; }, 'BODY_VISIBILITY'],
  ['transparent body ancestor', f => { f.body.parentElement = { parentElement: f.root, style: { opacity: '0' } }; }, 'OPACITY_ZERO'],
  ['hidden markdown', f => { f.markdown.style.visibility = 'hidden'; }, 'MARKDOWN_VISIBILITY'],
  ['hidden paragraph', f => { f.paragraph.style.visibility = 'hidden'; }, 'PARAGRAPH_VISIBILITY'],
]) test(`MOCK renderer records exact ${name} gate without changing rejection`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); mutate(f);
  const observed = await f.page.evaluate(readLiveTerminalRenderer, { role: 'owner', deadline: Date.now() + 1000,
    diagnostics: true, expectedGeneration: 0, expectedConversationId: 73 });
  assert.equal(observed.status, 'UNSAFE'); assert.equal(observed.diagnostic.reason, expected);
  assert.equal(observed.diagnostic.documentVisible, name === 'missing bridge' ? null : name !== 'hidden document');
  assert.doesNotMatch(JSON.stringify(observed.diagnostic), /PRIVATE_|conversationId|generation"|class|path|url/);
  if (name === 'duplicate root') assert.equal(observed.diagnostic.rootCount, 2);
  if (name === 'duplicate body') assert.equal(observed.diagnostic.bodyCount, 2);
  if (name === 'transparent body ancestor') {
    assert.equal(observed.diagnostic.rootVisible, true); assert.equal(observed.diagnostic.bodyVisible, false);
  }
});

test('renderer defaults to unchanged LIVE result and MOCK identity evidence contains only equality booleans', async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73));
  const args = { role: 'owner', deadline: Date.now() + 1000, expectedGeneration: 9, expectedConversationId: 74 };
  const live = await f.page.evaluate(readLiveTerminalRenderer, args);
  assert.equal(live.status, 'READY'); assert.equal(live.diagnostic, undefined);
  const mock = await f.page.evaluate(readLiveTerminalRenderer, { ...args, diagnostics: true });
  assert.equal(mock.status, 'READY'); assert.equal(mock.diagnostic.generationMatches, false);
  assert.equal(mock.diagnostic.conversationMatches, false);
  assert.equal(mock.diagnostic.bodyCount, 1); assert.equal(mock.diagnostic.assistantCount, 1);
  assert.doesNotMatch(JSON.stringify(mock.diagnostic), /PRIVATE_|73|74|conversationId|expected/);
});

function mockObservationArgs(fake) {
  const snapshot = fake.args.broker.snapshot;
  return { ...fake.args, mode: 'mock', broker: { ...fake.args.broker, mode: 'MOCK', snapshot: () => {
    const value = snapshot();
    return { ...value, mode: 'MOCK', mock: { transport: 'IN_PROCESS_SYNTHETIC_HTTPS', externalNetwork: 'DENIED',
      realProviderCalls: 0, usageIsSynthetic: true, fakeRequests: value.sentRequests, clockScope: 'BROKER_VM_ONLY' } };
  } } };
}

test('MOCK retains both renderer gates before the first rejecting assertion', async t => {
  const fake = await harness(t);
  fake.transport.afterSend = () => { fake.owner.body.style.opacity = '0'; };
  await assert.rejects(observePilotCompletion(mockObservationArgs(fake)), error => {
    const d = error.pilotProofDiagnostic;
    assert.equal(d.fault, 'RENDERER_UNPROVEN'); assert.equal(d.faultStage, 'TERMINAL_RENDER_READ');
    assert.equal(d.rendererReady, null); assert.equal(d.rendererTextMatched, null);
    assert.equal(d.rendererOwner.reason, 'OPACITY_ZERO'); assert.equal(d.rendererOwner.bodyVisible, false);
    assert.equal(d.rendererOwner.rootVisible, true); assert.equal(d.rendererOwner.generationMatches, true);
    assert.equal(d.rendererHaru.status, 'READY'); assert.equal(d.rendererHaru.conversationMatches, true);
    assert.doesNotMatch(JSON.stringify(d), /PRIVATE_|26e756|turn-synthetic|content|bridgeContent|className|http/);
    return true;
  });
  assert.equal(fake.sends(), 1); assert.equal(fake.owner.responses.size, 0);
});

for (const mode of ['live', 'mock']) test(`${mode} hostile renderer diagnostic cannot export arbitrary data`, async t => {
  const fake = await harness(t); const evaluate = fake.owner.page.evaluate;
  fake.owner.page.evaluate = async (fn, arg) => {
    if (fn === readLiveTerminalRenderer) return { status: 'UNSAFE', diagnostic: {
      status: 'PRIVATE_STATUS', reason: 'PRIVATE_REASON', rootCount: 9000, bodyVisible: 'true',
      content: SECRET, conversationId: 73, generation: 44, className: 'PRIVATE_CLASS', path: 'PRIVATE_PATH', url: 'https://PRIVATE' } };
    return evaluate(fn, arg);
  };
  await assert.rejects(mode === 'mock' ? observePilotCompletion(mockObservationArgs(fake)) : fake.prove(), error => {
    const d = error.pilotProofDiagnostic; assert.equal(d.fault, 'RENDERER_UNPROVEN');
    if (mode === 'mock') {
      assert.equal(d.rendererOwner.status, 'UNAVAILABLE'); assert.equal(d.rendererOwner.reason, null);
      assert.equal(d.rendererOwner.rootCount, null); assert.equal(d.rendererOwner.bodyVisible, null);
    } else { assert.equal(d.rendererOwner, undefined); assert.equal(d.rendererHaru, undefined); }
    assert.doesNotMatch(JSON.stringify(d), /PRIVATE|https|className|conversationId|content|path/); return true;
  });
  assert.equal(fake.sends(), 1);
});

function messageEntrance(f, change = () => {}) {
  const row = { isConnected: true, parentElement: f.root, className: '_msg_pin_456',
    style: { display: 'flex', visibility: 'visible', opacity: '0', filter: 'blur(3px)' } };
  const column = { isConnected: true, parentElement: row, style: { display: 'block', visibility: 'visible', opacity: '1' } };
  f.body.parentElement = column;
  const timing = { duration: 400, delay: 40, endDelay: 0, iterations: 1, iterationStart: 0, fill: 'both', direction: 'normal' };
  let resolve, reject, reads = 0;
  const finished = new Promise((yes, no) => { resolve = yes; reject = no; });
  const animation = { animationName: '_opRise_pin_519', playState: 'running', playbackRate: 1,
    effect: { target: row, getTiming: () => timing }, get finished() { reads++; change(); return finished; } };
  row.getAnimations = () => [animation];
  return { row, timing, animation, reads: () => reads, reject,
    finish(visible = true) { animation.playState = 'finished';
      if (visible) { row.style.opacity = '1'; row.style.filter = 'none'; }
      resolve(animation);
    } };
}
const terminalReadArgs = () => ({ role: 'owner', deadline: Date.now() + 1000,
  diagnostics: true, expectedGeneration: 0, expectedConversationId: 73 });

test('PIN message entrance has a finite 400ms opacity/blur transition and capped delay', () => {
  const css = readPinnedSource('web/src/components/ChatPanel/ChatPanel.module.css');
  const bubble = readPinnedSource('web/src/components/ChatPanel/MessageBubble.tsx');
  assert.match(css, /\.msg \{[^}]*animation: opRise 0\.4s var\(--op-ease\) both;/);
  assert.match(css, /@keyframes opRise \{\s+from \{\s+opacity: 0;\s+transform: translateY\(7px\);\s+filter: blur\(3px\);\s+\}\s+to \{\s+opacity: 1;\s+transform: none;\s+filter: none;/);
  assert.match(bubble, /animationDelay: `\$\{Math\.min\(index, 6\) \* 0\.04\}s`/);
});

for (const opacity of ['0', '0.3']) test(`terminal waits for the same finite opRise at opacity ${opacity} to finish visibly`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  entrance.row.style.opacity = opacity;
  let settled = false;
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()).then(value => { settled = true; return value; });
  await flush(); assert.equal(settled, false, 'zero opacity or partially blurred entrance cannot pass');
  assert.equal(entrance.reads(), 1);
  entrance.finish(); const result = await pending;
  assert.equal(result.status, 'READY'); assert.equal(result.content, SECRET);
  assert.equal(result.diagnostic.bodyVisible, true);
});

for (const mode of ['live', 'mock']) test(`${mode} completion awaits one natural entrance and keeps one admitted request`, async t => {
  const fake = await harness(t, { deadline: 1000 }); let entrance;
  fake.transport.afterSend = () => { entrance = messageEntrance(fake.owner, () => entrance.finish()); };
  const proof = await (mode === 'live' ? fake.prove() : observePilotCompletion(mockObservationArgs(fake)));
  assert.equal(proof.checks.terminalTextProven, true); assert.equal(entrance.reads(), 1); assert.equal(fake.sends(), 1);
});

for (const fault of ['permanent-opacity', 'permanent-blur', 'replacement', 'paragraph-replacement', 'generation', 'conversation', 'pending', 'rejected', 'removed-and-restored']) {
  test(`finite entrance still rejects ${fault} after waiting`, async () => {
    const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
    const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
    assert.equal(entrance.reads(), 1, 'the known animation must be observed before mutation');
    if (fault === 'replacement') f.replaceBody();
    if (fault === 'paragraph-replacement') f.markdown.childNodes = [{ ...f.paragraph }];
    if (fault === 'generation') f.alterState(s => { s.generation = 1; });
    if (fault === 'conversation') f.alterState(s => { s.snapshot.conversationId = 74; });
    if (fault === 'pending') f.alterState(s => { s.snapshot.hasPending = true; });
    if (fault === 'removed-and-restored') {
      // A same-object reattachment must not erase the intervening removal.
      f.notifyMutation([{ removedNodes: [f.body] }]);
    }
    if (fault === 'rejected') entrance.reject(new Error('PRIVATE_ANIMATION_ERROR'));
    else { entrance.finish(fault !== 'permanent-opacity'); if (fault === 'permanent-blur') entrance.row.style.filter = 'blur(1px)'; }
    const result = await pending; assert.equal(result.status, 'UNSAFE');
    assert.doesNotMatch(JSON.stringify(result.diagnostic), /PRIVATE|className|animationName/);
  });
}

for (const fault of ['static', 'unknown-name', 'paused', 'infinite', 'long-duration', 'wrong-target', 'two-animations', 'hidden-body', 'hidden-paragraph', 'transparent-child']) {
  test(`terminal does not await ${fault} transparency`, async () => {
    const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
    if (fault === 'static') entrance.row.getAnimations = () => [];
    if (fault === 'unknown-name') entrance.animation.animationName = 'unknown';
    if (fault === 'paused') entrance.animation.playState = 'paused';
    if (fault === 'infinite') entrance.timing.iterations = Infinity;
    if (fault === 'long-duration') entrance.timing.duration = 4000;
    if (fault === 'wrong-target') entrance.animation.effect.target = f.root;
    if (fault === 'two-animations') entrance.row.getAnimations = () => [entrance.animation, entrance.animation];
    if (fault === 'hidden-body') f.body.style.visibility = 'hidden';
    if (fault === 'hidden-paragraph') f.paragraph.style.visibility = 'hidden';
    if (fault === 'transparent-child') f.paragraph.style.opacity = '0';
    assert.equal((await f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs())).status, 'UNSAFE');
    assert.equal(entrance.reads(), 0);
  });
}

for (const budget of [80, 10000]) test(`nonresolving finite entrance respects ${budget}ms remaining and the 5s read cap`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  let callback, delay, cleared = false;
  f.context.setTimeout = (fn, ms) => { callback = fn; delay = ms; return 77; };
  f.context.clearTimeout = id => { assert.equal(id, 77); cleared = true; };
  const deadline = Date.now() + budget;
  const pending = f.page.evaluate(readLiveTerminalRenderer, { ...terminalReadArgs(), deadline }); await flush();
  assert.equal(entrance.reads(), 1); assert.equal(typeof callback, 'function'); assert.ok(delay > 0 && delay <= Math.min(budget, 5000));
  f.context.Date.now = () => deadline; callback();
  assert.equal((await pending).status, 'UNSAFE'); assert.equal(cleared, true);
});

const presentationUpdates = {
  version: s => { s.snapshot.version += 1; },
  theme: s => { s.snapshot.theme = 'dark'; },
  animationLevel: s => { s.snapshot.animationLevel = 'minimal'; },
  contextLabel: s => { s.snapshot.contextLabel = 'updated synthetic label'; },
};
for (const field of [...Object.keys(presentationUpdates), 'all']) test(`entrance projection permits legal ${field} publication without freezing presentation metadata`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
  f.alterState(s => { for (const update of field === 'all' ? Object.values(presentationUpdates) : [presentationUpdates[field]]) update(s); });
  entrance.finish(); const result = await pending;
  assert.equal(result.status, 'READY'); assert.equal(result.content, SECRET); assert.equal(result.bridgeContent, SECRET);
});

for (const [field, value] of [['version', 0], ['version', -1], ['version', NaN], ['version', Infinity], ['version', '1'],
  ['theme', 'system'], ['animationLevel', 'arbitrary'], ['contextLabel', null], ['contextLabel', 'x'.repeat(401)]]) {
  test(`entrance projection rejects invalid fixed DTO ${field}=${String(value).slice(0, 12)} before waiting`, async () => {
    const f = renderer('owner'); await f.publish(state('idle', 73)); f.alterState(s => { s.snapshot[field] = value; });
    let entrance; entrance = messageEntrance(f, () => entrance.finish());
    assert.equal((await f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs())).status, 'UNSAFE');
    assert.equal(entrance.reads(), 0);
  });
}
for (const field of ['theme', 'animationLevel']) test(`entrance projection permits absent optional ${field}`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
  f.alterState(s => { delete s.snapshot[field]; }); entrance.finish();
  assert.equal((await pending).status, 'READY');
});

const terminalChanges = {
  connection: s => { s.connected = false; }, generation: s => { s.generation++; },
  conversation: s => { s.snapshot.conversationId++; }, taskState: s => { s.snapshot.taskState = 'running'; },
  loading: s => { s.snapshot.loading = true; }, hasPending: s => { s.snapshot.hasPending = true; },
  canSend: s => { s.snapshot.canSend = false; }, canStop: s => { s.snapshot.canStop = true; },
  stopping: s => { s.snapshot.stopping = true; }, error: s => { s.snapshot.error = 'PRIVATE_ERROR'; },
  stopMessage: s => { s.snapshot.stopMessage = 'PRIVATE_STOP'; }, aiConfigured: s => { s.snapshot.aiConfigured = false; },
  content: s => { s.snapshot.messages[0].content = 'PRIVATE_CHANGED_TEXT'; },
  role: s => { s.snapshot.messages[0].role = 'user'; }, duplicate: s => { s.snapshot.messages.push({ ...s.snapshot.messages[0] }); },
  userMessage: s => { s.snapshot.messages.push({ role: 'user', content: 'PRIVATE_NEW_USER_TEXT' }); },
};
for (const [field, change] of Object.entries(terminalChanges)) test(`entrance projection rejects bound terminal ${field} mutation`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
  f.alterState(change); entrance.finish(); const result = await pending;
  assert.equal(result.status, 'UNSAFE'); assert.doesNotMatch(JSON.stringify(result.diagnostic), /PRIVATE|content|contextLabel/);
});
for (const field of ['aiConfigured', 'userMessage', 'canSend']) test(`entrance projection rechecks ${field} changed during the final full renderer read`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  const getState = f.context.window.offerpilotDesktop.getState; let reads = 0;
  f.context.window.offerpilotDesktop.getState = async () => { if (++reads === 3) f.alterState(terminalChanges[field]); return getState(); };
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush(); entrance.finish();
  assert.equal((await pending).status, 'UNSAFE');
});
test('entrance projection rejects same-node DOM text mutation even when bridge text stays bound', async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
  f.body.textContent = 'PRIVATE_CHANGED_DOM_TEXT'; entrance.finish();
  assert.equal((await pending).status, 'UNSAFE');
});
for (const stage of ['after-animation', 'final-read']) test(`entrance projection rejects version rollback at ${stage}`, async () => {
  const f = renderer('owner'); const initial = state('idle', 73); initial.snapshot.version = 2; await f.publish(initial);
  const entrance = messageEntrance(f); const getState = f.context.window.offerpilotDesktop.getState; let reads = 0;
  f.context.window.offerpilotDesktop.getState = async () => {
    reads++; if (stage === 'final-read' && reads === 3) f.alterState(s => { s.snapshot.version = 2; });
    return getState();
  };
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
  f.alterState(s => { s.snapshot.version = stage === 'after-animation' ? 1 : 3; }); entrance.finish();
  assert.equal((await pending).status, 'UNSAFE');
});

for (const phase of ['running', 'loading', 'canSend']) test(`entrance projection leaves initial ${phase} pending and rereads before binding a terminal`, async () => {
  const f = renderer('owner'); const initial = state('idle', 73);
  if (phase === 'running') Object.assign(initial.snapshot, { taskState: 'running', loading: true, canSend: false, canStop: true });
  if (phase === 'loading') Object.assign(initial.snapshot, { taskState: 'running', loading: true, canSend: false, canStop: false });
  if (phase === 'canSend') initial.snapshot.canSend = false;
  await f.publish(initial); let entrance; entrance = messageEntrance(f, () => {
    f.alterState(s => { Object.assign(s.snapshot, { taskState: 'idle', loading: false, canSend: true, canStop: false }); }); entrance.finish();
  });
  const args = terminalReadArgs(), result = await f.page.evaluate(readLiveTerminalRenderer, args);
  assert.equal(result.status, 'NOT_READY'); assert.equal(result.diagnostic.reason, 'CONTROLS_PENDING'); assert.equal(entrance.reads(), 0);
  assert.equal(result.conversationId, 73); assert.equal(result.generation, 0);
  f.alterState(s => { Object.assign(s.snapshot, { taskState: 'idle', loading: false, canSend: true, canStop: false }); });
  entrance.finish(); assert.equal((await f.page.evaluate(readLiveTerminalRenderer, args)).status, 'READY');
});
for (const fault of ['inline-format', 'hidden-paragraph', 'duplicate-body']) test(`entrance projection pending controls do not conceal ${fault}`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); f.alterState(s => { s.snapshot.canSend = false; });
  let entrance; entrance = messageEntrance(f, () => entrance.finish());
  if (fault === 'inline-format') f.paragraph.childNodes = [{ ...f.paragraph, parentElement: f.paragraph, tagName: 'STRONG', childNodes: [] }];
  if (fault === 'hidden-paragraph') f.paragraph.style.visibility = 'hidden';
  if (fault === 'duplicate-body') f.hiddenLast();
  assert.equal((await f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs())).status, fault === 'inline-format' ? 'UNSUPPORTED_FORMAT' : 'UNSAFE');
  assert.equal(entrance.reads(), 0);
});
for (const mode of ['live', 'mock']) test(`entrance projection ${mode} orchestration rereads pending controls inside the original deadline with one request`, async t => {
  const fake = await harness(t, { deadline: 1000 }); let entrance, reads = 0;
  fake.transport.afterSend = () => { fake.owner.alterState(s => { s.snapshot.canSend = false; });
    entrance = messageEntrance(fake.owner, () => { fake.owner.alterState(s => { s.snapshot.canSend = true; s.snapshot.version++; }); entrance.finish(); }); };
  fake.transport.terminalHook = () => { if (++reads === 2) fake.owner.alterState(s => { s.snapshot.canSend = true; }); };
  const outcome = await (mode === 'live' ? fake.prove() : observePilotCompletion(mockObservationArgs(fake)));
  assert.equal(outcome.checks.terminalTextProven, true); assert.equal(reads, 2); assert.equal(fake.sends(), 1); assert.equal(entrance.reads(), 1);
});

test('entrance projection rejects version rollback after the final renderer read saw a newer publication', async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  const getState = f.context.window.offerpilotDesktop.getState; let reads = 0;
  f.context.window.offerpilotDesktop.getState = async () => {
    if (++reads === 3) f.alterState(s => { s.snapshot.version = 4; });
    if (reads === 4) f.alterState(s => { s.snapshot.version = 3; });
    return getState();
  };
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
  f.alterState(s => { s.snapshot.version = 2; }); entrance.finish();
  assert.equal((await pending).status, 'UNSAFE');
});

test('entrance projection contract uses the fixed published DTO and command lease revision', () => {
  const protocol = readPinnedSource('desktop/haru-protocol.cjs');
  const owner = readPinnedSource('web/src/features/assistantSurface/useDesktopHaruOwner.ts');
  assert.match(protocol, /Number\.isSafeInteger\(value.version\).*value.version < 1/);
  assert.match(protocol, /\['light', 'dark'\].includes\(value.theme\)/);
  assert.match(protocol, /\['full', 'minimal', 'off'\].includes\(value.animationLevel\)/);
  assert.match(protocol, /contextLabel: text\(value.contextLabel, 400\)/);
  assert.match(protocol, /messages: Array.isArray\(value.messages\).*slice\(-12\).*\['user', 'assistant'\].*text\(item.content, 12000\)/);
  assert.doesNotMatch(protocol.slice(protocol.indexOf('function sanitizeSnapshot'), protocol.indexOf('function validateRequest')), /updatedAt|updated_at/);
  assert.match(owner, /version: \(snapshotRef.current.snapshot\?\.version \?\? 0\) \+ \(changedControls \? 1 : 0\)/);
  assert.match(owner, /controller.executionControl.execution, controller.activeConversationSelectionRef.current/);
  assert.match(owner, /bridge\?\.publish\?\.\(snapshotRef.current.snapshot\)/);
});

test('entrance projection rejects a same-node characterData change restored before the final read', async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
  f.notifyMutation([{ type: 'characterData', target: f.paragraph.childNodes[0] }]); entrance.finish();
  assert.equal((await pending).status, 'UNSAFE');
});
for (const field of ['theme', 'contextLabel']) test(`entrance projection rejects invalid ${field} publication after the animation`, async () => {
  const f = renderer('owner'); await f.publish(state('idle', 73)); const entrance = messageEntrance(f);
  const pending = f.page.evaluate(readLiveTerminalRenderer, terminalReadArgs()); await flush();
  f.alterState(s => { s.snapshot[field] = field === 'theme' ? 'system' : null; }); entrance.finish();
  assert.equal((await pending).status, 'UNSAFE');
});
