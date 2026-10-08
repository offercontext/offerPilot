import test from 'node:test';
import assert from 'node:assert/strict';
import { PIN, SYNTHETIC, validateRequest, validateMetadata, validateReviewedPin, publicApplication,
  selectOwnedProcesses, validateListeners, validateSecurity } from '../contract.mjs';
const clone = (value) => structuredClone(value);
const fullPin = Object.freeze({ ...PIN, fullRegressionRunId: PIN.runId });
function metadata(pin = fullPin) {
  const repo = { id: 123, full_name: pin.repository };
  const run = { id: pin.runId, head_sha: pin.buildCommit, head_branch: pin.branch,
    path: pin.buildWorkflow, event: 'push', repository: repo, head_repository: repo,
    status: 'in_progress', conclusion: null };
  const artifact = { id: pin.artifactId, name: pin.artifactName, expired: false, digest: pin.artifactDigest,
    workflow_run: { id: pin.runId, head_sha: pin.buildCommit, head_branch: pin.branch, repository_id: 123, head_repository_id: 123 } };
  return [run, artifact, { total_count: 1, artifacts: [clone(artifact)] },
    { total_count: 2, jobs: [{ name: 'Experimental installer and desktop smoke', status: 'completed', conclusion: 'success' },
      { name: 'Full release regression (required for release)', status: 'in_progress', conclusion: null }] },
    { ...clone(run), id: pin.fullRegressionRunId, head_sha: pin.commit, path: '.github/workflows/desktop-windows.yml' }];
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
  assert.equal(validateMetadata(...metadata(),fullPin).fullRegression, 'not-certified-by-this-job');
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
    (x) => { x[4].id++; }, (x) => { x[4].head_sha = 'bad'; },
    (x) => { x[4].head_branch = 'master'; }, (x) => { x[4].path = '.github/workflows/desktop-layout-retry.yml'; },
    (x) => { x[4].repository.full_name = 'fork/repo'; },
  ]) { const values = metadata(); mutate(values); assert.throws(() => validateMetadata(...values,fullPin)); }
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

// Pure unit fixtures, not published artifact identities. Only PIN is used by verify-artifact.
const retryPin = Object.freeze({ ...PIN,
  commit: 'd853bd2eb117929e73530bb5036256801278b235',
  buildCommit: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  buildWorkflow: '.github/workflows/desktop-layout-retry.yml',
  runId: 20000000001, artifactId: 20000000002,
  fullRegressionRunId: 37626960710,
  artifactName: 'unit-fixture-only-retry-artifact',
});
test('reviewed retry pins bind artifact/run to helper head while retaining exact product/full-gate identity',()=>{
  validateReviewedPin(retryPin);
  const result=validateMetadata(...metadata(retryPin),retryPin);
  assert.equal(result.commit,retryPin.commit);
  assert.equal(result.buildCommit,retryPin.buildCommit);
  assert.equal(result.buildWorkflow,retryPin.buildWorkflow);
  assert.equal(result.runId,retryPin.runId);
  assert.equal(result.fullRegressionRunId,37626960710);
  assert.equal(result.fullRegression,'not-certified-by-this-job');
  assert.notEqual(result.commit,result.buildCommit);
  assert.throws(()=>validateRequest(retryPin),'unit fixture cannot authorize a new runtime pin');
});
test('retry metadata fails if build and product identities are swapped or unrelated workflows/branches appear',()=>{
  for(const mutate of [
    (x)=>{x[0].head_sha=retryPin.commit;},
    (x)=>{x[1].workflow_run.head_sha=retryPin.commit;},
    (x)=>{x[4].head_sha=retryPin.buildCommit;},
    (x)=>{x[0].path='.github/workflows/desktop-windows.yml';},
    (x)=>{x[0].path='.github/workflows/arbitrary.yml';},
    (x)=>{x[0].head_branch='feat/other';},
    (x)=>{x[1].workflow_run.head_branch='master';},
    (x)=>{x[4].id=retryPin.runId;},
    (x)=>{x[4].event='workflow_dispatch';},
  ]) {const values=metadata(retryPin);mutate(values);assert.throws(()=>validateMetadata(...values,retryPin));}
});
test('narrow pin schema cannot broaden repository, branch, workflow or confuse independent regression',()=>{
  for(const patch of [
    {repository:'other/repository'}, {branch:'master'},
    {buildWorkflow:'.github/workflows/arbitrary.yml'},
    {buildWorkflow:'https://example.invalid/workflow.yml'},
    {fullRegressionRunId:retryPin.runId}, {buildCommit:retryPin.commit},
    {commit:'not-a-sha'}, {runId:0}, {artifactId:-1},
    {installer:'other.exe'}, {schema:1}, {extra:'arbitrary'},
  ]) assert.throws(()=>validateReviewedPin({...retryPin,...patch}));
  const ordinaryPin={...PIN,buildWorkflow:'.github/workflows/desktop-windows.yml',commit:'b'.repeat(40),buildCommit:'b'.repeat(40),fullRegressionRunId:PIN.runId};
  assert.throws(()=>validateReviewedPin({...ordinaryPin,buildCommit:retryPin.buildCommit}));
  assert.throws(()=>validateReviewedPin({...ordinaryPin,fullRegressionRunId:ordinaryPin.runId+1}));
});

