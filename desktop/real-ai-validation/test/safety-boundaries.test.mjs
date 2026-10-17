import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { parse } from 'yaml';
import { CASES, PIN } from '../contract.mjs';
import { Ledger } from '../broker-core.cjs';
import { validateSyntheticRoute } from '../synthetic-api.mjs';
import { safeResults, numericLedger, saveEvidence } from '../safe-evidence.mjs';
import { githubReader } from '../github-read.mjs';
const here = new URL('../', import.meta.url);
const priceProvenance = { priceCheckedAt: '2026-10-16', validUntilUtc: '2026-10-16T23:59:59.999Z',
  priceBasis: 'PEAK_CNY', sessionId: 'offerpilot-fixed-exe-real-ai-20261008', mode: 'FIXED_EXE_UI_BUDGET_BROKER' };
const workflow = parse(await fs.readFile(new URL('../../.github/validation/historical-workflows/desktop-real-ai.yml', here), 'utf8'));
test('only original branch push route; no dispatch/PR/schedule triggers', () => {
  assert.deepEqual(Object.keys(workflow.on), ['push']);
  assert.deepEqual(workflow.on.push.branches, ['feat/20261005-windows-desktop-validation']);
  assert.equal(workflow.concurrency.group, 'offerpilot-fixed-exe-real-ai-20261008');
  assert.equal(workflow.concurrency['cancel-in-progress'], false);
  assert.ok(workflow.jobs.preflight.if.includes('github.sha == vars.OFFERPILOT_AI_REQUEST_SHA'));
  assert.ok(workflow.jobs.preflight.if.includes('github.run_attempt == 1'));
});
test('key appears exactly once and only after approval/dependencies/artifact preparation', () => {
  let secretSteps = [];
  for (const job of Object.values(workflow.jobs)) {
    assert.deepEqual(job.permissions, { contents: 'read', actions: 'read' });
    assert.equal(JSON.stringify(job.env || {}).includes('secrets.'), false);
    for (const [index, step] of job.steps.entries()) {
      if (JSON.stringify(step).includes('secrets.')) secretSteps.push({ job, index, step });
      if (step.uses) assert.match(step.uses, /^[\w/-]+@[0-9a-f]{40}$/);
      if (step.uses?.startsWith('actions/checkout@')) assert.equal(step.with['persist-credentials'], false);
    }
  }
  assert.equal(secretSteps.length, 1);
  const { job, index, step } = secretSteps[0];
  assert.equal(job.environment.name, 'offerpilot-real-ai-validation');
  assert.equal(step.env.OFFERPILOT_REAL_AI_KEY, '${{ secrets.OFFERPILOT_REAL_AI_DEEPSEEK_20261008 }}');
  const prior = job.steps.slice(0, index).map(step => step.run || '').join('\n');
  for (const required of ['preflight.mjs --approval', 'npm.cmd ci', '--ignore-scripts', 'npm.cmd test', 'prepare.mjs']) assert.ok(prior.includes(required));
  assert.ok(job.steps.some(step => step.if === '${{ always() }}' && step.run?.includes('cleanup.mjs')));
});
test('artifact allowlist contains no screenshots/raw/profile/config/export logs', () => {
  const upload = workflow.jobs['real-ai'].steps.find(step => step.uses?.startsWith('actions/upload-artifact@'));
  assert.deepEqual(upload.with.path.trim().split('\n'), ['desktop/real-ai-validation/evidence/result.json', 'desktop/real-ai-validation/evidence/ledger.json']);
  assert.equal(workflow.jobs['real-ai'].steps.some(step => /HAR|trace|Start-Transcript/.test(step.run || '')), false);
});
test('synthetic API adapter rejects every direct LLM/config route', () => {
  for (const route of ['/api/chat', '/api/chat/stream', '/api/settings', '/api/settings/providers/test', '/api/resumes/1/structure-preview',
    '/api/offers/1/negotiation/proposals', '/api/applications/1/interview-preparation-proposals', 'https://evil.test/api/applications', '/api/applications?secret=x'])
    for (const method of ['GET', 'POST', 'PATCH']) assert.throws(() => validateSyntheticRoute(route, method));
  validateSyntheticRoute('/api/applications', 'GET'); validateSyntheticRoute('/api/resumes/1', 'PATCH');
});
const rows = () => CASES.map(id => ({ id, status: 'BLOCKED', code: 'NOT_STARTED', checks: {} }));
test('result allowlist strips unknown payloads and rejects arbitrary check keys', () => {
  const result = rows(); result[0].rawResponse = 'fake-secret';
  assert.equal(JSON.stringify(safeResults(result)).includes('fake-secret'), false);
  result[0].code = 'PRIVATE_UNKNOWN_ENUM'; assert.throws(() => safeResults(result)); result[0].code = 'NOT_STARTED';
  result[0].checks.fakePrivateIdentifier = true;
  assert.throws(() => safeResults(result));
});
const continuationRows = () => {
  const result = rows();
  result[1] = { id: 'pilot-stream', status: 'FAIL', code: 'STREAM_NOT_OBSERVED', checks: {},
    diagnostic: { stage: 'PILOT_STREAM_READBACK' }, continuation: { status: 'PROVEN', code: 'MOCK_CONTINUATION_PROVEN',
      eligible: true, ledgerSafe: true, mirrorProven: true, domEqual: true, noNewRequests: true,
      cleanupPassed: true, continued: true } };
  return result;
};
test('MOCK continuation is explicit fixed-field FAIL evidence, never LIVE or a PASS override', () => {
  const result = continuationRows(); result[1].continuation.raw = 'private-terminal-content';
  const safe = safeResults(result, true);
  assert.equal(safe[1].status, 'FAIL'); assert.equal(safe[1].continuation.continued, true);
  assert.doesNotMatch(JSON.stringify(safe), /private-terminal-content|raw/);
  assert.throws(() => safeResults(result), { safeCode: 'SCENARIO_EVIDENCE_INVALID' });
  for (const changes of [{ status: 'PASS' }, { id: 'connection' }, { code: 'HARU_SYNC_FAILED' },
    { diagnostic: { stage: 'PILOT_RUNNING' } }]) {
    const changed = continuationRows(); Object.assign(changed[1], changes);
    assert.throws(() => safeResults(changed, true), { safeCode: 'SCENARIO_EVIDENCE_INVALID' });
  }
  for (const key of ['eligible', 'ledgerSafe', 'mirrorProven', 'domEqual', 'noNewRequests', 'cleanupPassed']) {
    const changed = continuationRows(); changed[1].continuation[key] = false;
    assert.throws(() => safeResults(changed, true), { safeCode: 'SCENARIO_EVIDENCE_INVALID' });
  }
  for (const changes of [{ status: 'PRIVATE' }, { code: 'PRIVATE' }, { cleanupPassed: 'true' }]) {
    const changed = continuationRows(); Object.assign(changed[1].continuation, changes);
    assert.throws(() => safeResults(changed, true), { safeCode: 'SCENARIO_EVIDENCE_INVALID' });
  }
});
test('numeric ledger does not serialize arbitrary fields or credential-shaped strings', () => {
  const reasons = 'AUTH ROUTE CLOSED DEADLINE UNARMED BUSY CASE BUDGET COUNT BODY MODEL PARAMETER CANCELLED DISCONNECT TIMEOUT LEDGER UPSTREAM REDIRECT PROTOCOL USAGE SETTLED EXPIRED UPSTREAM_DISCONNECT'.split(' ');
  const snapshot = { model: 'deepseek-flash', budgetMicroCny: 10000000, reserveMicroCny: 3000000, sentRequests: 1, settledMicroCny: 20,
    retainedMicroCny: 0, active: false, closed: true, journalFailed: false,
    denied: Object.fromEntries(reasons.map(key => [key, 0])), privateName123: 111,
    apiKey: 'fake-secret', provenance: { ...priceProvenance, productCommit: PIN.commit, buildRunId: String(PIN.runId), artifactId: String(PIN.artifactId), installerSha256: PIN.installerSha256, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), runId: '1', runAttempt: 1 }, requests: [{ caseId: 'connection', status: 'SETTLED', envelope: 'UNCHANGED', cap: 64,
      micro: 20, outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: false, promptTokens: 2, completionTokens: 2, cacheHitTokens: 0, cacheMissTokens: 2, raw: 'fake-secret' }] };
  const text = JSON.stringify(numericLedger(snapshot));
  assert.equal(text.includes('fake-secret'), false); assert.equal(text.includes('privateName123'), false);
  assert.equal(numericLedger(snapshot).requests[0].cap, 64);
  for (const [field, other] of Object.entries({ priceCheckedAt: '2026-10-08',
    validUntilUtc: '2026-10-08T23:59:59.999Z', priceBasis: 'OFF_PEAK_CNY',
    sessionId: 'offerpilot-fixed-exe-real-ai-20261009', mode: 'OTHER_BROKER' })) {
    for (const value of [other, undefined, null]) {
      assert.throws(() => numericLedger({ ...snapshot, provenance: { ...snapshot.provenance, [field]: value } }),
        { safeCode: 'LEDGER_SHAPE_INVALID' }, `mixed or missing price/session provenance: ${field}`);
    }
  }
  assert.throws(() => numericLedger({ ...snapshot, provenance: { ...snapshot.provenance,
    priceCheckedAt: '2026-10-09', validUntilUtc: '2026-10-09T23:59:59.999Z' } }),
  { safeCode: 'LEDGER_SHAPE_INVALID' }, 'an old price snapshot cannot be relabelled with the new review date');
  assert.equal(numericLedger(snapshot).priceCheckedAt, snapshot.provenance.priceCheckedAt);
  assert.equal(numericLedger(snapshot).priceValidUntilUtc, snapshot.provenance.validUntilUtc);
  assert.equal(numericLedger(snapshot).sessionId, snapshot.provenance.sessionId);
  for (const [field, other] of Object.entries({ productCommit: 'f'.repeat(40), buildRunId: String(PIN.runId + 1),
    artifactId: String(PIN.artifactId + 1), installerSha256: 'f'.repeat(64) })) {
    const mixed = { ...snapshot, provenance: { ...snapshot.provenance, [field]: other } };
    assert.throws(() => numericLedger(mixed), { safeCode: 'LEDGER_SHAPE_INVALID' }, `mixed product provenance: ${field}`);
  }
  const previousProducts = [
    { productCommit: '16f31e477fd9882392ea8f754b6e2ef5ebdcf5c4', buildRunId: '37754883783',
      artifactId: '11540740222', installerSha256: '2e7b144ef657dcfa6e9408b532b59617c00f5a442081ff47ec18b75753713439' },
    { productCommit: 'c040a5d2f1949ff8a4ae806e7c3b593c6481e6d0', buildRunId: '37806395272',
      artifactId: '11564445795', installerSha256: '9e33c18f5c83d01bd23ebed01e22d5952a72787fef48cefec8dd875686e46ea7' },
  ];
  for (const previousProduct of previousProducts) {
    assert.notEqual(PIN.commit, previousProduct.productCommit);
    assert.throws(() => numericLedger({ ...snapshot, provenance: { ...snapshot.provenance, ...previousProduct } }),
      { safeCode: 'LEDGER_SHAPE_INVALID' }, 'the old package ledger cannot be relabelled as the new product');
  }
  snapshot.requests[0].status = 'KEY_WAS_FAKE'; assert.throws(() => numericLedger(snapshot));
});
test('unavailable ledger means entire remaining budget unavailable', () => {
  assert.equal(numericLedger({}).budgetUnavailableMicroCny, 10000000);
});
test('stale price evidence writes nothing and cannot release an uncertain reservation', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-price-evidence-'));
  const book = new Ledger(); book.armCase('connection'); const ticket = book.claim();
  book.reserve(ticket, 64, 'INSERTED'); book.finish(ticket, 'TIMEOUT'); book.close();
  const snapshot = { ...book.snapshot(), journalFailed: false, provenance: { ...priceProvenance,
    productCommit: PIN.commit, buildRunId: String(PIN.runId), artifactId: String(PIN.artifactId),
    installerSha256: PIN.installerSha256, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40),
    runId: '1', runAttempt: 1 } };
  const before = structuredClone(snapshot);
  const report = { status: 'BLOCKED', code: 'NOT_STARTED', scenarios: rows() };
  try {
    for (const changes of [
      { priceCheckedAt: '2026-10-08', validUntilUtc: '2026-10-08T23:59:59.999Z' },
      { priceCheckedAt: '2026-10-09', validUntilUtc: '2026-10-09T23:59:59.999Z' },
      { priceCheckedAt: '2026-10-10', validUntilUtc: '2026-10-10T23:59:59.999Z' },
      { priceCheckedAt: '2026-10-10' },
      { validUntilUtc: '2026-10-10T23:59:59.999Z' },
      { priceCheckedAt: '2026-10-12', validUntilUtc: '2026-10-12T23:59:59.999Z' },
      { priceCheckedAt: '2026-10-15', validUntilUtc: '2026-10-15T23:59:59.999Z' },
      { priceCheckedAt: '2026-10-15' },
      { validUntilUtc: '2026-10-15T23:59:59.999Z' },
      { priceCheckedAt: '2026-10-09' },
      { validUntilUtc: '2026-10-09T23:59:59.999Z' },
      { validUntilUtc: '2026-10-12T23:59:59.999Z' },
      { sessionId: 'offerpilot-fixed-exe-real-ai-20261009' },
    ]) {
      await assert.rejects(saveEvidence(directory, report, { ...snapshot,
        provenance: { ...snapshot.provenance, ...changes } }), { safeCode: 'LEDGER_SHAPE_INVALID' });
      assert.deepEqual(await fs.readdir(directory), []);
    }
    await saveEvidence(directory, report, snapshot);
    const output = JSON.parse(await fs.readFile(path.join(directory, 'ledger.json'), 'utf8'));
    assert.equal(output.priceCheckedAt, '2026-10-16');
    assert.equal(output.sessionId, 'offerpilot-fixed-exe-real-ai-20261008');
    assert.equal(output.retainedMicroCny, 3000000); assert.equal(output.settledMicroCny, 0);
    assert.equal(output.requests[0].micro, 3000000); assert.equal(output.requests[0].status, 'TIMEOUT');
    assert.deepEqual(snapshot, before, 'export does not rewrite the original ledger');
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});
test('evidence writer never outputs supplied credential', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-evidence-test-'));
  try {
    await assert.rejects(saveEvidence(directory, { status: 'BLOCKED', code: 'NOT_STARTED', scenarios: rows() }, {}, ['NOT_STARTED']));
    assert.deepEqual(await fs.readdir(directory), []);
    await saveEvidence(directory, { status: 'BLOCKED', code: 'NOT_STARTED', scenarios: rows() }, {}, ['fake-key-never-output']);
    assert.deepEqual((await fs.readdir(directory)).sort(), ['ledger.json', 'result.json']);
    const report = JSON.parse(await fs.readFile(path.join(directory, 'result.json'), 'utf8'));
    assert.equal(report.fullRegressionRunId, PIN.fullRegressionRunId);
    assert.equal(report.fullRegression, PIN.fullRegressionRunId === null ? 'not-run-package-only' : 'independent-not-certified');
    assert.equal(report.independentFullGateCertified, false);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});
