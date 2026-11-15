import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { RECOVERY, validateCarryForward } from '../carry-forward-policy.cjs';
import { PIN, WORKFLOW, PAID_JOB } from '../contract.mjs';
import { validatePriorMetadata, downloadPriorZip, verifyHistoricalSource, preparedCarryForward, loadPreparedCarryForward } from '../carry-forward-evidence.mjs';
const zip = () => fs.readFile(new URL('./fixtures/prior-paid-38694575612.zip', import.meta.url));
import { sourceMetadata } from './fixtures/recovery-metadata.mjs';
const validate = f => validatePriorMetadata(f.run, f.jobs, f.artifact, f.artifacts);
test('exact completed failed paid source requires a successful evidence upload and complete metadata', () => {
  assert.equal(validate(sourceMetadata()).runId, RECOVERY.source.runId);
  for (const change of [f => f.run.id++, f => f.run.run_attempt++, f => { f.run.conclusion = 'success'; },
    f => { f.run.repository.full_name = 'other/repo'; }, f => { f.run.head_sha = PIN.commit; },
    f => { f.jobs.jobs[0].conclusion = 'skipped'; }, f => { f.jobs.jobs[0].steps[0].conclusion = 'failure'; },
    f => { f.jobs.jobs[0].steps = []; }, f => f.jobs.jobs.push(f.jobs.jobs[0]), f => f.jobs.total_count++,
    f => { f.artifact.expired = true; }, f => f.artifact.id++, f => { f.artifact.digest = 'sha256:' + '0'.repeat(64); },
    f => { f.artifact.workflow_run.head_sha = PIN.commit; }, f => { f.artifacts.artifacts[0].expired = true; },
    f => f.artifacts.total_count++, f => { f.artifact.size_in_bytes = 65537; }]) {
    const f = sourceMetadata(); change(f); assert.throws(() => validate(f), { safeCode: 'CARRY_FORWARD_SOURCE_UNVERIFIED' });
  }
});
test('source retrieval has only fixed routes and imports original ZIP after metadata verification', async () => {
  const f = sourceMetadata(), seen = [];
  const map = new Map([[`actions/runs/${RECOVERY.source.runId}`, f.run], [`actions/runs/${RECOVERY.source.runId}/jobs?filter=all&per_page=100`, f.jobs],
    [`actions/artifacts/${RECOVERY.source.artifactId}`, f.artifact], [`actions/runs/${RECOVERY.source.runId}/artifacts?per_page=100`, f.artifacts]]);
  const result = await verifyHistoricalSource({ read: async route => { seen.push(route); assert.ok(map.has(route)); return map.get(route); }, download: zip });
  assert.equal(validateCarryForward(result.carryForward, 'live').settledMicroCny, 188); assert.deepEqual(result.priorZip, await zip());
  assert.equal(seen.length, 4);
  f.run.run_attempt = 2; let downloaded = false;
  await assert.rejects(verifyHistoricalSource({ read: async route => map.get(route), download: () => { downloaded = true; } }));
  assert.equal(downloaded, false);
});
test('ZIP download bounds raw bytes, follows only approved HTTPS storage, and never forwards authorization', async () => {
  const calls = [], bytes = await zip();
  const fetchImpl = async (url, options) => { calls.push({ url, options });
    return calls.length === 1 ? new Response(null, { status: 302, headers: { location: 'https://productionresultssa1.blob.core.windows.net/actions/zip?sig=private' } })
      : new Response(bytes, { status: 200, headers: { 'content-length': String(bytes.length) } }); };
  assert.deepEqual(await downloadPriorZip('fake-github-token', fetchImpl), bytes);
  assert.equal(calls[0].url, `https://api.github.com/repos/${PIN.repository}/actions/artifacts/${RECOVERY.source.artifactId}/zip`);
  assert.equal(calls[0].options.redirect, 'manual'); assert.match(calls[0].options.headers.Authorization, /^Bearer /);
  assert.equal(Object.hasOwn(calls[1].options.headers, 'Authorization'), false); assert.equal(calls[1].options.redirect, 'error');
  assert.equal(calls[0].options.signal, calls[1].options.signal);
  for (const target of ['http://productionresultssa1.blob.core.windows.net/file', 'https://evil.test/file', 'https://api.github.com/elsewhere',
    'https://token@productionresultssa1.blob.core.windows.net/file', 'https://productionresultssa1.blob.core.windows.net:444/file']) {
    let count = 0;
    await assert.rejects(downloadPriorZip('token', async () => { count++; return new Response(null, { status: 302, headers: { location: target } }); }), { safeCode: 'CARRY_FORWARD_SOURCE_UNVERIFIED' });
    assert.equal(count, 1);
  }
  for (const response of [new Response('private failure body', { status: 500 }), new Response(Buffer.alloc(65537)),
    new Response(bytes, { headers: { 'content-length': '65537' } }), new Response(Buffer.alloc(10))])
    await assert.rejects(downloadPriorZip('token', async () => response), error => !error.message.includes('private'));
});
test('prepared import binds fresh owned ZIP to current H/R/product and rejects substitution or symlinks', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-carry-'));
  const prepared = { schema: 1, mode: 'live', scope: RECOVERY.scope, root, helperSha: 'a'.repeat(40), requestSha: 'b'.repeat(40) };
  try {
    prepared.carryForward = preparedCarryForward(prepared);
    await fs.writeFile(prepared.carryForward.zipPath, await zip(), { flag: 'wx' });
    assert.equal(validateCarryForward(await loadPreparedCarryForward(prepared), 'live').sentRequests, 1);
    for (const mutate of [p => { p.carryForward.digest = '0'.repeat(64); }, p => { p.helperSha = 'c'.repeat(40); },
      p => { p.carryForward.currentProduct.commit = RECOVERY.source.productCommit; }, p => { p.carryForward.zipPath = path.join(root, '..', 'external.zip'); },
      p => { p.carryForward.extra = true; }, p => { p.mode = 'mock'; }, p => { p.scope = 'full-suite'; }]) {
      const copy = structuredClone(prepared); mutate(copy);
      await assert.rejects(loadPreparedCarryForward(copy), { safeCode: 'CARRY_FORWARD_PREPARED_INVALID' });
    }
    await fs.writeFile(prepared.carryForward.zipPath, Buffer.alloc(20));
    await assert.rejects(loadPreparedCarryForward(prepared));
    await fs.unlink(prepared.carryForward.zipPath);
    await fs.symlink(new URL('./fixtures/prior-paid-38694575612.zip', import.meta.url), prepared.carryForward.zipPath);
    await assert.rejects(loadPreparedCarryForward(prepared), { safeCode: 'CARRY_FORWARD_PREPARED_INVALID' });
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('prepared loader accepts native short-name aliases but never root or ancestor links', async t => {
  const { default: syncFs } = await import('node:fs');
  const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-carry-identity-'));
  const root = path.join(parent, 'owned evidence'), alias = path.join(parent, 'OWNED~1'); await fs.mkdir(root);
  const prepared = { mode: 'live', scope: RECOVERY.scope, root, helperSha: 'a'.repeat(40), requestSha: 'b'.repeat(40) };
  prepared.carryForward = preparedCarryForward(prepared);
  await fs.writeFile(prepared.carryForward.zipPath, await zip());
  try {
    const original = { native: syncFs.realpathSync.native, lstat: syncFs.lstatSync };
    const mapped = value => value === alias ? root : value;
    t.mock.method(syncFs.realpathSync, 'native', (value, ...args) => original.native(mapped(value), ...args));
    t.mock.method(syncFs, 'lstatSync', (value, ...args) => original.lstat(mapped(value), ...args));
    const aliased = { ...prepared, root: alias }; aliased.carryForward = preparedCarryForward(aliased);
    assert.equal((await loadPreparedCarryForward(aliased)).sentRequests, 1); t.mock.restoreAll();
    const rootLink = path.join(parent, 'root link'), ancestorLink = path.join(parent, 'ancestor link');
    const type = process.platform === 'win32' ? 'junction' : 'dir';
    await fs.symlink(root, rootLink, type); await fs.symlink(parent, ancestorLink, type);
    for (const linkedRoot of [rootLink, path.join(ancestorLink, 'owned evidence')]) {
      const linked = { ...prepared, root: linkedRoot }; linked.carryForward = preparedCarryForward(linked);
      await assert.rejects(loadPreparedCarryForward(linked), { safeCode: 'CARRY_FORWARD_PREPARED_INVALID' });
    }
  } finally { t.mock.restoreAll(); await fs.rm(parent, { recursive: true, force: true }); }
});

test('oversized download stays bounded even when stream cancellation hangs or rejects', async () => {
  for (const outcome of ['hang', 'reject']) {
    let cancelled = false;
    const response = new Response(new ReadableStream({
      start(controller) { controller.enqueue(Buffer.alloc(RECOVERY.maxZipBytes + 1)); },
      cancel() { cancelled = true; return outcome === 'hang' ? new Promise(() => {}) : Promise.reject(new Error('private stream cancellation failure')); },
    }));
    let timer;
    try {
      await Promise.race([
        assert.rejects(downloadPriorZip('token', async () => response), { safeCode: 'CARRY_FORWARD_SOURCE_UNVERIFIED' }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('download waited on cleanup')), 250); }),
      ]);
      assert.equal(cancelled, true); await new Promise(resolve => setImmediate(resolve));
    } finally { clearTimeout(timer); }
  }
});
