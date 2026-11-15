import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Ledger, PRICE_REVIEW } from '../broker-core.cjs';
import { CASES, PIN } from '../contract.mjs';
import { RECOVERY, PRIOR_ROW, verifyPriorZip, createMockCarryForward, carryForwardEvidence } from '../carry-forward-policy.cjs';
import { MOCK_PILOT_CHECK_MAP } from '../ui-diagnostics.mjs';
import { numericLedger, safeResults, saveEvidence } from '../safe-evidence.mjs';

// Numeric/evidence-only fixtures: no provider transport, EXE or paid action.
const liveCarry = verifyPriorZip(readFileSync(new URL('./fixtures/prior-paid-38694575612.zip', import.meta.url)));
const mockCarry = createMockCarryForward();
const pilot = () => ({ caseId: 'pilot-stream', status: 'SETTLED', envelope: 'UNCHANGED', cap: 4096,
  micro: 64, promptTokens: 20, completionTokens: 3, cacheHitTokens: 0, cacheMissTokens: 20,
  outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: false });
function snapshot({ mode = 'live', current = true } = {}) {
  return { ...new Ledger().snapshot(), sentRequests: current ? 2 : 1,
    settledMicroCny: current ? 252 : 188, retainedMicroCny: 0, closed: true, journalFailed: false,
    currentRequests: current ? 1 : 0, requests: [structuredClone(PRIOR_ROW), ...(current ? [pilot()] : [])],
    carryForward: structuredClone(carryForwardEvidence(mode === 'live' ? liveCarry : mockCarry)),
    provenance: { ...PRICE_REVIEW, sessionId: RECOVERY.sessionId, mode: 'FIXED_EXE_UI_BUDGET_BROKER',
      productCommit: PIN.commit, buildRunId: String(PIN.runId), artifactId: String(PIN.artifactId),
      installerSha256: PIN.installerSha256, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), runId: '1', runAttempt: 1 },
    ...(mode === 'mock' ? { mode: 'MOCK', mock: { transport: 'IN_PROCESS_SYNTHETIC_HTTPS', externalNetwork: 'DENIED',
      realProviderCalls: 0, usageIsSynthetic: true, fakeRequests: current ? 1 : 0, clockScope: 'BROKER_VM_ONLY' } } : {}) };
}
function scenario({ mode = 'live', sustained = true } = {}) {
  const checks = { sustainedStreamingObserved: sustained, incrementalAssistantRendering: sustained,
    freshConversationProven: true, uiAdmissionProven: true, naturalCompletionProven: true,
    terminalIdentityProven: true, terminalMirrorProven: true, terminalTextProven: true,
    ledgerUnchanged: true, admissionObserverCleanupPassed: true, providerRequestSettled: true,
    oneProviderRequestVerified: true, haruVisibleAssistantMatchesSnapshot: true,
    ownerRunningTransitionObserved: sustained, haruRunningTransitionObserved: sustained,
    positiveRunningConversationMatched: sustained, finalRunningConversationMatched: sustained,
    haruRunningAndIdleMirrored: sustained };
  return { id: 'pilot-stream', status: 'PASS',
    code: mode === 'mock' ? 'MOCK_PILOT_STREAMING_PROVEN' : sustained ? 'LIVE_STREAMING_PROVEN' : 'LIVE_COMPLETION_ONLY',
    checks: mode === 'mock' ? Object.fromEntries(Object.entries(checks).map(([key, value]) => [MOCK_PILOT_CHECK_MAP[key] ?? key, value])) : checks };
}
function report(options = {}) {
  return { mode: options.mode ?? 'live', scope: RECOVERY.scope, status: 'PASS',
    code: options.sustained === false ? 'PILOT_HARU_RECOVERY_COMPLETION_ONLY' : 'PILOT_HARU_RECOVERY_PASSED',
    cleanupCode: 'CLEANUP_PASSED', cleanupPassed: true, scenarios: [scenario(options)] };
}
const blocked = () => ({ mode: 'live', scope: RECOVERY.scope, status: 'BLOCKED', code: 'NOT_STARTED',
  cleanupCode: 'CLEANUP_PASSED', cleanupPassed: true,
  scenarios: [{ id: 'pilot-stream', status: 'BLOCKED', code: 'NOT_STARTED', checks: {} }] });
