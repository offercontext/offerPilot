import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
import { installMirrorObservation, readMirrorObservation, removeMirrorObservation, isRunningMirrorProven } from '../mirror-observer.mjs';

// Execute the actual serialized observer in separate VM renderer worlds. These
// are offline mechanism tests, not EXE, browser, provider, or release evidence.
const KEY = '__offerpilotBoundedMirrorObserver';
const state = (taskState = 'idle', conversationId = null, extra = {}) => ({ connected: true, generation: 0,
  snapshot: { taskState, conversationId, loading: taskState === 'running', hasPending: taskState === 'waiting_confirmation' }, ...extra });
const flush = async () => { for (let index = 0; index < 16; index += 1) await Promise.resolve(); };
function renderer(role, initial = state()) {
  let now = Date.now(), nextTimer = 0, current = initial, runningDom = false, enabled = true, shown = true;
  let nextRead, installGate, reads = 0;
  const timers = new Map(), listeners = new Set(), mutations = new Set();
  const schedule = (callback, ms, interval = false) => { const id = ++nextTimer; timers.set(id, { callback, due: now + ms, ms, interval }); return id; };
  class FakeDate extends Date { static now() { return now; } }
  const node = { disabled: false, textContent: '正在处理', getClientRects: () => shown ? [{}] : [], getAttribute: () => null };
  const context = vm.createContext({ Date: FakeDate, console,
    setTimeout: (callback, ms) => schedule(callback, ms), clearTimeout: id => timers.delete(id),
    setInterval: (callback, ms) => schedule(callback, ms, true), clearInterval: id => timers.delete(id),
    getComputedStyle: () => ({ display: 'block', visibility: 'visible' }),
    MutationObserver: class {
      constructor(callback) { this.callback = callback; }
      observe() { mutations.add(this.callback); }
      disconnect() { mutations.delete(this.callback); }
    },
    document: { documentElement: {}, visibilityState: 'visible', querySelectorAll(selector) {
      const matchesRole = role === 'owner' ? selector.includes('data-onboarding-target') : selector.includes('desktop-haru-avatar');
      node.disabled = !enabled;
      return matchesRole && runningDom ? [node] : [];
    } },
    window: { offerpilotDesktop: { role, getState: async () => {
      reads += 1;
      if (nextRead) { const read = nextRead; nextRead = undefined; return read; }
      return structuredClone(current);
    }, onState: callback => { listeners.add(callback); return () => listeners.delete(callback); } } },
  });
  const page = { evaluate: async (fn, argument) => {
    if (argument?.operation === 'install' && installGate) { const gate = installGate; installGate = undefined; await gate; }
    context.__argument = structuredClone(argument);
    return vm.runInContext(`(${fn.toString()})(__argument)`, context);
  } };
  return { page, context,
    async tick(ms = 50) {
      now += ms;
      const due = [...timers.entries()].filter(([, timer]) => timer.due <= now);
      for (const [id, timer] of due) {
        if (!timers.has(id)) continue;
        if (timer.interval) timer.due = now + timer.ms; else timers.delete(id);
        timer.callback(); await flush();
      }
      await flush();
    },
    async publish(value, { dom = runningDom, stopEnabled = true, visible = true, event = true } = {}) {
      current = structuredClone(value); runningDom = dom; enabled = stopEnabled; shown = visible;
      if (event && role === 'haru') for (const callback of [...listeners]) callback(structuredClone(value));
      for (const callback of [...mutations]) callback();
      await flush();
    },
    holdNextInstall() { let release; installGate = new Promise(resolve => { release = resolve; }); return release; },
    deferNextRead() { let resolve, reject; nextRead = new Promise((yes, no) => { resolve = yes; reject = no; }); return { resolve, reject }; },
    rawSummary() { return context.window[KEY]?.summary(); },
    resources() { return { timers: timers.size, listeners: listeners.size, mutations: mutations.size }; },
    reads: () => reads,
  };
}
function pair(t, initialOwner, initialHaru) {
  const owner = renderer('owner', initialOwner), haru = renderer('haru', initialHaru);
  t.after(async () => { await removeMirrorObservation(owner.page, haru.page); });
  return { owner, haru, install: caseId => installMirrorObservation(owner.page, haru.page, caseId ?? 'pilot-stream'),
    read: () => readMirrorObservation(owner.page, haru.page), remove: () => removeMirrorObservation(owner.page, haru.page),
    publish: async (value, options) => { await Promise.all([owner.publish(value, options), haru.publish(value, options)]); },
    tick: async ms => { await Promise.all([owner.tick(ms), haru.tick(ms)]); } };
}