test('GitHub metadata transport is GET only, fixed origin and fails closed without raw errors', async () => {
  const token = 'FAKE_GITHUB_TOKEN'; let call;
  const read = githubReader(token, async (...args) => { call = args; return { ok: true, headers: new Headers(), json: async () => ({ safe: true }) }; });
  assert.deepEqual(await read('environments/offerpilot-real-ai-validation'), { safe: true });
  assert.equal(call[1].method, 'GET'); assert.equal(call[1].redirect, 'error');
  assert.ok(call[0].startsWith('https://api.github.com/repos/offercontext/offerPilot/'));
  await assert.rejects(read('../secrets'), error => error.message === 'GITHUB_READ_PATH_DENIED');
  await assert.rejects(githubReader(token, async () => { throw new Error(token); })('safe'), error => !error.message.includes(token));
});

test('PASS evidence is forbidden until cleanup and ledger are verified', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-pass-test-'));
  try {
    await assert.rejects(saveEvidence(directory, { status: 'PASS', code: 'ALL_UI_CASES_PASSED', scenarios: rows(), cleanupPassed: false }, {}));
    assert.deepEqual(await fs.readdir(directory), []);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('ledger write failure cannot replace the existing result with PASS', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-ledger-write-'));
  const reasons = 'AUTH ROUTE CLOSED DEADLINE UNARMED BUSY CASE BUDGET COUNT BODY MODEL PARAMETER CANCELLED DISCONNECT TIMEOUT LEDGER UPSTREAM REDIRECT PROTOCOL USAGE SETTLED EXPIRED UPSTREAM_DISCONNECT'.split(' ');
  const ledger = { model: 'deepseek-flash', budgetMicroCny: 10000000, reserveMicroCny: 3000000,
    sentRequests: 7, settledMicroCny: 120, retainedMicroCny: 3000000, active: false, closed: true, journalFailed: false,
    provenance: { ...priceProvenance, productCommit: PIN.commit, buildRunId: String(PIN.runId), artifactId: String(PIN.artifactId), installerSha256: PIN.installerSha256, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), runId: '1', runAttempt: 1 },
    denied: Object.fromEntries(reasons.map(key => [key, 0])),
    requests: CASES.map(caseId => ({ caseId, status: caseId === 'pilot-cancel' ? 'DISCONNECT' : 'SETTLED',
      envelope: 'UNCHANGED', cap: 64, micro: caseId === 'pilot-cancel' ? 3000000 : 20,
      outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: caseId === 'pilot-cancel' })) };
  try {
    await fs.writeFile(path.join(directory, 'result.json'), '{"status":"BLOCKED"}');
    await fs.mkdir(path.join(directory, 'ledger.json'));
    await assert.rejects(saveEvidence(directory, { mode: 'mock', status: 'PASS', code: 'ALL_UI_CASES_PASSED',
      scenarios: CASES.map(id => ({ id, status: 'PASS', code: 'PASSED', checks: {} })),
      cleanupPassed: true, cleanupCode: 'CLEANUP_PASSED' }, ledger),
    error => error.safeCode === undefined && typeof error.code === 'string');
    assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'result.json'), 'utf8')).status, 'BLOCKED');
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('unreadable profile ownership is not mistaken for absence', async () => {
  const { exists } = await import('../prepare.mjs');
  assert.equal(await exists('fake-path', async () => { throw Object.assign(new Error(), { code: 'ENOENT' }); }), false);
  for (const code of ['EACCES', 'EIO', 'EPERM']) await assert.rejects(exists('fake-path', async () => { throw Object.assign(new Error(), { code }); }));
});


