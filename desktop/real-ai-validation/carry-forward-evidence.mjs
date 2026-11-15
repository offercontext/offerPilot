// Fixed-source read-only verification. Never accept a caller-selected source URL,
// artifact ID, digest or ledger. This module runs before provider key injection.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import { RECOVERY, verifyPriorZip } from './carry-forward-policy.cjs';
import { PIN, WORKFLOW, PAID_JOB, demand } from './contract.mjs';
import { unlinkedFile } from '../installed-ui/filesystem-safety.mjs';
const fail = code => { throw Object.assign(new Error(code), { safeCode: code }); };
const sourceCode = 'CARRY_FORWARD_SOURCE_UNVERIFIED';
const preparedCode = 'CARRY_FORWARD_PREPARED_INVALID';
const complete = (payload, key) => Number.isSafeInteger(payload?.total_count) && payload.total_count > 0 && payload.total_count <= 100 &&
  Array.isArray(payload[key]) && payload.total_count === payload[key].length;
export function validatePriorMetadata(run, jobs, artifact, artifacts) {
  try {
    const source = RECOVERY.source;
    assert.equal(run?.id, source.runId); assert.equal(run.path, WORKFLOW); assert.equal(run.head_sha, source.requestCommit);
    assert.equal(run.head_branch, PIN.branch); assert.equal(run.event, 'push'); assert.equal(run.run_attempt, 1);
    assert.equal(run.status, 'completed'); assert.equal(run.conclusion, 'failure');
    assert.equal(run.repository?.full_name, PIN.repository); assert.equal(run.head_repository?.full_name, PIN.repository);
    assert.ok(Number.isSafeInteger(run.repository.id) && run.repository.id > 0); assert.equal(run.repository.id, run.head_repository.id);
    assert.ok(complete(jobs, 'jobs'));
    assert.equal(new Set(jobs.jobs.map(job => job.id)).size, jobs.jobs.length);
    for (const job of jobs.jobs) {
      assert.ok(Number.isSafeInteger(job.id) && job.id > 0); assert.equal(job.run_id, run.id);
      assert.equal(job.head_sha, run.head_sha); assert.equal(job.status, 'completed');
    }
    const paid = jobs.jobs.filter(job => job.name === PAID_JOB); assert.equal(paid.length, 1); assert.equal(paid[0].conclusion, 'failure');
    assert.ok(Array.isArray(paid[0].steps));
    const upload = paid[0].steps.filter(step => step.name === RECOVERY.sourceUploadStep);
    assert.equal(upload.length, 1); assert.equal(upload[0].status, 'completed'); assert.equal(upload[0].conclusion, 'success');
    function validateArtifact(item) {
      assert.equal(item?.id, source.artifactId); assert.equal(item.name, RECOVERY.sourceArtifactName);
      assert.equal(item.expired, false); assert.equal(item.digest, `sha256:${source.digest}`);
      assert.ok(Number.isSafeInteger(item.size_in_bytes) && item.size_in_bytes > 0 && item.size_in_bytes <= RECOVERY.maxZipBytes);
      assert.equal(item.workflow_run?.id, run.id); assert.equal(item.workflow_run.head_sha, source.requestCommit);
      assert.equal(item.workflow_run.head_branch, PIN.branch); assert.equal(item.workflow_run.repository_id, run.repository.id);
      assert.equal(item.workflow_run.head_repository_id, run.head_repository.id);
    }
    validateArtifact(artifact); assert.ok(complete(artifacts, 'artifacts'));
    assert.equal(new Set(artifacts.artifacts.map(item => item.id)).size, artifacts.artifacts.length);
    const selected = artifacts.artifacts.filter(item => item.name === RECOVERY.sourceArtifactName);
    assert.equal(selected.length, 1); validateArtifact(selected[0]); assert.equal(selected[0].size_in_bytes, artifact.size_in_bytes);
    return source;
  } catch { fail(sourceCode); }
}
async function boundedBytes(response, signal) {
  assert.equal(response.ok, true); assert.ok(response.body);
  const length = response.headers.get('content-length');
  if (length !== null) assert.ok(/^\d+$/.test(length) && Number(length) > 0 && Number(length) <= RECOVERY.maxZipBytes);
  const reader = response.body.getReader(), chunks = []; let total = 0;
  let abort;
  const aborted = new Promise((_, reject) => { abort = () => reject(new Error(sourceCode)); signal.addEventListener('abort', abort, { once: true }); });
  try {
    while (true) {
      signal.throwIfAborted(); const { done, value } = await Promise.race([reader.read(), aborted]); if (done) break;
      total += value.byteLength; assert.ok(total <= RECOVERY.maxZipBytes); chunks.push(Buffer.from(value));
    }
    assert.ok(total > 0); if (length !== null) assert.equal(total, Number(length)); return Buffer.concat(chunks);
  } finally {
    signal.removeEventListener('abort', abort);
    // Cancellation is cleanup, never a new unbounded wait after the deadline.
    // Observe a rejection, but a stalled storage stream must not hold preflight.
    try { void reader.cancel().catch(() => {}); } catch { /* No raw stream errors. */ }
  }
}
export async function downloadPriorZip(token, fetchImpl = fetch) {
  try {
    assert.ok(typeof token === 'string' && token.length > 0);
    const signal = AbortSignal.timeout(20000);
    const response = await fetchImpl(`https://api.github.com/repos/${PIN.repository}/actions/artifacts/${RECOVERY.source.artifactId}/zip`, {
      method: 'GET', redirect: 'manual', signal,
      headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${token}`, 'X-GitHub-Api-Version': '2022-11-28' },
    });
    let download = response;
    if (response.status === 302) {
      const location = new URL(response.headers.get('location'));
      assert.ok(location.protocol === 'https:' && !location.username && !location.password && !location.port && !location.hash);
      assert.ok(/^[a-z0-9-]+\.blob\.core\.windows\.net$/i.test(location.hostname) || /^[a-z0-9-]+\.actions\.githubusercontent\.com$/i.test(location.hostname));
      // The original Authorization is deliberately absent on signed storage.
      download = await fetchImpl(location.href, { method: 'GET', redirect: 'error', signal, headers: { Accept: 'application/zip' } });
    } else assert.equal(response.status, 200);
    const bytes = await boundedBytes(download, signal); verifyPriorZip(bytes); return bytes;
  } catch { fail(sourceCode); } // Never include signed URLs, response bodies or raw network errors.
}
export async function verifyHistoricalSource({ read, download }) {
  const source = RECOVERY.source;
  const [run, jobs, artifact, artifacts] = await Promise.all([
    read(`actions/runs/${source.runId}`), read(`actions/runs/${source.runId}/jobs?filter=all&per_page=100`),
    read(`actions/artifacts/${source.artifactId}`), read(`actions/runs/${source.runId}/artifacts?per_page=100`),
  ]);
  validatePriorMetadata(run, jobs, artifact, artifacts);
  const priorZip = await download(); const carryForward = verifyPriorZip(priorZip);
  demand(priorZip.length === artifact.size_in_bytes, sourceCode);
  return { run, jobs, carryForward, priorZip };
}
const currentProduct = () => ({ commit: PIN.commit, buildCommit: PIN.buildCommit, runId: PIN.runId, artifactId: PIN.artifactId,
  installerSha256: PIN.installerSha256, fullRegressionRunId: PIN.fullRegressionRunId });
export function preparedCarryForward(prepared) {
  demand(prepared?.mode === 'live' && prepared.scope === RECOVERY.scope && typeof prepared.root === 'string' && path.isAbsolute(prepared.root) &&
    /^[0-9a-f]{40}$/.test(prepared.helperSha || '') && /^[0-9a-f]{40}$/.test(prepared.requestSha || '') &&
    prepared.helperSha !== prepared.requestSha, preparedCode);
  return { scope: RECOVERY.scope, sourceRunId: RECOVERY.source.runId, artifactId: RECOVERY.source.artifactId,
    digest: RECOVERY.source.digest, zipPath: path.join(prepared.root, 'prior-paid-evidence.zip'),
    helperSha: prepared.helperSha, requestSha: prepared.requestSha, currentProduct: currentProduct() };
}
export async function loadPreparedCarryForward(prepared) {
  let handle;
  try {
    const binding = preparedCarryForward(prepared); assert.deepEqual(prepared.carryForward, binding);
    // Native identity accepts Windows 8.3/case aliases while rejecting linked
    // roots, any linked ancestor, and file substitution. Never trust string-only
    // realpath equality on hosted Windows runner temporary paths.
    const filename = unlinkedFile(prepared.root, 'prior-paid-evidence.zip');
    const info = await fs.lstat(filename, { bigint: true });
    assert.ok(info.isFile() && !info.isSymbolicLink() && info.ino > 0n && info.dev >= 0n && info.size > 0n && info.size <= BigInt(RECOVERY.maxZipBytes));
    handle = await fs.open(filename, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
    const opened = await handle.stat({ bigint: true });
    assert.ok(opened.isFile() && opened.dev === info.dev && opened.ino === info.ino && opened.size === info.size);
    // Read at most the maximum plus one byte even if the file changes after stat.
    const bytes = Buffer.alloc(RECOVERY.maxZipBytes + 1); let offset = 0;
    while (offset < bytes.length) { const result = await handle.read(bytes, offset, bytes.length - offset, offset); if (result.bytesRead === 0) break; offset += result.bytesRead; }
    assert.equal(offset, Number(info.size)); assert.ok(offset <= RECOVERY.maxZipBytes);
    return verifyPriorZip(bytes.subarray(0, offset));
  } catch { fail(preparedCode); }
  finally { if (handle) await handle.close(); }
}