test('preinstalled real event and DOM observers retain positive-ID running after it ends', async t => {
  const p = pair(t); await p.install();
  await p.publish(state('running', 7), { dom: true });
  await p.publish(state('idle', 7), { dom: false });
  const observed = await p.read();
  assert.equal(observed.owner.currentTaskState, 'idle');
  assert.equal(observed.haru.currentTaskState, 'idle');
  assert.equal(isRunningMirrorProven(observed), true);
  // The old late poll sees only idle, so cannot establish that genuine interval.
  assert.equal([observed.owner, observed.haru].every(value => value.currentTaskState === 'running'), false);
});

test('positive-ID running during owner polling, with Haru push, is independently recorded', async t => {
  const p = pair(t); await p.install();
  await p.owner.publish(state('running', 7), { dom: false });
  await p.haru.publish(state('running', 7), { dom: true });
  await p.owner.publish(state('running', 7), { dom: true });
  await p.tick();
  assert.equal(isRunningMirrorProven(await p.read()), true);
  assert.equal(p.owner.resources().listeners, 0, 'owner receives no Haru broadcast');
  assert.equal(p.haru.resources().listeners, 1);
});

for (const kind of ['bridge-only', 'dom-only', 'idle-only', 'null-only', 'disabled-owner', 'hidden-haru']) {
  test(`${kind} cannot certify running`, async t => {
    const p = pair(t); await p.install();
    if (kind === 'bridge-only') await p.publish(state('running', 7), { dom: false });
    if (kind === 'dom-only') await p.publish(state('idle', 7), { dom: true });
    if (kind === 'idle-only') await p.publish(state('idle', 7), { dom: false });
    if (kind === 'null-only') {
      await p.publish(state('running', null), { dom: true });
      await p.publish(state('idle', 7), { dom: false });
    }
    if (kind === 'disabled-owner') {
      await p.owner.publish(state('running', 7), { dom: true, stopEnabled: false });
      await p.haru.publish(state('running', 7), { dom: true });
    }
    if (kind === 'hidden-haru') {
      await p.owner.publish(state('running', 7), { dom: true });
      await p.haru.publish(state('running', 7), { dom: true, visible: false });
    }
    assert.equal(isRunningMirrorProven(await p.read()), false);
  });
}

test('nullable initial running is diagnostic only; a later real positive-ID running is required', async t => {
  const p = pair(t); await p.install();
  await p.publish(state('running', null), { dom: true });
  let observed = await p.read();
  assert.equal(observed.owner.runningWithNullObserved, true);
  assert.equal(observed.haru.runningWithNullObserved, true);
  assert.equal(isRunningMirrorProven(observed), false);
  await p.publish(state('running', 7), { dom: true });
  observed = await p.read();
  assert.equal(isRunningMirrorProven(observed), true);
});

test('different positive IDs across surfaces fail, even when both render running', async t => {
  const p = pair(t); await p.install();
  await p.owner.publish(state('running', 7), { dom: true });
  await p.haru.publish(state('running', 8), { dom: true });
  assert.equal(isRunningMirrorProven(await p.read()), false);
});

for (const terminalId of [8, null]) {
  test(`a later conversation ID ${terminalId} invalidates previously proven running`, async t => {
    const p = pair(t); await p.install();
    await p.publish(state('running', 7), { dom: true });
    assert.equal(isRunningMirrorProven(await p.read()), true);
    await p.publish(state('idle', terminalId), { dom: false });
    const observed = await p.read();
    assert.equal(observed.owner.identityChanged, true);
    assert.equal(observed.haru.identityChanged, true);
    assert.equal(isRunningMirrorProven(observed), false);
    assert.equal(p.owner.resources().timers, 0);
  });
}

test('fresh baseline waits for the newly opened idle/null draft, without adopting the old case', async t => {
  const p = pair(t, state('idle', 42), state('idle', 42));
  const installing = p.install(); await flush();
  await p.publish(state(), { dom: false });
  await p.tick(50); await installing;
  assert.equal(isRunningMirrorProven(await p.read()), false);
  await p.publish(state('running', 7), { dom: true });
  assert.equal(isRunningMirrorProven(await p.read()), true);
});