test('MOCK Windows entry is separate and live cannot select fake transport with environment flags', async () => {
  const live = await fs.readFile(new URL('../run.mjs', import.meta.url), 'utf8');
  assert.equal(/import .*mock-provider/.test(live), false);
  assert.ok(live.includes("mode: 'live'"));
  assert.equal(/AI_MODE|MOCK_MODE|process.argv/.test(live), false);
  const offline = workflow.jobs.offline;
  assert.equal(offline.environment, undefined);
  assert.equal(JSON.stringify(offline).includes('secrets.'), false);
  assert.ok(offline.if.includes("needs.route.outputs.offline == 'true'"));
  assert.ok(offline.steps.some(step => step.run?.includes('offline.mjs run')));
  assert.ok(offline.steps.filter(step => step.uses?.startsWith('actions/upload-artifact@')).every(step => step.with.name.startsWith('fixed-exe-mock-evidence-')));
});

test('live key is removed from environment before third-party module initialization', async () => {
  const live = await fs.readFile(new URL('../run.mjs', import.meta.url), 'utf8');
  const removed = live.indexOf('delete process.env.OFFERPILOT_REAL_AI_KEY');
  assert.ok(removed > 0 && removed < live.indexOf("import('./validation-runner.mjs')"));
  assert.equal(live.includes('mock-provider'), false);
  assert.equal(/^import /m.test(live), false);
});

