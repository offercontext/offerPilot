import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
import { Ledger } from '../broker-core.cjs';
import { installMirrorObservation, removeMirrorObservation } from '../mirror-observer.mjs';
import { CONTINUATION_CODES, proveMockTerminalContinuation } from '../mock-continuation.mjs';

// Local, synthetic renderer worlds execute the actual observer and terminal
// readers. These mechanism tests are not Windows/EXE or provider evidence.
const SECRET = 'PRIVATE_SYNTHETIC_TERMINAL_CONTENT';
const flush = async () => { for (let n = 0; n < 30; n += 1) await Promise.resolve(); };
const state = (taskState = 'idle', conversationId = null) => ({ connected: true, generation: 0,
  snapshot: { taskState, conversationId, loading: taskState === 'running', hasPending: false,
    stopping: false, canStop: taskState === 'running', canSend: taskState === 'idle', error: '', stopMessage: '',
    messages: conversationId ? [{ role: 'assistant', content: SECRET }] : [] } });
function renderer(role) {
  let current = state(), nextTimer = 0, hook;
  const listeners = new Set(), mutations = new Set(), timers = new Set(), calls = [];
  const makeNode = text => ({ isConnected: true, hidden: false, parentElement: null,
    textContent: text, style: { display: 'block', visibility: 'visible', opacity: '1' },
    getClientRects: () => [{ width: 100, height: 40 }], getAttribute: () => null, disabled: false });
  const root = makeNode(''), body = makeNode(SECRET), running = makeNode('正在处理');
  body.parentElement = root;
  let structured = false, secondBody;
  root.querySelectorAll = selector => {
    if (selector === 'article[aria-label^="本轮任务："]') return structured ? [makeNode('')] : [];
    const expected = role === 'owner' ? '[class*="bubbleAssistant"]' : '.desktop-haru-messages article[data-role="assistant"] p';
    return selector === expected ? [body, ...(secondBody ? [secondBody] : [])] : [];
  };
  const schedule = () => { const id = ++nextTimer; timers.add(id); return id; };
  class RendererDate extends Date { static now() { return Date.now(); } }
  const context = vm.createContext({ Date: RendererDate,
    setTimeout: schedule, clearTimeout: id => timers.delete(id), setInterval: schedule, clearInterval: id => timers.delete(id),
    getComputedStyle: node => node.style,
    MutationObserver: class { constructor(fn) { this.fn = fn; } observe() { mutations.add(this.fn); } disconnect() { mutations.delete(this.fn); } },
    document: { visibilityState: 'visible', documentElement: {}, querySelectorAll(selector) {
      const rootSelector = role === 'owner' ? '[data-onboarding-target="pilot"]' : 'main[aria-label="Haru 桌面小窗"]';
      if (selector === rootSelector) return [root];
      const runningSelector = role === 'owner'
        ? '[data-onboarding-target="pilot"] button[aria-label="停止当前回复"]'
        : 'main[aria-label="Haru 桌面小窗"] .desktop-haru-avatar [role="status"]';
      return selector === runningSelector && current.snapshot.taskState === 'running' ? [running] : [];
    } },
    window: { offerpilotDesktop: { role, getState: async () => structuredClone(current),
      onState(fn) { listeners.add(fn); return () => listeners.delete(fn); } } },
  });
  const page = { async evaluate(fn, arg) {
    calls.push(arg?.operation ?? 'terminal');
    await hook?.(arg);
    context.__argument = structuredClone(arg);
    return vm.runInContext(`(${fn.toString()})(__argument)`, context);
  } };
  return { page, context, root, body, calls,
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
function ledger() {
  const book = new Ledger();
  for (const caseId of ['connection', 'pilot-stream']) {
    book.armCase(caseId);
    const ticket = book.claim(); book.reserve(ticket, ticket.maxTokens, 'UNCHANGED');
    book.markTransport(ticket, 'outboundStarted'); book.markTransport(ticket, 'upstreamResponded');
    book.finish(ticket, 'SETTLED', { prompt_tokens: 20, completion_tokens: 3,
      prompt_cache_hit_tokens: 0, prompt_cache_miss_tokens: 20, total_tokens: 23 });
  }
  return { ...book.snapshot(), journalFailed: false, mode: 'MOCK', mock: {
    transport: 'IN_PROCESS_SYNTHETIC_HTTPS', externalNetwork: 'DENIED', fakeRequests: 2,
    realProviderCalls: 0, usageIsSynthetic: true } };
}
async function harness(t, { install = true, seenRunning = true } = {}) {
  const owner = renderer('owner'), haru = renderer('haru');
  t.after(() => removeMirrorObservation(owner.page, haru.page));
  if (install) {
    await installMirrorObservation(owner.page, haru.page, 'pilot-stream');
    if (seenRunning) await Promise.all([owner.publish(state('running', 73)), haru.publish(state('running', 73))]);
    await Promise.all([owner.publish(state('idle', 73)), haru.publish(state('idle', 73))]);
  }
  const snapshot = ledger(); let reads = 0;
  const broker = { mode: 'MOCK', snapshot() { reads += 1; return structuredClone(snapshot); },
    armCase() { throw new Error('mutation forbidden'); }, cancelCase() { throw new Error('mutation forbidden'); },
    prepareCase() { throw new Error('mutation forbidden'); } };
  const args = { page: owner.page, haru: haru.page, broker, previousCount: 1, deadlineMs: Date.now() + 60_000,
    result: { id: 'pilot-stream', status: 'FAIL', code: 'STREAM_NOT_OBSERVED', diagnostic: { stage: 'PILOT_STREAM_READBACK' } } };
  return { owner, haru, snapshot, args, reads: () => reads, prove: () => proveMockTerminalContinuation(args) };
}
const expectBlocked = async (fake, code) => {
  const value = await fake.prove();
  assert.equal(value.status, 'BLOCKED'); if (code) assert.equal(value.code, code);
  assert.equal(value.noNewRequests, false);
  assert.ok(CONTINUATION_CODES.includes(value.code));
  assert.doesNotMatch(JSON.stringify(value), /PRIVATE_|73|conversationId|generation|bridgeContent/);
};

test('proves only real two-window visible terminal text and existing new-ID observer evidence', async t => {
  const fake = await harness(t);
  fake.owner.body.textContent = ` \n${SECRET}\t\n`;
  const resultBefore = structuredClone(fake.args.result);
  const proof = await fake.prove();
  assert.deepEqual(proof, { status: 'PROVEN', code: 'MOCK_CONTINUATION_PROVEN', eligible: true,
    ledgerSafe: true, mirrorProven: true, domEqual: true, noNewRequests: true });
  assert.equal(Object.isFrozen(proof), true);
  assert.deepEqual(fake.args.result, resultBefore, 'terminal proof must never rewrite the original FAIL');
  assert.equal(fake.reads(), 2);
  assert.equal(fake.owner.calls.filter(call => call === 'terminal').length, 1);
  assert.equal(fake.haru.calls.filter(call => call === 'terminal').length, 1);
});

for (const [key, value] of [['id', 'pilot-hitl-reject'], ['status', 'PASS'], ['code', 'UI_TIMEOUT'], ['stage', 'PILOT_RUNNING']]) {
  test(`wrong ${key} cannot enable continuation`, async t => {
    const fake = await harness(t);
    if (key === 'stage') fake.args.result.diagnostic.stage = value; else fake.args.result[key] = value;
    await expectBlocked(fake, 'NOT_ELIGIBLE'); assert.equal(fake.reads(), 0);
  });
}
for (const change of [f => { f.args.broker.mode = 'live'; }, f => { f.snapshot.mode = 'live'; },
  f => { f.snapshot.mock.realProviderCalls = 1; }, f => { f.snapshot.mock.externalNetwork = 'ALLOWED'; },
  f => { f.snapshot.mock.usageIsSynthetic = false; }, f => { f.snapshot.mock.fakeRequests = 3; }]) {
  test('missing or altered offline boundary cannot enable continuation', async t => {
    const fake = await harness(t); change(fake); await expectBlocked(fake, 'MOCK_BOUNDARY_REQUIRED');
  });
}
for (const [name, change] of [
  ['active', f => { f.snapshot.active = true; }], ['closed', f => { f.snapshot.closed = true; }],
  ['journal failure', f => { f.snapshot.journalFailed = true; }], ['missing journal state', f => { delete f.snapshot.journalFailed; }],
  ['denied', f => { f.snapshot.denied.UNARMED = 1; }], ['missing denied fields', f => { f.snapshot.denied = {}; }],
  ['unknown denial', f => { f.snapshot.denied.UNKNOWN = 0; }],
  ['extra row', f => { f.snapshot.requests.push(structuredClone(f.snapshot.requests[1])); f.snapshot.sentRequests += 1; f.snapshot.mock.fakeRequests += 1; }],
  ['unknown ledger case', f => { f.snapshot.requests[0].caseId = 'UNKNOWN'; }],
  ['future case row', f => { f.snapshot.requests[0].caseId = 'pilot-hitl-reject'; }],
  ['duplicate case', f => { f.snapshot.requests[0].caseId = 'pilot-stream'; }],
  ['outbound false', f => { f.snapshot.requests[1].outboundStarted = false; }],
  ['response false', f => { f.snapshot.requests[1].upstreamResponded = false; }],
  ['unsettled usage', f => { f.snapshot.requests[1].status = 'USAGE'; }],
  ['unknown status', f => { f.snapshot.requests[1].status = 'UNKNOWN'; }],
  ['untrusted usage', f => { delete f.snapshot.requests[1].completionTokens; }],
  ['inconsistent usage', f => { f.snapshot.requests[1].cacheMissTokens += 1; }],
  ['unknown row fields', f => { f.snapshot.requests[1].unknown = SECRET; }],
  ['retained reserve', f => { f.snapshot.retainedMicroCny = 3_000_000; }],
  ['budget altered', f => { f.snapshot.budgetMicroCny = 11_000_000; }],
  ['spent mismatch', f => { f.snapshot.settledMicroCny += 1; }],
  ['wrong prior count', f => { f.args.previousCount = 0; }],
  ['request count above cap', f => { f.snapshot.sentRequests = 9; f.snapshot.mock.fakeRequests = 9; }],
]) test(`${name} blocks before renderer terminal reads`, async t => {
  const fake = await harness(t); change(fake); await expectBlocked(fake, 'LEDGER_UNSAFE');
  assert.equal(fake.owner.calls.includes('terminal'), false);
});

test('matching Haru DOM and shared bridge cannot substitute for different real owner text', async t => {
  const fake = await harness(t); fake.owner.body.textContent = 'OTHER_PRIVATE_TEXT';
  await expectBlocked(fake, 'DOM_MISMATCH');
});
for (const [name, change] of [
  ['document hidden', f => { f.owner.context.document.visibilityState = 'hidden'; }],
  ['CSS hidden', f => { f.haru.body.style.visibility = 'hidden'; }],
  ['ancestor transparent', f => { f.owner.root.style.opacity = '0'; }],
  ['ancestor display none', f => { f.owner.root.style.display = 'none'; }],
  ['zero rectangle', f => { f.owner.body.getClientRects = () => [{ width: 0, height: 0 }]; }],
  ['detached DOM', f => { f.haru.body.isConnected = false; }],
  ['hidden final assistant', f => { f.haru.hiddenLast(); }],
  ['structured card', f => { f.owner.setStructured(true); }],
  ['bridge error', f => { f.haru.alterState(s => { s.snapshot.error = SECRET; }); }],
  ['stop message', f => { f.owner.alterState(s => { s.snapshot.stopMessage = SECRET; }); }],
  ['stopping', f => { f.owner.alterState(s => { s.snapshot.stopping = true; }); }],
  ['send unavailable', f => { f.haru.alterState(s => { s.snapshot.canSend = false; }); }],
]) test(`${name} is never terminal proof`, async t => {
  const fake = await harness(t); change(fake); await expectBlocked(fake, 'TERMINAL_UNPROVEN');
});
for (const [name, change] of [
  ['no positive ID', s => { s.snapshot.conversationId = null; }],
  ['changed ID', s => { s.snapshot.conversationId = 74; }],
  ['pending HITL', s => { s.snapshot.hasPending = true; s.snapshot.taskState = 'waiting_confirmation'; }],
  ['loading', s => { s.snapshot.loading = true; }],
  ['generation changed', s => { s.generation += 1; }],
  ['disconnected', s => { s.connected = false; }],
]) test(`${name} invalidates observer proof`, async t => {
  const fake = await harness(t); fake.owner.alterState(change); await expectBlocked(fake, 'MIRROR_UNPROVEN');
});

test('absence of the installed original null-baseline observer cannot be reconstructed', async t => {
  const fake = await harness(t, { install: false });
  await Promise.all([fake.owner.publish(state('idle', 73)), fake.haru.publish(state('idle', 73))]);
  await expectBlocked(fake, 'MIRROR_UNPROVEN');
});
test('current idle DOM cannot replace the original positive-ID running observation', async t => {
  const fake = await harness(t, { seenRunning: false }); await expectBlocked(fake, 'MIRROR_UNPROVEN');
});

test('historical unexpected approval remains unsafe even after matching idle terminal text', async t => {
  const fake = await harness(t);
  const pending = state('waiting_confirmation', 73); pending.snapshot.hasPending = true;
  await Promise.all([fake.owner.publish(pending), fake.haru.publish(pending)]);
  await Promise.all([fake.owner.publish(state('idle', 73)), fake.haru.publish(state('idle', 73))]);
  await expectBlocked(fake, 'MIRROR_UNPROVEN');
});

for (const [name, change] of [
  ['late denial', f => { f.snapshot.denied.UNARMED = 1; }],
  ['late request', f => { f.snapshot.requests.push(structuredClone(f.snapshot.requests[1])); f.snapshot.sentRequests += 1; f.snapshot.mock.fakeRequests += 1; }],
  ['late active', f => { f.snapshot.active = true; }],
  ['late ledger mutation', f => { f.snapshot.requests[1].envelope = 'TIGHTENED'; }],
]) test(`${name} during DOM read is caught after the read`, async t => {
  const fake = await harness(t);
  fake.owner.onEvaluate(arg => { if (!arg?.operation) change(fake); });
  await expectBlocked(fake, 'LEDGER_CHANGED'); assert.equal(fake.reads(), 2);
});

test('identity change during DOM reading is caught by fresh observer read', async t => {
  const fake = await harness(t);
  fake.owner.onEvaluate(arg => { if (!arg?.operation) fake.haru.alterState(s => { s.snapshot.conversationId = 74; }); });
  await expectBlocked(fake);
});
for (const operation of ['read', 'terminal']) test(`${operation} rejection blocks without raw error or retries`, async t => {
  const fake = await harness(t); let attempts = 0;
  fake.owner.onEvaluate(arg => { if ((arg?.operation ?? 'terminal') === operation) { attempts += 1; throw new Error(SECRET); } });
  await expectBlocked(fake, 'READ_FAILED'); assert.equal(attempts, 1);
});

test('expired suite never reads broker or renderers', async t => {
  const fake = await harness(t); fake.args.deadlineMs = Date.now() - 1;
  await expectBlocked(fake, 'DEADLINE'); assert.equal(fake.reads(), 0);
});
for (const suiteRemaining of [50, 60_000]) test(`all reads share one deadline capped at ${Math.min(suiteRemaining, 15000)}ms`, async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const fake = await harness(t); fake.args.deadlineMs = Date.now() + suiteRemaining;
  let terminalReads = 0;
  fake.owner.onEvaluate(arg => { if (!arg?.operation) { terminalReads += 1; return new Promise(() => {}); } });
  const pending = fake.prove(); await flush();
  assert.equal(terminalReads, 1);
  t.mock.timers.tick(Math.min(suiteRemaining, 15000) - 1); await flush();
  let settled = false; pending.then(() => { settled = true; }); await flush(); assert.equal(settled, false);
  t.mock.timers.tick(1); await flush();
  const result = await pending; assert.equal(result.code, 'DEADLINE'); assert.equal(result.status, 'BLOCKED');
  assert.equal(terminalReads, 1, 'timeout cannot cause provider/UI retry');
});

test('time consumed by the first observer read is not reset for DOM verification', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const fake = await harness(t); fake.args.deadlineMs = Date.now() + 50;
  let firstRead = true, terminalReads = 0;
  fake.owner.onEvaluate(arg => {
    if (arg?.operation === 'read' && firstRead) {
      firstRead = false; return new Promise(resolve => setTimeout(resolve, 40));
    }
    if (!arg?.operation) { terminalReads += 1; return new Promise(() => {}); }
  });
  const pending = fake.prove(); await flush(); assert.equal(terminalReads, 0);
  t.mock.timers.tick(40); await flush(); assert.equal(terminalReads, 1);
  let settled = false; pending.then(() => { settled = true; });
  t.mock.timers.tick(9); await flush(); assert.equal(settled, false);
  t.mock.timers.tick(1); await flush();
  assert.equal((await pending).code, 'DEADLINE');
});

test('implementation remains read-only and contains no configurable provider or artifact transport', async () => {
  const source = await fs.readFile(new URL('../mock-continuation.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\.\s*(?:armCase|prepareCase|cancelCase|request|publish|screenshot|fetch|route|writeFile|appendFile)\s*\(/u);
  assert.doesNotMatch(source, /process\.env|https?:\/\/|providerKey|console\./u);
});