async function directory(t) {
  const result = await fs.mkdtemp(path.join(os.tmpdir(), 'scoped-evidence-'));
  t.after(() => fs.rm(result, { recursive: true, force: true })); return result;
}
const read = async root => ({ result: JSON.parse(await fs.readFile(path.join(root, 'result.json'), 'utf8')),
  ledger: JSON.parse(await fs.readFile(path.join(root, 'ledger.json'), 'utf8')) });

for (const options of [{ mode: 'live' }, { mode: 'live', sustained: false }, { mode: 'mock' }]) {
  test(`scoped ${options.mode} ${options.sustained === false ? 'completion' : 'streaming'} proof never claims all seven cases`, async t => {
    const root = await directory(t), before = snapshot(options);
    await saveEvidence(root, report(options), before);
    const saved = await read(root);
    assert.equal(saved.result.scope, RECOVERY.scope);
    assert.equal(saved.result.code, report(options).code);
    assert.equal(saved.result.realProviderCalled, options.mode === 'live');
    assert.deepEqual(saved.result.scenarios.map(row => row.id), ['pilot-stream']);
    assert.deepEqual(saved.result.notRunCases, CASES.filter(id => id !== 'pilot-stream')
      .map(id => ({ id, status: 'NOT_RUN', code: 'OUTSIDE_RECOVERY_SCOPE' })));
    assert.equal(saved.result.releaseReady, false);
    assert.equal(saved.ledger.sentRequests, 2); assert.equal(saved.ledger.currentRequests, 1);
    assert.equal(saved.ledger.settledMicroCny, 252); assert.equal(saved.ledger.currentSettledMicroCny, 64);
    assert.equal(saved.ledger.currentRetainedMicroCny, 0);
    assert.deepEqual(saved.ledger.requests[0], PRIOR_ROW);
    assert.equal(saved.ledger.carryForward.priorRequestCount, 1);
    assert.equal(saved.ledger.carryForward.priorSettledMicroCny, 188);
    assert.equal(saved.ledger.productCommit, PIN.commit);
    if (options.mode === 'live') {
      assert.equal(saved.ledger.carryForward.source.productCommit, RECOVERY.source.productCommit);
      assert.notEqual(saved.ledger.carryForward.source.productCommit, PIN.commit);
      assert.deepEqual(saved.result.coverageLimitations, options.sustained === false ? ['PILOT_SUSTAINED_STREAMING_NOT_PROVEN'] : []);
    } else assert.deepEqual(saved.ledger.carryForward.source, { synthetic: true, kind: 'synthetic-carry-forward-fixture' });
    assert.deepEqual(before, snapshot(options), 'export never rewrites original source or current ledger');
  });
}

test('pre-action failure preserves known 188 / 1 baseline and reports no current real-provider call', async t => {
  const root = await directory(t);
  await saveEvidence(root, blocked(), snapshot({ current: false }));
  const saved = await read(root);
  assert.equal(saved.result.realProviderCalled, false);
  assert.equal(saved.ledger.sentRequests, 1); assert.equal(saved.ledger.settledMicroCny, 188);
  assert.equal(saved.ledger.currentRequests, 0); assert.equal(saved.ledger.currentSettledMicroCny, 0);
  assert.equal(saved.ledger.retainedMicroCny, 0);
});
test('unverified source means cost unavailable, never a zero-cost fresh ledger', async t => {
  const root = await directory(t);
  await saveEvidence(root, blocked(), {});
  const saved = await read(root);
  assert.equal(saved.result.realProviderCalled, false); assert.equal(saved.ledger.unavailable, true);
  assert.equal(saved.ledger.budgetUnavailableMicroCny, 10000000);
  for (const field of ['sentRequests', 'settledMicroCny', 'currentRequests', 'currentSettledMicroCny', 'carryForward'])
    assert.equal(Object.hasOwn(saved.ledger, field), false);
  const fresh = snapshot({ current: false }); delete fresh.carryForward; delete fresh.currentRequests;
  fresh.requests = []; fresh.sentRequests = 0; fresh.settledMicroCny = 0;
  await assert.rejects(saveEvidence(root, blocked(), fresh), { safeCode: 'REPORT_INVALID' });
});
for (const outboundStarted of [false, true]) test(`uncertain current usage retains 3 CNY plus history; current outbound=${outboundStarted}`, async t => {
  const root = await directory(t), book = snapshot();
  book.requests[1] = { caseId: 'pilot-stream', status: 'TIMEOUT', envelope: 'UNCHANGED', cap: 4096,
    micro: 3000000, outboundStarted, upstreamResponded: false, clientDisconnectObserved: false };
  book.settledMicroCny = 188; book.retainedMicroCny = 3000000;
  await saveEvidence(root, blocked(), book);
  const saved = await read(root);
  assert.equal(saved.result.realProviderCalled, outboundStarted);
  assert.equal(saved.ledger.settledMicroCny + saved.ledger.retainedMicroCny, 3000188);
  assert.equal(saved.ledger.currentSettledMicroCny, 0); assert.equal(saved.ledger.currentRetainedMicroCny, 3000000);
});

