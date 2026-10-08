import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { parse } from 'yaml';
import { CASES } from '../contract.mjs';
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
test('numeric ledger does not serialize arbitrary fields or credential-shaped strings', () => {
  const reasons = 'AUTH ROUTE CLOSED DEADLINE UNARMED BUSY CASE BUDGET COUNT BODY MODEL PARAMETER CANCELLED DISCONNECT TIMEOUT LEDGER UPSTREAM REDIRECT PROTOCOL USAGE SETTLED EXPIRED UPSTREAM_DISCONNECT'.split(' ');
  const snapshot = { model: 'deepseek-flash', budgetMicroCny: 10000000, reserveMicroCny: 3000000, sentRequests: 1, settledMicroCny: 20,
    retainedMicroCny: 0, active: false, closed: true, journalFailed: false,
    denied: Object.fromEntries(reasons.map(key => [key, 0])), privateName123: 111,
    apiKey: 'fake-secret', provenance: { helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), runId: '1', runAttempt: 1 }, requests: [{ caseId: 'connection', status: 'SETTLED', envelope: 'UNCHANGED', cap: 64,
      micro: 20, outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: false, promptTokens: 2, completionTokens: 2, cacheHitTokens: 0, cacheMissTokens: 2, raw: 'fake-secret' }] };
  const text = JSON.stringify(numericLedger(snapshot));
  assert.equal(text.includes('fake-secret'), false); assert.equal(text.includes('privateName123'), false);
  assert.equal(numericLedger(snapshot).requests[0].cap, 64);
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
    provenance: { helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), runId: '1', runAttempt: 1 },
    denied: Object.fromEntries(reasons.map(key => [key, 0])),
    requests: CASES.map(caseId => ({ caseId, status: caseId === 'pilot-cancel' ? 'DISCONNECT' : 'SETTLED',
      envelope: 'UNCHANGED', cap: 64, micro: caseId === 'pilot-cancel' ? 3000000 : 20,
      outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: caseId === 'pilot-cancel' })) };
  try {
    await fs.writeFile(path.join(directory, 'result.json'), '{"status":"BLOCKED"}');
    await fs.mkdir(path.join(directory, 'ledger.json'));
    await assert.rejects(saveEvidence(directory, { status: 'PASS', code: 'ALL_UI_CASES_PASSED',
      scenarios: CASES.map(id => ({ id, status: 'PASS', code: 'PASSED', checks: {} })),
      cleanupPassed: true, cleanupCode: 'CLEANUP_PASSED' }, ledger));
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