test('stale positive baseline times out and unregisters both surfaces', async t => {
  const p = pair(t, state('idle', 42), state('idle', 42));
  const installing = p.install(); const rejected = assert.rejects(installing, { code: 'HARU_SYNC_FAILED' });
  await flush(); await p.tick(5001); await rejected;
  assert.deepEqual(p.owner.resources(), { timers: 0, listeners: 0, mutations: 0 });
  assert.deepEqual(p.haru.resources(), { timers: 0, listeners: 0, mutations: 0 });
});

test('duplicate installation rejects and preserves the existing case until explicit cleanup', async t => {
  const p = pair(t); await p.install();
  await assert.rejects(p.install('pilot-cancel'), { code: 'HARU_SYNC_FAILED' });
  assert.equal((await p.read()).owner.caseId, 'pilot-stream');
  await p.remove(); await p.install('pilot-cancel');
  assert.equal((await p.read()).owner.caseId, 'pilot-cancel');
  assert.equal(isRunningMirrorProven(await p.read()), false);
});

test('case/token replacement and cross-pair read cannot inherit another case evidence', async t => {
  const a = pair(t), b = pair(t); await a.install(); await b.install('pilot-cancel');
  await a.publish(state('running', 7), { dom: true });
  assert.equal(isRunningMirrorProven(await readMirrorObservation(a.owner.page, b.haru.page)), false);
  const record = a.owner.context.window[KEY];
  record.token = 'stale-observer';
  assert.equal(isRunningMirrorProven(await a.read()), false);
  record.dispose(); delete a.owner.context.window[KEY];
});

test('cleanup is idempotent and pending IPC completion cannot recreate or mutate disposed evidence', async t => {
  const p = pair(t); await p.install();
  const late = p.owner.deferNextRead();
  await p.tick(50);
  const prior = p.owner.context.window[KEY];
  await p.remove(); await p.remove();
  late.resolve(state('running', 7)); await flush();
  assert.equal(p.owner.context.window[KEY], undefined);
  assert.equal(prior.summary().bridgeRunningObserved, false);
  assert.equal(prior.summary().installed, false);
  assert.deepEqual(p.owner.resources(), { timers: 0, listeners: 0, mutations: 0 });
  assert.deepEqual(p.haru.resources(), { timers: 0, listeners: 0, mutations: 0 });
  assert.equal(isRunningMirrorProven(await p.read()), false);
});

test('getState timeout faults the observer in five seconds and ignores late success', async t => {
  const p = pair(t); await p.install();
  const late = p.owner.deferNextRead();
  await p.owner.tick(50); await p.owner.tick(5000);
  late.resolve(state('running', 7)); await flush();
  const observed = await p.read();
  assert.equal(observed.owner.readTimedOut, true);
  assert.equal(observed.owner.healthy, false);
  assert.equal(isRunningMirrorProven(observed), false);
  assert.deepEqual(p.owner.resources(), { timers: 0, listeners: 0, mutations: 0 });
});

test('new Haru push wins over an older in-flight getState result', async t => {
  const p = pair(t); await p.install();
  const older = p.haru.deferNextRead(); await p.haru.tick(50);
  await p.haru.publish(state('running', 7), { dom: true });
  older.resolve(state()); await flush();
  assert.equal(p.haru.rawSummary().runningConversationId, 7);
  assert.equal(p.haru.rawSummary().identityChanged, false);
});

for (const failure of ['disconnect', 'generation', 'failed', 'malformed-id', 'malformed-state']) {
  test(`${failure} fails closed after real running`, async t => {
    const p = pair(t); await p.install();
    await p.publish(state('running', 7), { dom: true });
    let invalid = failure === 'disconnect' ? state('idle', 7, { connected: false })
      : failure === 'generation' ? state('idle', 7, { generation: 1 })
      : failure === 'failed' ? state('failed', 7)
      : failure === 'malformed-id' ? state('idle', '7') : state('private-untrusted-label', 7);
    await p.publish(invalid, { dom: false });
    assert.equal(isRunningMirrorProven(await p.read()), false);
  });
}

