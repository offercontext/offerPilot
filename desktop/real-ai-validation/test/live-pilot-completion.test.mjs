import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { Ledger } from '../broker-core.cjs';
import { PIN, CASES } from '../contract.mjs';
import { safeResults, saveEvidence } from '../safe-evidence.mjs';
import { installMirrorObservation, removeMirrorObservation } from '../mirror-observer.mjs';
import { compactPlainMessage, normalizePlainText, projectAdmission, projectTerminal, readLivePilotJson,
  observeLiveAdmission, isLiveLedgerProven, proveLivePilotCompletion } from '../live-pilot-completion.mjs';
// Offline synthetic mechanism tests. No EXE, real provider or release claim.
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
    textContent: text, innerText: text, style: { display: 'block', visibility: 'visible', opacity: '1' },
    getClientRects: () => [{ width: 100, height: 40 }], getAttribute: () => null, disabled: false });
  const root = makeNode(''), body = makeNode(SECRET), running = makeNode('正在处理');
  body.parentElement = root;
  let structured = false, secondBody, duplicateRoot = false;
  root.querySelectorAll = selector => {
    if (selector.startsWith('article[aria-label^="本轮任务："]')) return structured ? [makeNode('')] : [];
    const expected = role === 'owner' ? '[class*="bubbleAssistant"]' : '.desktop-haru-messages article[data-role="assistant"] p';
    return selector === expected ? [body, ...(secondBody ? [secondBody] : [])] : [];
  };
  const schedule = () => { const id = ++nextTimer; timers.add(id); return id; };
  class RendererDate extends Date { static now() { return Date.now(); } }
  const context = vm.createContext({ Date: RendererDate, TextDecoder, AbortController, location: { origin: 'http://127.0.0.1:41000' },
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
  return { page, context, root, body, calls, responses, frame,
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
    execution_generation: ID.execution_generation, type: 'message', message: SECRET } } });
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
async function harness(t, { running = 'none', growth = 0, content = SECRET, deadline = 3000, initial } = {}) {
  const owner = renderer('owner'), haru = renderer('haru');
  const prior = ledger(false), final = ledger(); let snapshot = prior, sends = 0, status = terminal(), history = [];
  status.terminal.response.message = content;
  const transport = { historyOptions: {}, statusOptions: {}, admissionOptions: {}, afterSend: null, terminalHook: null };
  function reply(value, options = {}) {
    const bytes = new TextEncoder().encode(typeof value === 'string' ? value : JSON.stringify(value)); let consumed = false;
    return { status: options.status ?? 200, redirected: options.redirected ?? false,
      url: options.url, body: { getReader() { return { async read() { if (options.never) return new Promise(() => {});
        if (consumed) return { done: true }; consumed = true; return { value: bytes, done: false }; }, async cancel() {} }; } } };
  }
  owner.context.fetch = async (route, options) => {
    assert.equal(options.method, 'GET'); assert.equal(options.credentials, 'same-origin'); assert.equal(options.redirect, 'error');
    assert.ok(route === '/api/chat/conversations?include_archived=true' || route === `/api/pilot/runtime/v1/requests/${ID.request_id}`);
    const config = route.includes('conversations') ? transport.historyOptions : transport.statusOptions;
    return reply(route.includes('conversations') ? history : status, { url: `http://127.0.0.1:41000${route}`, ...config });
  };
  owner.context.window.__offerpilotBoundedUiObserver = { summary: () => ({ installed: true, originalTargetConnected: true,
    originalTargetCurrent: true, currentPilotUnique: true, targetReplacementObserved: false, readFailed: false,
    originalGrowthWithStopCount: growth }) };
  t.after(() => removeMirrorObservation(owner.page, haru.page));
  if (initial) await Promise.all([owner.publish(initial), haru.publish(initial)]);
  await installMirrorObservation(owner.page, haru.page, 'pilot-stream');
  const emit = (value = admission(), options = {}) => {
    const response = { request: () => ({ method: () => options.method ?? 'POST', frame: () => options.frame ?? owner.frame,
      redirectedFrom: () => options.redirectedFrom ?? null,
      headers: () => assert.fail('headers forbidden'), postData: () => assert.fail('postData forbidden') }),
      url: () => options.url ?? 'http://127.0.0.1:41000/api/pilot/runtime/v1/turns', status: () => options.status ?? 202,
      body: async () => options.body ?? Buffer.from(JSON.stringify(value)) };
    for (const listener of [...owner.responses]) listener(response);
  };
  const broker = { snapshot: () => structuredClone(snapshot), cancelCase: () => assert.fail('proof cannot cancel'),
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
  const fake = await harness(t); fake.transport.afterSend = () => change(fake); await blocked(fake);
});
test('rendered Markdown is unproven rather than regex-repaired', async t => {
  const fake = await harness(t, { content: '**hello**' }); fake.transport.afterSend = () => { fake.owner.body.innerText = 'hello'; }; await blocked(fake);
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
  const fake = await harness(t); fake.transport.admissionOptions = { body: Buffer.alloc(65537, 32) }; await blocked(fake);
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
test('timeout cannot be revived by late admission body', async t => {
  const fake = await harness(t, { deadline: 80 }); let release;
  fake.transport.admissionOptions.body = new Promise(resolve => { release = resolve; });
  await blocked(fake); release(Buffer.from(JSON.stringify(admission()))); await flush();
  assert.equal(fake.owner.responses.size, 0); assert.equal(fake.sends(), 1);
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
  const source = path => execFileSync('git', ['show', `${PIN.commit}:${path}`], { encoding: 'utf8' });
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
  assert.doesNotMatch(helper, /\.postData\(|\.headers\(|request_id\s*:/);
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
  const response = { url: () => `${origin}/api/pilot/runtime/v1/turns`, request: () => ({ method: () => 'POST', frame: () => frame, redirectedFrom: () => null }) };
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
  return { ...book.snapshot(), journalFailed: false, provenance: { productCommit: PIN.commit, buildRunId: String(PIN.runId),
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