for (const [name, mutate] of [
  ['zero prior cost', v => { v.carryForward.priorSettledMicroCny = 0; }],
  ['187 prior cost', v => { v.carryForward.priorSettledMicroCny = 187; }],
  ['189 prior cost', v => { v.carryForward.priorSettledMicroCny = 189; }],
  ['zero prefix count', v => { v.carryForward.priorRequestCount = 0; }],
  ['two prefix count', v => { v.carryForward.priorRequestCount = 2; }],
  ['historical row cost', v => { v.requests[0].micro = 0; }],
  ['historical cap', v => { v.requests[0].cap = 63; }],
  ['historical usage', v => { v.requests[0].promptTokens++; }],
  ['historical field', v => { v.requests[0].productCommit = PIN.commit; }],
  ['historical removal', v => { v.requests.shift(); v.sentRequests--; }],
  ['extra request', v => { v.requests.push(pilot()); v.sentRequests++; v.currentRequests++; }],
  ['wrong current case', v => { v.requests[1].caseId = 'connection'; }],
  ['current count', v => { v.currentRequests = 0; }],
  ['current token cap', v => { v.requests[1].cap = 4097; }],
  ['current invalid settled transport', v => { v.requests[1].outboundStarted = false; }],
  ['cumulative cost reset', v => { v.settledMicroCny -= 188; }],
  ['retained cost', v => { v.retainedMicroCny = 1; }],
  ['scope', v => { v.carryForward.scope = 'full-suite'; }],
  ['source new product', v => { v.carryForward.source.productCommit = PIN.commit; }],
  ['source new installer', v => { v.carryForward.source.installerSha256 = PIN.installerSha256; }],
  ['source price relabel', v => { v.carryForward.source.priceCheckedAt = '2026-10-16'; }],
  ['source raw field', v => { v.carryForward.source.raw = 'private-provider-content'; }],
  ['current old product', v => { v.provenance.productCommit = RECOVERY.source.productCommit; }],
  ['current old review', v => { v.provenance.priceCheckedAt = '2026-10-14'; }],
]) test(`${name} cannot be exported as scoped evidence`, async t => {
  const root = await directory(t), book = snapshot(); mutate(book);
  assert.throws(() => numericLedger(book), { safeCode: 'LEDGER_SHAPE_INVALID' });
  await assert.rejects(saveEvidence(root, blocked(), book), { safeCode: 'LEDGER_SHAPE_INVALID' });
  assert.deepEqual(await fs.readdir(root), []);
});