test('MOCK image uploads are a fixed whitelist and live remains JSON-only', async () => {
  const { SCREEN_IDS } = await import('../mock-screenshots.mjs');
  const offlineUpload = workflow.jobs.offline.steps.find(step => step.uses?.startsWith('actions/upload-artifact@'));
  assert.deepEqual(offlineUpload.with.path.trim().split('\n'), [
    'desktop/real-ai-validation/mock-evidence/result.json', 'desktop/real-ai-validation/mock-evidence/ledger.json',
    ...SCREEN_IDS.map(id => `desktop/real-ai-validation/mock-evidence/screens/${id}.png`),
  ]);
  const liveUpload = workflow.jobs['real-ai'].steps.find(step => step.uses?.startsWith('actions/upload-artifact@'));
  assert.equal(liveUpload.with.path.includes('.png'), false);
  const live = await fs.readFile(new URL('../run.mjs', import.meta.url), 'utf8');
  const offline = await fs.readFile(new URL('../offline.mjs', import.meta.url), 'utf8');
  const shared = await fs.readFile(new URL('../validation-runner.mjs', import.meta.url), 'utf8');
  assert.equal(live.includes('screenshotFactory'), false);
  assert.ok(offline.includes('screenshotFactory: createMockScreenshots'));
  assert.ok(shared.includes("(mode !== 'live' || screenshotFactory === undefined)"));
  assert.ok(shared.includes('screenshotEvidence.registerToken(preparedCase.clientToken)'));
});