test('observer expires at the enclosing suite deadline even without caller cleanup', async t => {
  const p = pair(t); await p.install();
  await p.tick(600000);
  assert.equal((await p.read()).owner.expired, true);
  assert.deepEqual(p.owner.resources(), { timers: 0, listeners: 0, mutations: 0 });
  assert.deepEqual(p.haru.resources(), { timers: 0, listeners: 0, mutations: 0 });
});

test('only fixed fields leave the renderer, never prose, errors, args, or bridge objects', async t => {
  const p = pair(t); await p.install();
  const noisy = state('running', 7);
  Object.assign(noisy.snapshot, { error: 'PRIVATE_ERROR', messages: [{ content: 'PRIVATE_PROSE' }], args: { key: 'PRIVATE_ARGUMENT' } });
  await p.publish(noisy, { dom: true });
  const observed = await p.read();
  assert.doesNotMatch(JSON.stringify(observed), /PRIVATE_|messages|args|snapshot|token/);
  const expected = ['role', 'caseId', 'installed', 'baselineReady', 'healthy', 'connected', 'currentTaskState', 'loading', 'hasPending',
    'bridgeRunningObserved', 'domRunningObserved', 'runningWithNullObserved', 'conversationId', 'runningConversationId',
    'identityChanged', 'generationChanged', 'readTimedOut', 'expired'].sort();
  assert.deepEqual(Object.keys(observed.owner).sort(), expected);
});

