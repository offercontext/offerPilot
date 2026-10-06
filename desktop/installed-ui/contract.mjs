import assert from 'node:assert/strict';
import path from 'node:path';

export const PIN = Object.freeze({
  schema: 1,
  repository: 'offercontext/offerPilot',
  branch: 'feat/20261005-windows-desktop-validation',
  commit: '744fce4ab3bdde1b6a4aa8accd9e626b306c6d74',
  runId: 37454260377,
  artifactId: 11409501649,
  artifactName: 'offerpilot-windows-experimental-validation-744fce4ab3bdde1b6a4aa8accd9e626b306c6d74',
  artifactDigest: 'sha256:cd58641cf668d71e26ceabe0b290194c72d490038da9037297df1b2a17cfd375',
  installer: 'OfferPilot-Desktop-0.1.0-desktop.1-win-x64-setup.exe',
  installerSha256: '371a416d5566bbdd33f8b28fd1a3514350972cddb5bb02bc406c8915286a320d',
});
export const SYNTHETIC = Object.freeze({
  company_name: '桌面验收中文公司',
  position_name: '本地持久化测试岗位',
  notes: '仅用于安装界面验证：中文、空格与重启保存。无真实求职材料。',
  status: 'pending',
});
export function validateRequest(request) {
  assert.deepEqual(request, PIN, 'request must exactly equal the reviewed pin');
  return PIN;
}
export function validateMetadata(run, artifact, artifacts, jobs) {
  assert.equal(run.id, PIN.runId);
  assert.equal(run.head_sha, PIN.commit);
  assert.equal(run.head_branch, PIN.branch);
  assert.equal(run.path, '.github/workflows/desktop-windows.yml');
  assert.equal(run.event, 'push');
  assert.equal(run.repository?.full_name, PIN.repository);
  assert.equal(run.head_repository?.full_name, PIN.repository);
  assert.equal(artifact.id, PIN.artifactId);
  assert.equal(artifact.name, PIN.artifactName);
  assert.equal(artifact.expired, false);
  assert.equal(artifact.digest, PIN.artifactDigest);
  assert.equal(artifact.workflow_run?.id, PIN.runId);
  assert.equal(artifact.workflow_run?.head_sha, PIN.commit);
  assert.equal(artifact.workflow_run?.head_branch, PIN.branch);
  assert.equal(artifact.workflow_run?.repository_id, run.repository?.id);
  assert.equal(artifact.workflow_run?.head_repository_id, run.head_repository?.id);
  assert.equal(artifacts.total_count, artifacts.artifacts.length, 'artifact listing must be complete');
  const selected = artifacts.artifacts.filter((item) => item.name === PIN.artifactName);
  assert.equal(selected.length, 1);
  assert.equal(selected[0].id, PIN.artifactId);
  assert.equal(selected[0].digest, PIN.artifactDigest);
  assert.equal(jobs.total_count, jobs.jobs.length, 'job listing must be complete');
  const packaging = jobs.jobs.filter((job) => job.name === 'Experimental installer and desktop smoke');
  assert.equal(packaging.length, 1);
  assert.equal(packaging[0].status, 'completed');
  assert.equal(packaging[0].conclusion, 'success');
  // Full regression deliberately remains independent: never infer its result here.
  return { runId: PIN.runId, artifactId: PIN.artifactId, commit: PIN.commit,
    digest: PIN.artifactDigest, packaging: 'success', fullRegression: 'not-certified-by-this-job' };
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
