import assert from 'node:assert/strict';
import path from 'node:path';

export const PIN = Object.freeze({
  schema: 2,
  repository: 'offercontext/offerPilot',
  branch: 'feat/20261005-windows-desktop-validation',
  commit: '590291ce4e33407eb4f13f092298e7398aed394c',
  buildCommit: '590291ce4e33407eb4f13f092298e7398aed394c',
  buildWorkflow: '.github/workflows/desktop-windows.yml',
  fullRegressionRunId: null,
  runId: 37828103435,
  artifactId: 11573930091,
  artifactName: 'offerpilot-windows-experimental-validation-590291ce4e33407eb4f13f092298e7398aed394c',
  artifactDigest: 'sha256:ad5c3f7e2858f1b1c5339a2e95af16d7c0785b268382d170cd2b9ef7efd6debd',
  installer: 'OfferPilot-Desktop-0.1.0-desktop.1-win-x64-setup.exe',
  installerSha256: 'abaa504cef5a51ba7f3b4f1dddc1d24f889f350231a4dcb5b523e4f91b76f316',
});
export const SYNTHETIC = Object.freeze({
  company_name: '桌面验收中文公司',
  position_name: '本地持久化测试岗位',
  notes: '仅用于安装界面验证：中文、空格与重启保存。无真实求职材料。',
  status: 'pending',
});
export function validateRequest(request) {
  assert.deepEqual(request, PIN, 'request must exactly equal the reviewed pin');
  return validateReviewedPin(PIN);
}
// The production caller always supplies the reviewed constant PIN. The optional
// pin argument makes distinct-source/build test fixtures possible, not a runtime input.
export function validateReviewedPin(pin) {
  assert.deepEqual(Object.keys(pin).sort(), Object.keys(PIN).sort());
  assert.equal(pin.schema, 2);
  assert.equal(pin.repository, 'offercontext/offerPilot');
  assert.equal(pin.branch, 'feat/20261005-windows-desktop-validation');
  for (const key of ['commit', 'buildCommit']) assert.match(pin[key], /^[a-f0-9]{40}$/);
  for (const key of ['runId', 'artifactId']) assert.ok(Number.isSafeInteger(pin[key]) && pin[key] > 0);
  assert.ok(pin.fullRegressionRunId === null || (Number.isSafeInteger(pin.fullRegressionRunId) && pin.fullRegressionRunId > 0));
  assert.match(pin.artifactDigest, /^sha256:[a-f0-9]{64}$/);
  assert.match(pin.installerSha256, /^[a-f0-9]{64}$/);
  assert.equal(pin.installer, 'OfferPilot-Desktop-0.1.0-desktop.1-win-x64-setup.exe');
  assert.ok(['.github/workflows/desktop-windows.yml', '.github/workflows/desktop-layout-retry.yml'].includes(pin.buildWorkflow));
  if (pin.buildWorkflow === '.github/workflows/desktop-windows.yml') {
    assert.equal(pin.buildCommit, pin.commit, 'ordinary build must use its product head');
    if (pin.fullRegressionRunId !== null) {
      assert.equal(pin.fullRegressionRunId, pin.runId, 'ordinary build keeps the original full-gate provenance');
    }
  } else {
    assert.notEqual(pin.fullRegressionRunId, null, 'package-only applies only to the ordinary product build');
    assert.notEqual(pin.buildCommit, pin.commit, 'retry activation and product source must remain distinct');
    assert.notEqual(pin.fullRegressionRunId, pin.runId, 'retry cannot replace the independent full gate');
  }
  return pin;
}
function validateRunIdentity(run, { id, commit, workflow }, pin) {
  assert.equal(run.id, id);
  assert.equal(run.head_sha, commit);
  assert.equal(run.head_branch, pin.branch);
  assert.equal(run.path, workflow);
  assert.equal(run.event, 'push');
  assert.equal(run.repository?.full_name, pin.repository);
  assert.equal(run.head_repository?.full_name, pin.repository);
  assert.ok(Number.isSafeInteger(run.repository?.id) && run.repository.id > 0);
  assert.equal(run.head_repository?.id, run.repository.id);
}
export function validateMetadata(run, artifact, artifacts, jobs, fullRegressionRun, reviewedPin = PIN) {
  const pin = validateReviewedPin(reviewedPin);
  validateRunIdentity(run, { id: pin.runId, commit: pin.buildCommit, workflow: pin.buildWorkflow }, pin);
  if (pin.fullRegressionRunId === null) {
    assert.equal(fullRegressionRun, null, 'package-only cannot substitute a different product full gate');
    assert.equal(run.head_commit?.id, pin.buildCommit, 'package-only activation commit must equal the pinned product');
    assert.ok(/^build: AI \[windows-package-only\] /i.test(run.head_commit?.message || ''),
      'package-only requires the exact build commit activation prefix');
    assert.ok(/^build: AI \[windows-package-only\] /i.test(run.display_title || ''), 'package-only title must agree with activation');
    assert.equal(run.run_attempt, 1, 'package-only must use the original build attempt');
  } else {
    validateRunIdentity(fullRegressionRun, { id: pin.fullRegressionRunId, commit: pin.commit,
      workflow: '.github/workflows/desktop-windows.yml' }, pin);
    assert.equal(fullRegressionRun.repository.id, run.repository.id);
  }
  assert.equal(artifact.id, pin.artifactId);
  assert.equal(artifact.name, pin.artifactName);
  assert.equal(artifact.expired, false);
  assert.equal(artifact.digest, pin.artifactDigest);
  assert.equal(artifact.workflow_run?.id, pin.runId);
  assert.equal(artifact.workflow_run?.head_sha, pin.buildCommit);
  assert.equal(artifact.workflow_run?.head_branch, pin.branch);
  assert.equal(artifact.workflow_run?.repository_id, run.repository.id);
  assert.equal(artifact.workflow_run?.head_repository_id, run.head_repository.id);
  assert.equal(artifacts.total_count, artifacts.artifacts.length, 'artifact listing must be complete');
  const selected = artifacts.artifacts.filter((item) => item.name === pin.artifactName);
  assert.equal(selected.length, 1);
  assert.equal(selected[0].id, pin.artifactId);
  assert.equal(selected[0].digest, pin.artifactDigest);
  assert.equal(jobs.total_count, jobs.jobs.length, 'job listing must be complete');
  const packaging = jobs.jobs.filter((job) => job.name === 'Experimental installer and desktop smoke');
  assert.equal(packaging.length, 1);
  assert.equal(packaging[0].status, 'completed');
  assert.equal(packaging[0].conclusion, 'success');
  if (pin.fullRegressionRunId === null) {
    assert.equal(packaging[0].run_id, pin.runId, 'package-only packaging job must belong to the pinned run');
    assert.equal(packaging[0].head_sha, pin.buildCommit);
    for (const name of ['Collect complete pytest manifest', 'Complete pytest shard ${{ matrix.shard }} of 12',
      'Full release regression (required for release)']) {
      const selectedJobs = jobs.jobs.filter(job => job.name === name);
      assert.equal(selectedJobs.length, 1, 'package-only requires every exact skipped full-gate job');
      assert.equal(selectedJobs[0].run_id, pin.runId, 'package-only skipped gate job must belong to the pinned run');
      assert.equal(selectedJobs[0].head_sha, pin.buildCommit);
      assert.equal(selectedJobs[0].status, 'completed');
      assert.equal(selectedJobs[0].conclusion, 'skipped');
    }
    assert.equal(jobs.jobs.filter(job => job.name.startsWith('Complete pytest shard ')).length, 1,
      'package-only must not contain an expanded or mixed shard matrix');
  }
  // Full regression remains independent: checking its identity is not certifying its result.
  return { runId: pin.runId, artifactId: pin.artifactId, commit: pin.commit,
    buildCommit: pin.buildCommit, buildWorkflow: pin.buildWorkflow,
    fullRegressionRunId: pin.fullRegressionRunId, digest: pin.artifactDigest,
    packaging: 'success', ...(pin.fullRegressionRunId === null ? { buildScope: 'package-only' } : {}),
    fullRegression: pin.fullRegressionRunId === null ? 'not-run-package-only' : 'not-certified-by-this-job' };
}
export function publicApplication(value) {
  assert.ok(Number.isSafeInteger(value?.id) && value.id > 0, 'positive application ID required');
  const result = { id: value.id };
  for (const [key, expected] of Object.entries(SYNTHETIC)) {
    assert.equal(value[key], expected, `synthetic ${key} differs`);
    result[key] = value[key];
  }
  return result;
}
export function normalizedWindowsPath(value) { return path.win32.normalize(value).toLowerCase(); }
export function sameWindowsPath(left, right) { return normalizedWindowsPath(left) === normalizedWindowsPath(right); }
export function selectOwnedProcesses(processes, mainPid, exe, backendExe) {
  assert.ok(Number.isSafeInteger(mainPid) && mainPid > 0);
  const main = processes.find((item) => item.pid === mainPid && sameWindowsPath(item.path, exe));
  assert.ok(main && main.created, 'actual main PID/path must be independently visible in CIM');
  const backends = processes.filter((item) => item.parentPid === mainPid && sameWindowsPath(item.path, backendExe));
  assert.equal(backends.length, 1, 'exactly one direct installed backend child required');
  assert.ok(backends[0].created);
  return { main, backend: backends[0] };
}
export function validateListeners(listeners, mainPid, backendPid, port) {
  const main = listeners.filter((item) => item.pid === mainPid);
  const backend = listeners.filter((item) => item.pid === backendPid);
  assert.ok(main.length >= 2, 'temporary inspect and CDP listeners must be observable');
  assert.ok(main.every((item) => ['127.0.0.1', '::1'].includes(item.address)), 'debug listeners must be loopback');
  assert.equal(backend.length, 1, 'one backend listener required');
  assert.equal(backend[0].address, '127.0.0.1');
  assert.equal(backend[0].port, port);
  return { debugListenersLoopback: true, backendLoopback: true, port };
}
export function validateSecurity(security) {
  assert.equal(security.packaged, true);
  assert.equal(security.nodeIntegration, false);
  assert.equal(security.contextIsolation, true);
  assert.equal(security.sandbox, true);
  assert.equal(security.webSecurity, true);
  if (security.devTools !== undefined) assert.equal(security.devTools, false);
  assert.deepEqual(security.devToolsProbe, { beforeOpen: false, beforeContents: false,
    openedEvent: false, afterOpen: false, afterContents: false });
  assert.equal(security.devToolsOpened, false);
  assert.deepEqual(security.unsafeSwitches, []);
}
