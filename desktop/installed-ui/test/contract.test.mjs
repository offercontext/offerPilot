import test from 'node:test';
import assert from 'node:assert/strict';
import { PIN, SYNTHETIC, validateRequest, validateMetadata, publicApplication,
  selectOwnedProcesses, validateListeners, validateSecurity } from '../contract.mjs';
const clone = (value) => structuredClone(value);
function metadata() {
  const repo = { id: 123, full_name: PIN.repository };
  const run = { id: PIN.runId, head_sha: PIN.commit, head_branch: PIN.branch,
    path: '.github/workflows/desktop-windows.yml', event: 'push', repository: repo, head_repository: repo,
    status: 'in_progress', conclusion: null };
  const artifact = { id: PIN.artifactId, name: PIN.artifactName, expired: false, digest: PIN.artifactDigest,
    workflow_run: { id: PIN.runId, head_sha: PIN.commit, head_branch: PIN.branch, repository_id: 123, head_repository_id: 123 } };
  return [run, artifact, { total_count: 1, artifacts: [clone(artifact)] },
    { total_count: 2, jobs: [{ name: 'Experimental installer and desktop smoke', status: 'completed', conclusion: 'success' },
      { name: 'Full release regression (required for release)', status: 'in_progress', conclusion: null }] }];
}
test('request rejects missing values, any different pin and arbitrary input keys', () => {
  assert.deepEqual(validateRequest(clone(PIN)), PIN);
  assert.throws(() => validateRequest(undefined));
  for (const key of Object.keys(PIN)) {
    const changed = clone(PIN); changed[key] = 'different'; assert.throws(() => validateRequest(changed));
    const missing = clone(PIN); delete missing[key]; assert.throws(() => validateRequest(missing));
  }
  assert.throws(() => validateRequest({ ...PIN, url: 'https://example.com/installer.exe' }));
});
test('source verification accepts pending full regression without claiming its pass', () => {
  assert.equal(validateMetadata(...metadata()).fullRegression, 'not-certified-by-this-job');
});
test('artifact/run/job mismatch and incomplete listings fail closed', () => {
  for (const mutate of [
    (x) => { x[0].head_sha = 'bad'; }, (x) => { x[0].head_branch = 'master'; },
    (x) => { x[0].head_repository.full_name = 'fork/repo'; }, (x) => { x[0].path = 'other.yml'; },
    (x) => { x[1].id++; }, (x) => { x[1].expired = true; }, (x) => { x[1].digest = 'bad'; },
    (x) => { x[1].workflow_run.id++; }, (x) => { x[1].workflow_run.head_repository_id++; },
    (x) => { x[2].artifacts[0].id++; }, (x) => { x[2].total_count++; },
    (x) => { x[2].artifacts.push(clone(x[1])); x[2].total_count++; },
    (x) => { x[3].jobs[0].conclusion = 'failure'; }, (x) => { x[3].jobs[0].status = 'in_progress'; },
    (x) => { x[3].total_count++; },
  ]) { const values = metadata(); mutate(values); assert.throws(() => validateMetadata(...values)); }
});
test('UI response evidence contains only the approved synthetic fields', () => {
  assert.deepEqual(publicApplication({ id: 42, ...SYNTHETIC, token: 'must-not-appear', provider: 'must-not-appear' }), { id: 42, ...SYNTHETIC });
  for (const id of [0, -1, '42', null, 1.5]) assert.throws(() => publicApplication({ id, ...SYNTHETIC }));
  assert.throws(() => publicApplication({ id: 42, ...SYNTHETIC, notes: 'unapproved content' }));
});
test('backend ownership requires real main PID, exact installed path and direct parent', () => {
  const exe = 'C:\\临时 UI\\OfferPilot Desktop.exe';
  const backend = 'C:\\临时 UI\\resources\\backend\\offerpilot-backend.exe';
  const main = { pid: 101, parentPid: 100, path: exe, created: 'one' };
  const child = { pid: 102, parentPid: 101, path: backend, created: 'two' };
  assert.deepEqual(selectOwnedProcesses([main, child], 101, exe.toLowerCase(), backend), { main, backend: child });
  for (const changed of [{ ...child, parentPid: 100 }, { ...child, path: 'C:\\other.exe' }, { ...child, created: null }]) {
    assert.throws(() => selectOwnedProcesses([main, changed], 101, exe, backend));
  }
  assert.throws(() => selectOwnedProcesses([main, child], 100, exe, backend), 'shell process PID is not the actual Electron PID');
  assert.throws(() => selectOwnedProcesses([main, child, { ...child, pid: 103 }], 101, exe, backend));
});
test('debug and backend listeners must remain loopback and match the saved port', () => {
  const listeners = [{ pid: 101, address: '127.0.0.1', port: 4001 }, { pid: 101, address: '::1', port: 4002 },
    { pid: 102, address: '127.0.0.1', port: 4003 }];
  assert.equal(validateListeners(listeners, 101, 102, 4003).port, 4003);
  assert.throws(() => validateListeners([{ ...listeners[0], address: '0.0.0.0' }, ...listeners.slice(1)], 101, 102, 4003));
  assert.throws(() => validateListeners(listeners, 101, 102, 4004));
  assert.throws(() => validateListeners(listeners.slice(1), 101, 102, 4003));
});
test('each packaged security property and unsafe switch is fail closed', () => {
  const security = { packaged: true, nodeIntegration: false, contextIsolation: true, sandbox: true,
    webSecurity: true, devToolsOpened: false, unsafeSwitches: [],
    devToolsProbe: { beforeOpen: false, beforeContents: false, openedEvent: false, afterOpen: false, afterContents: false } };
  validateSecurity(security);
  for (const key of Object.keys(security).filter((key) => !['unsafeSwitches', 'devToolsProbe'].includes(key))) {
    assert.throws(() => validateSecurity({ ...security, [key]: !security[key] }));
  }
  assert.throws(() => validateSecurity({ ...security, unsafeSwitches: ['no-sandbox'] }));
  assert.throws(() => validateSecurity({ ...security, devTools: true }));
  assert.throws(() => validateSecurity({ ...security, devToolsProbe: undefined }));
  for (const key of Object.keys(security.devToolsProbe)) {
    for (const value of [true, undefined, null, 'false']) {
      assert.throws(() => validateSecurity({ ...security, devToolsProbe: { ...security.devToolsProbe, [key]: value } }));
    }
  }
});