test('full-gate identity validation cannot be mistaken for full-gate success certification',()=>{
  for(const state of [{status:'in_progress',conclusion:null},{status:'completed',conclusion:'failure'},{status:'completed',conclusion:'success'}]){
    const values=metadata(retryPin);Object.assign(values[4],state);
    assert.equal(validateMetadata(...values,retryPin).fullRegression,'not-certified-by-this-job');
  }
});

// Unit-only package fixture: no artifact or API response can change the reviewed runtime PIN.
const packagePin = Object.freeze({ ...PIN, fullRegressionRunId: null });
function packageMetadata() {
  const values = metadata(packagePin);
  values[0].display_title = 'build: AI [windows-package-only] unit fixture';
  values[0].head_commit = { id: packagePin.buildCommit, message: values[0].display_title + '\n\nNo full gate certification.' };
  values[0].run_attempt = 1;
  values[4] = null;
  values[3] = { total_count: 4, jobs: [
    { name: 'Experimental installer and desktop smoke', status: 'completed', conclusion: 'success' },
    ...['Collect complete pytest manifest', 'Complete pytest shard ${{ matrix.shard }} of 12',
      'Full release regression (required for release)'].map(name => ({ name, status: 'completed', conclusion: 'skipped' })),
  ].map(job => ({ ...job, run_id: packagePin.runId, head_sha: packagePin.buildCommit })) };
  return values;
}
test('package-only null pin is explicitly not run, not full-gate identity or certification', () => {
  validateReviewedPin(packagePin);
  const result = validateMetadata(...packageMetadata(), packagePin);
  assert.equal(result.fullRegressionRunId, null);
  assert.equal(result.buildScope, 'package-only');
  assert.equal(result.fullRegression, 'not-run-package-only');
  assert.equal(result.packaging, 'success');
  assert.equal(result.commit, packagePin.commit);
  const values = packageMetadata();
  values[0].display_title = 'BUILD: AI [WINDOWS-PACKAGE-ONLY] exact workflow semantics';
  values[0].head_commit.message = values[0].display_title;
  assert.equal(validateMetadata(...values, packagePin).fullRegression, 'not-run-package-only');
});
test('ordinary runs, substituted full gate, wrong attempt or absent package-only marker reject null', () => {
  for (const mutate of [
    x => { delete x[0].display_title; },
    x => { delete x[0].head_commit; },
    x => { x[0].head_commit.id = 'f'.repeat(40); },
    x => { x[0].head_commit.message = 'test: ordinary run with forged display title'; },
    x => { x[0].head_commit.message = 'first line\nbuild: AI [windows-package-only] later marker'; },
    x => { x[0].head_commit.message = 'build: AI [windows-package-only]'; },
    x => { x[0].display_title = 'test: normal full gate'; },
    x => { x[0].display_title = 'build: AI [windows-package-only]'; },
    x => { x[0].display_title = 'prefix build: AI [windows-package-only] unit fixture'; },
    x => { x[0].run_attempt = 2; },
    x => { x[0].event = 'workflow_dispatch'; },
    x => { x[0].head_sha = 'f'.repeat(40); },
    x => { x[4] = metadata(fullPin)[4]; },
    x => { x[4] = undefined; },
    x => { x[3].jobs[0].run_id++; },
    x => { x[3].jobs[0].head_sha = 'f'.repeat(40); },
  ]) { const values = packageMetadata(); mutate(values); assert.throws(() => validateMetadata(...values, packagePin)); }
});
test('every package-only skipped full-gate stage must exist exactly once in the same run and product', () => {
  for (const index of [1, 2, 3]) {
    for (const mutate of [
      x => { x[3].jobs.splice(index, 1); x[3].total_count--; },
      x => { x[3].jobs.push(clone(x[3].jobs[index])); x[3].total_count++; },
      x => { x[3].jobs[index].name = 'unrelated stage'; },
      x => { x[3].jobs[index].status = 'in_progress'; },
      ...['success', 'failure', 'cancelled', null].map(conclusion => x => { x[3].jobs[index].conclusion = conclusion; }),
      x => { x[3].jobs[index].run_id++; },
      x => { x[3].jobs[index].head_sha = 'f'.repeat(40); },
    ]) { const values = packageMetadata(); mutate(values); assert.throws(() => validateMetadata(...values, packagePin)); }
  }
  const values = packageMetadata();
  values[3].jobs.push({ ...values[3].jobs[2], name: 'Complete pytest shard 1 of 12' }); values[3].total_count++;
  assert.throws(() => validateMetadata(...values, packagePin));
});
test('nullable scope cannot broaden workflow, source/build identity or integer full-gate rules', () => {
  for (const patch of [
    { buildWorkflow: '.github/workflows/desktop-layout-retry.yml', buildCommit: 'e'.repeat(40) },
    { buildWorkflow: '.github/workflows/other.yml' }, { buildCommit: 'e'.repeat(40) },
    { fullRegressionRunId: undefined }, { fullRegressionRunId: 'null' }, { fullRegressionRunId: 0 },
    { fullRegressionRunId: packagePin.runId + 1 },
  ]) assert.throws(() => validateReviewedPin({ ...packagePin, ...patch }));
  validateReviewedPin(fullPin);
  const full = validateMetadata(...metadata(fullPin), fullPin);
  assert.equal(full.fullRegression, 'not-certified-by-this-job');
  assert.equal(Object.hasOwn(full, 'buildScope'), false, 'integer provenance retains its original evidence shape');
});