test('screenshot summary cannot disclose arbitrary strings or appear in live evidence', async () => {
  const { safeScreenshotEvidence } = await import('../safe-evidence.mjs');
  assert.deepEqual(safeScreenshotEvidence(undefined, false), { captured: [], skipped: [] });
  const value = { captured: ['pilot-stream'], skipped: [{ id: 'failure-owner', code: 'SCREEN_GUARD_REJECTED', token: 'private-key' }] };
  const clean = safeScreenshotEvidence(value, true);
  assert.doesNotMatch(JSON.stringify(clean), /private-key|token/);
  assert.throws(() => safeScreenshotEvidence(value, false));
  assert.throws(() => safeScreenshotEvidence({ captured: ['../private-key.png'] }, true));
  assert.throws(() => safeScreenshotEvidence({ skipped: [{ id: 'failure-owner', code: 'private-key' }] }, true));
});

test('screenshot guard reasons are a strict enum and cannot carry DOM or credentials', async () => {
  const { safeScreenshotEvidence } = await import('../safe-evidence.mjs');
  const value = reason => ({ skipped: [{ id: 'failure-owner', code: 'SCREEN_GUARD_REJECTED', reason, raw: 'private-key' }] });
  assert.deepEqual(safeScreenshotEvidence(value('BUSINESS_SURFACE'), true), { captured: [], skipped: [
    { id: 'failure-owner', code: 'SCREEN_GUARD_REJECTED', reason: 'BUSINESS_SURFACE' }] });
  for (const reason of ['private-key', 'PASSED', {}, 1]) assert.throws(() => safeScreenshotEvidence(value(reason), true));
});