test('recovery refuses full-suite labels, old connection PASS, subset/full-suite swaps and generic MOCK PASS', async t => {
  const root = await directory(t);
  for (const mutate of [v => { v.code = 'ALL_UI_CASES_PASSED'; }, v => { v.code = 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY'; },
    v => { delete v.scope; }, v => { v.scope = 'full-suite'; }, v => { v.scope = 'arbitrary-recovery'; },
    v => { v.scenarios.unshift({ id: 'connection', status: 'PASS', code: 'PASSED', checks: {} }); },
    v => { v.scenarios = CASES.map(id => id === 'pilot-stream' ? scenario() : { id, status: 'PASS', code: 'PASSED', checks: {} }); },
    v => { v.scenarios[0].id = 'connection'; }, v => { v.status = 'BLOCKED'; }]) {
    const changed = report(); mutate(changed); await assert.rejects(saveEvidence(root, changed, snapshot()));
  }
  const mock = report({ mode: 'mock' }); mock.scenarios[0] = { id: 'pilot-stream', status: 'PASS', code: 'PASSED', checks: {} };
  await assert.rejects(saveEvidence(root, mock, snapshot({ mode: 'mock' })), { safeCode: 'SCENARIO_EVIDENCE_INVALID' });
  assert.throws(() => safeResults([scenario()], false), { safeCode: 'SCENARIO_EVIDENCE_INVALID' });
  assert.throws(() => safeResults([scenario()], false, 'full-suite'), { safeCode: 'SCENARIO_EVIDENCE_INVALID' });
  assert.deepEqual(await fs.readdir(root), []);
});

test('LIVE/MOCK reports, prefixes, and transport counters cannot be mixed', async t => {
  const root = await directory(t);
  for (const [result, book] of [[report(), snapshot({ mode: 'mock' })], [report({ mode: 'mock' }), snapshot()],
    [blocked(), snapshot({ mode: 'mock', current: false })]]) await assert.rejects(saveEvidence(root, result, book));
  for (const mutate of [v => { v.carryForward.mode = 'live'; }, v => { v.carryForward.source = structuredClone(RECOVERY.source); },
    v => { v.mock.fakeRequests = v.sentRequests; }, v => { v.mock.realProviderCalls = 1; },
    v => { v.mock.externalNetwork = 'ALLOWED'; }, v => { v.mock.clockScope = 'GLOBAL'; }]) {
    const book = snapshot({ mode: 'mock' }); mutate(book);
    await assert.rejects(saveEvidence(root, report({ mode: 'mock' }), book));
  }
  const genericLive = snapshot(); genericLive.mode = 'MOCK';
  await assert.rejects(saveEvidence(root, report(), genericLive));
  assert.deepEqual(await fs.readdir(root), []);
});

test('cleanup downgrade preserves proof checks but cannot emit overall PASS or a success code', async t => {
  const root = await directory(t), result = report();
  result.cleanupPassed = false; result.cleanupCode = 'BROKER_CLEANUP_FAILED';
  await assert.rejects(saveEvidence(root, result, snapshot()), { safeCode: 'REPORT_INVALID' });
  result.status = 'BLOCKED';
  await assert.rejects(saveEvidence(root, result, snapshot()), { safeCode: 'REPORT_INVALID' });
  result.code = 'BROKER_CLEANUP_FAILED';
  await saveEvidence(root, result, snapshot());
  const saved = await read(root);
  assert.equal(saved.result.status, 'BLOCKED'); assert.equal(saved.result.code, 'BROKER_CLEANUP_FAILED');
  assert.equal(saved.result.scenarios[0].checks.naturalCompletionProven, true);
  result.scenarios[0] = { ...result.scenarios[0], status: 'BLOCKED', code: 'PROVIDER_BUDGET_BLOCKED',
    checks: { ...result.scenarios[0].checks, brokerCleanupFailed: true } };
  await saveEvidence(root, result, snapshot());
  assert.equal((await read(root)).result.scenarios[0].checks.naturalCompletionProven, true);
});

test('recovery execution timing is an optional bounded numeric projection unique to this scope', async t => {
  const root = await directory(t), result = blocked();
  result.executionTiming = { windowMs: 120000, elapsedBeforeUiMs: 40000, remainingBeforeUiMs: 80000 };
  await saveEvidence(root, result, snapshot({ current: false }));
  assert.deepEqual((await read(root)).result.executionTiming, result.executionTiming);
  for (const timing of [{ windowMs: 600000, elapsedBeforeUiMs: 40000, remainingBeforeUiMs: 80000 },
    { windowMs: 120000, elapsedBeforeUiMs: -1, remainingBeforeUiMs: 80000 },
    { windowMs: 120000, elapsedBeforeUiMs: 600001, remainingBeforeUiMs: 0 },
    { windowMs: 120000, elapsedBeforeUiMs: 1.5, remainingBeforeUiMs: 80000 },
    { windowMs: 120000, elapsedBeforeUiMs: 40000, remainingBeforeUiMs: -1 },
    { windowMs: 120000, elapsedBeforeUiMs: 40000, remainingBeforeUiMs: 120001 },
    { windowMs: 120000, elapsedBeforeUiMs: 40000 },
    { windowMs: 120000, elapsedBeforeUiMs: 40000, remainingBeforeUiMs: 80000, raw: 'private-content' }])
    await assert.rejects(saveEvidence(root, { ...result, executionTiming: timing }, snapshot({ current: false })), { safeCode: 'REPORT_INVALID' });
  await assert.rejects(saveEvidence(root, { ...result, scope: 'full-suite',
    scenarios: CASES.map(id => ({ id, status: 'BLOCKED', code: 'NOT_STARTED', checks: {} })) }, {}), { safeCode: 'REPORT_INVALID' });
});