test('case IDs are a fixed whitelist and module contains no product-changing or transport bypass operations', async t => {
  const p = pair(t);
  await assert.rejects(p.install('PRIVATE_CASE'), { code: 'HARU_SYNC_FAILED' });
  const source = await fs.readFile(new URL('../mirror-observer.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\.publish\(|\.request\(|\.windowAction\(|fetch\(|\.route\(|\.headers\(|\.postData\(|\.messages\b|\.error\b|\.args\b/);
});

test('inconsistent task/loading fields fail closed rather than preserving a previous pass', async t => {
  const p = pair(t); await p.install();
  await p.publish(state('running', 7), { dom: true });
  const invalid = state('running', 7); invalid.snapshot.loading = false;
  await p.publish(invalid, { dom: true });
  assert.equal(isRunningMirrorProven(await p.read()), false);
});

test('DOM inspection failure invalidates evidence and stops observer resources', async t => {
  const p = pair(t); await p.install();
  await p.publish(state('running', 7), { dom: true });
  p.owner.context.document.querySelectorAll = () => { throw new Error('PRIVATE_DOM_FAILURE'); };
  await p.owner.tick(50);
  const observed = await p.read();
  assert.equal(observed.owner.healthy, false);
  assert.equal(isRunningMirrorProven(observed), false);
  assert.doesNotMatch(JSON.stringify(observed), /PRIVATE_DOM_FAILURE/);
  assert.deepEqual(p.owner.resources(), { timers: 0, listeners: 0, mutations: 0 });
});


test('disposing during baseline refresh never schedules a post-disposal retry timer', async t => {
  const p = pair(t, state('idle', 42), state('idle', 42));
  const ownerLate = p.owner.deferNextRead(), haruLate = p.haru.deferNextRead();
  const installing = p.install();
  const rejected = assert.rejects(installing, { code: 'HARU_SYNC_FAILED' });
  await flush();
  await p.remove();
  await flush();
  assert.deepEqual(p.owner.resources(), { timers: 0, listeners: 0, mutations: 0 });
  assert.deepEqual(p.haru.resources(), { timers: 0, listeners: 0, mutations: 0 });
  await rejected;
  ownerLate.resolve(state()); haruLate.resolve(state()); await flush();
  assert.equal(p.owner.context.window[KEY], undefined);
  assert.equal(p.haru.context.window[KEY], undefined);
});

test('an install RPC delivered after cleanup cannot create an orphan observer', async t => {
  const p = pair(t);
  const release = p.haru.holdNextInstall();
  const installing = p.install();
  const rejected = assert.rejects(installing, { code: 'HARU_SYNC_FAILED' });
  await flush(); await p.remove();
  release(); await flush();
  assert.deepEqual(p.owner.resources(), { timers: 0, listeners: 0, mutations: 0 });
  assert.deepEqual(p.haru.resources(), { timers: 0, listeners: 0, mutations: 0 });
  await rejected;
  assert.equal(p.owner.context.window[KEY], undefined);
  assert.equal(p.haru.context.window[KEY], undefined);
});

test('fresh idle/null bridge baseline waits until old rendered running UI disappears', async t => {
  const p = pair(t);
  await p.publish(state(), { dom: true });
  const installing = p.install(); let installed = false; void installing.then(() => { installed = true; });
  await flush();
  assert.equal(installed, false);
  assert.equal(p.owner.rawSummary().baselineReady, false);
  assert.equal(p.haru.rawSummary().baselineReady, false);
  await p.publish(state(), { dom: false }); await p.tick(50); await installing;
  assert.equal(isRunningMirrorProven(await p.read()), false);
  await p.publish(state('running', 7), { dom: true });
  assert.equal(isRunningMirrorProven(await p.read()), true);
});

test('old DOM running cannot be combined with the next positive-ID bridge state during installation', async t => {
  const p = pair(t);
  await p.publish(state(), { dom: true });
  const installing = p.install(); const rejected = assert.rejects(installing, { code: 'HARU_SYNC_FAILED' });
  await flush();
  await p.publish(state('running', 7), { dom: true }); await p.tick(50);
  assert.equal(p.owner.rawSummary().baselineReady, false);
  assert.equal(p.haru.rawSummary().baselineReady, false);
  await p.remove(); await rejected;
  assert.deepEqual(p.owner.resources(), { timers: 0, listeners: 0, mutations: 0 });
  assert.deepEqual(p.haru.resources(), { timers: 0, listeners: 0, mutations: 0 });
});

test('a delayed old installation failure cleans only its token and preserves a newer case', async t => {
  const p = pair(t);
  const release = p.haru.holdNextInstall();
  const oldInstallation = p.install();
  const oldRejected = assert.rejects(oldInstallation, { code: 'HARU_SYNC_FAILED' });
  await flush(); await p.remove();
  await p.install('pilot-cancel');
  release(); await oldRejected;
  const observed = await p.read();
  assert.equal(observed.owner.caseId, 'pilot-cancel');
  assert.equal(observed.haru.caseId, 'pilot-cancel');
  assert.equal(observed.owner.healthy, true);
  assert.equal(observed.haru.healthy, true);
  await p.publish(state('running', 7), { dom: true });
  assert.equal(isRunningMirrorProven(await p.read()), true);
});


for (const role of ['owner', 'haru']) {
  test(`hidden ${role} document cannot prove DOM running until visibly running again`, async t => {
    const p = pair(t); await p.install();
    p[role].context.document.visibilityState = 'hidden';
    await p.publish(state('running', 7), { dom: true });
    let observed = await p.read();
    assert.equal(observed[role].bridgeRunningObserved, true);
    assert.equal(observed[role].domRunningObserved, false);
    assert.equal(isRunningMirrorProven(observed), false);
    p[role].context.document.visibilityState = 'visible';
    await p.tick(50);
    observed = await p.read();
    assert.equal(observed[role].domRunningObserved, true);
    assert.equal(isRunningMirrorProven(observed), true);
  });
}

test('restoring visibility after hidden running has ended cannot invent running DOM proof', async t => {
  const p = pair(t); await p.install();
  p.owner.context.document.visibilityState = 'hidden';
  p.haru.context.document.visibilityState = 'hidden';
  await p.publish(state('running', 7), { dom: true });
  await p.publish(state('idle', 7), { dom: false });
  p.owner.context.document.visibilityState = 'visible';
  p.haru.context.document.visibilityState = 'visible';
  await p.tick(50);
  const observed = await p.read();
  assert.equal(observed.owner.domRunningObserved, false);
  assert.equal(observed.haru.domRunningObserved, false);
  assert.equal(isRunningMirrorProven(observed), false);
});

test('hidden documents cannot establish a fresh rendered idle baseline', async t => {
  const p = pair(t);
  p.owner.context.document.visibilityState = 'hidden';
  p.haru.context.document.visibilityState = 'hidden';
  const installing = p.install(); await flush();
  assert.equal(p.owner.rawSummary().baselineReady, false);
  assert.equal(p.haru.rawSummary().baselineReady, false);
  p.owner.context.document.visibilityState = 'visible';
  p.haru.context.document.visibilityState = 'visible';
  await p.tick(50); await installing;
  const observed = await p.read();
  assert.equal(observed.owner.baselineReady, true);
  assert.equal(observed.haru.baselineReady, true);
  assert.equal(isRunningMirrorProven(observed), false);
});
