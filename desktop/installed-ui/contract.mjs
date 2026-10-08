import assert from 'node:assert/strict';
import path from 'node:path';

export const PIN = Object.freeze({
  schema: 2,
  repository: 'offercontext/offerPilot',
  branch: 'feat/20261005-windows-desktop-validation',
  commit: '2e78e9489e2c022b979fc2e580870975db2a8cce',
  buildCommit: '2e78e9489e2c022b979fc2e580870975db2a8cce',
  buildWorkflow: '.github/workflows/desktop-windows.yml',
  fullRegressionRunId: 37728772813,
  runId: 37728772813,
  artifactId: 11529527005,
  artifactName: 'offerpilot-windows-experimental-validation-2e78e9489e2c022b979fc2e580870975db2a8cce',
  artifactDigest: 'sha256:1c583341820b6a9158de5bb9dea3a90c943f2cfb2e4fe9a6cbe7d35967c1c058',
  installer: 'OfferPilot-Desktop-0.1.0-desktop.1-win-x64-setup.exe',
  installerSha256: '2d5b5cfe13a8bfa549ab1390c6c561080e4d1baa8e7d58136185c97cd4d1e2ff',
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
  for (const key of ['runId', 'fullRegressionRunId', 'artifactId']) assert.ok(Number.isSafeInteger(pin[key]) && pin[key] > 0);
  assert.match(pin.artifactDigest, /^sha256:[a-f0-9]{64}$/);
  assert.match(pin.installerSha256, /^[a-f0-9]{64}$/);
  assert.equal(pin.installer, 'OfferPilot-Desktop-0.1.0-desktop.1-win-x64-setup.exe');
  assert.ok(['.github/workflows/desktop-windows.yml', '.github/workflows/desktop-layout-retry.yml'].includes(pin.buildWorkflow));
  if (pin.buildWorkflow === '.github/workflows/desktop-windows.yml') {
    assert.equal(pin.buildCommit, pin.commit, 'ordinary build must use its product head');
    assert.equal(pin.fullRegressionRunId, pin.runId, 'ordinary build keeps the original full-gate provenance');
  } else {
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
  validateRunIdentity(fullRegressionRun, { id: pin.fullRegressionRunId, commit: pin.commit,
    workflow: '.github/workflows/desktop-windows.yml' }, pin);
  assert.equal(fullRegressionRun.repository.id, run.repository.id);
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
  // Full regression remains independent: checking its identity is not certifying its result.
  return { runId: pin.runId, artifactId: pin.artifactId, commit: pin.commit,
    buildCommit: pin.buildCommit, buildWorkflow: pin.buildWorkflow,
    fullRegressionRunId: pin.fullRegressionRunId, digest: pin.artifactDigest,
    packaging: 'success', fullRegression: 'not-certified-by-this-job' };
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