test('both MOCK and live workflows bind source and download to the single reviewed product PIN', () => {
  for (const job of [workflow.jobs.offline, workflow.jobs['real-ai']]) {
    const source = job.steps.filter(step => step.uses?.startsWith('actions/checkout@') && step.with?.path === '.ai-product-source');
    assert.equal(source.length, 1); assert.equal(source[0].with.ref, PIN.commit);
    assert.equal(job.env.AI_PRODUCT_SOURCE, '${{ github.workspace }}/.ai-product-source');
    const safety = job.steps.findIndex(step => step.run?.includes('npm.cmd test --prefix desktop/real-ai-validation'));
    assert.ok(safety >= 0 && job.steps.indexOf(source[0]) < safety, 'PIN checkout must precede every safety test');
    assert.equal(source[0].if, undefined); assert.equal(source[0]['continue-on-error'], undefined);
    assert.equal(source[0].with['persist-credentials'], false);
    const download = job.steps.filter(step => step.uses?.startsWith('actions/download-artifact@'));
    assert.equal(download.length, 1); assert.equal(download[0].with.repository, PIN.repository);
    assert.equal(download[0].with['run-id'], PIN.runId); assert.equal(download[0].with.name, PIN.artifactName);
  }
});

test('AI install source comparison consumes the shared complete desktop module closure', async () => {
  const { DESKTOP_SOURCE_FILES, AUDITED_DESKTOP_PRODUCT } = await import('../../installed-ui/desktop-source-manifest.mjs');
  assert.equal(AUDITED_DESKTOP_PRODUCT, PIN.commit);
  assert.equal(DESKTOP_SOURCE_FILES.length, 12);
  assert.equal(new Set(DESKTOP_SOURCE_FILES).size, 12);
  const prepare = await fs.readFile(new URL('prepare.mjs', here), 'utf8');
  assert.match(prepare, /import \{ DESKTOP_SOURCE_FILES \} from '\.\.\/installed-ui\/desktop-source-manifest\.mjs'/);
  assert.match(prepare, /for \(const name of DESKTOP_SOURCE_FILES\)/);
  assert.match(prepare, /normalizeSourceText\(extractFile[\s\S]*?normalizeSourceText\(await fs\.readFile/);
});

test('renderer structure evidence is MOCK Pilot-only and cannot widen LIVE artifacts', () => {
  const result = rows();
  const renderer = { status: 'UNSAFE', reason: 'OPACITY_ZERO', rootCount: 1, bodyCount: 1,
    assistantCount: 1, documentVisible: true, rootVisible: true, bodyVisible: false,
    generationMatches: true, conversationMatches: true, content: 'private-raw-body',
    url: 'private-origin', generation: 999, conversationId: 888 };
  for (const row of result) row.diagnostic = { pilotProof: {
    fault: 'RENDERER_UNPROVEN', rendererOwner: renderer, rendererHaru: renderer,
  } };
  const mock = safeResults(result, true), live = safeResults(result, false);
  assert.equal(mock[1].diagnostic.pilotProof.rendererOwner.reason, 'OPACITY_ZERO');
  assert.equal(mock[1].diagnostic.pilotProof.rendererHaru.bodyVisible, false);
  assert.ok(mock.filter(row => row.id !== 'pilot-stream').every(row => !('rendererOwner' in row.diagnostic.pilotProof)));
  assert.ok(live.every(row => !('rendererOwner' in row.diagnostic.pilotProof) && !('rendererHaru' in row.diagnostic.pilotProof)));
  for (const output of [mock, live]) for (const secret of ['private-raw-body', 'private-origin', '999', '888'])
    assert.equal(JSON.stringify(output).includes(secret), false);
});

test('free recovery probe is explicit, fresh, separate and mandatory after unchanged full MOCK suite', async () => {
  const steps = workflow.jobs.offline.steps;
  const normal = steps.findIndex(step => step.run?.includes('offline.mjs run\n'));
  const prepare = steps.findIndex(step => step.run?.includes('offline.mjs prepare-recovery'));
  const run = steps.findIndex(step => step.run?.includes('offline.mjs run-recovery'));
  assert.ok(normal > 0 && prepare > normal && run > prepare);
  for (const index of [prepare, run]) {
    assert.equal(steps[index].if, undefined); assert.equal(steps[index]['continue-on-error'], undefined);
    assert.equal(steps[index].env.AI_EVIDENCE_DIR, '${{ github.workspace }}/desktop/real-ai-validation/mock-recovery-evidence');
    assert.ok(steps[index].run.includes('if ($LASTEXITCODE -ne 0)'));
  }
  const uploads = steps.filter(step => step.uses?.startsWith('actions/upload-artifact@'));
  assert.equal(uploads.length, 2);
  assert.deepEqual(uploads[1].with.path.trim().split('\n'), [
    'desktop/real-ai-validation/mock-recovery-evidence/result.json',
    'desktop/real-ai-validation/mock-recovery-evidence/ledger.json',
  ]);
  assert.notEqual(uploads[0].with.name, uploads[1].with.name);
  const offline = await fs.readFile(new URL('../offline.mjs', import.meta.url), 'utf8');
  assert.match(offline, /run-recovery[\s\S]*?mode: 'mock', scope: RECOVERY.scope, brokerFactory: createOfflineRecoveryBroker/);
  const runner = await fs.readFile(new URL('../validation-runner.mjs', import.meta.url), 'utf8');
  assert.match(runner, /mode !== 'live' \|\| scope === RECOVERY.scope/);
  assert.match(runner, /prepared.scope === scope/);
  assert.match(runner, /await loadPreparedCarryForward\(prepared\)/);
  assert.match(runner, /ledger = recoveryBaselineSnapshot\(carryForward, binding\)/);
  assert.match(runner, /recovery \? RECOVERY.executionMs : 600000/);
  assert.match(runner, /executionStartedAt \} : \{\}/);
  assert.match(runner, /refreshSyntheticProfile\(page, broker, fixture, deadlineMs, \{ mode, scope \}\)/);
  assert.match(runner, /capture, mode, scope, mockContinuation, mockPilotProof/);
  assert.equal(/deadlineMs: started \+ 600000/.test(runner), false);
  assert.match(runner, /'PILOT_HARU_RECOVERY_PASSED', 'PILOT_HARU_RECOVERY_COMPLETION_ONLY'\]\.includes\(report.code\)/);
});

test('runner rejects full LIVE scope and recovery-only injection misuse before any environment action', async () => {
  const { executeValidation } = await import('../validation-runner.mjs');
  const { RECOVERY } = await import('../carry-forward-policy.cjs');
  const brokerFactory = () => { throw new Error('must not create broker'); };
  for (const options of [
    { mode: 'live', scope: 'full-suite' }, { mode: 'live', scope: 'unreviewed-scope' },
    { mode: 'live', mockPilotProofFactory: () => {} },
    { mode: 'mock', scope: RECOVERY.scope },
    { mode: 'mock', scope: RECOVERY.scope, mockPilotProofFactory: () => {}, screenshotFactory: () => {} },
    { mode: 'mock', scope: RECOVERY.scope, mockPilotProofFactory: () => {}, mockHaruSessionFactory: () => {} },
    { mode: 'mock', scope: RECOVERY.scope, mockPilotProofFactory: () => {}, mockContinuation: () => {} },
  ]) await assert.rejects(executeValidation({ brokerFactory, ...options }), { safeCode: 'INVALID_HARNESS' });
});
