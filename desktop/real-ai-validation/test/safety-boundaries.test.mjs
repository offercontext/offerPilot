import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { parse } from 'yaml';
import { CASES, PIN } from '../contract.mjs';
import { validateSyntheticRoute } from '../synthetic-api.mjs';
import { safeResults, numericLedger, saveEvidence } from '../safe-evidence.mjs';
import { githubReader } from '../github-read.mjs';
const here = new URL('../', import.meta.url);
const workflow = parse(await fs.readFile(new URL('../../.github/workflows/desktop-real-ai.yml', here), 'utf8'));
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
    apiKey: 'fake-secret', provenance: { productCommit: PIN.commit, buildRunId: String(PIN.runId), artifactId: String(PIN.artifactId), installerSha256: PIN.installerSha256, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), runId: '1', runAttempt: 1 }, requests: [{ caseId: 'connection', status: 'SETTLED', envelope: 'UNCHANGED', cap: 64,
      micro: 20, outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: false, promptTokens: 2, completionTokens: 2, cacheHitTokens: 0, cacheMissTokens: 2, raw: 'fake-secret' }] };
  const text = JSON.stringify(numericLedger(snapshot));
  assert.equal(text.includes('fake-secret'), false); assert.equal(text.includes('privateName123'), false);
  assert.equal(numericLedger(snapshot).requests[0].cap, 64);
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
    provenance: { productCommit: PIN.commit, buildRunId: String(PIN.runId), artifactId: String(PIN.artifactId), installerSha256: PIN.installerSha256, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), runId: '1', runAttempt: 1 },
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
