import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { finishValidationProcess, trackAppResource, waitForResourceSettlement } from '../run-finalization.mjs';
import { saveEvidence } from '../safe-evidence.mjs';
import { CASES } from '../contract.mjs';

const completed = { exitCode: 1, finalEvidenceSaved: true, cleanupPassed: true, cleanupCode: 'CLEANUP_PASSED', appResourcesReleased: true };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test('only the owned launcher close event proves its process and pipes have closed', () => {
  const child = new EventEmitter();
  const resource = trackAppResource({ process: () => child });
  assert.equal(resource.launcherClosed, false);
  child.emit('exit', 0); assert.equal(resource.launcherClosed, false);
  child.emit('close', 0); assert.equal(resource.launcherClosed, true);
});

test('unsettled resource close is bounded and the same promise can settle after OS cleanup', async () => {
  const close = deferred();
  assert.equal(await waitForResourceSettlement(close.promise, 5), false);
  close.resolve();
  assert.equal(await waitForResourceSettlement(close.promise, 5), true);
  assert.equal(await waitForResourceSettlement(Promise.reject(new Error('private close failure')), 5), true);
});

test('failure exit remains failure after proven cleanup and final evidence write', () => {
  const exits = [], target = { exit: code => exits.push(code) };
  finishValidationProcess(completed, target);
  assert.equal(target.exitCode, 1); assert.deepEqual(exits, [1]);
  finishValidationProcess({ ...completed, exitCode: 0 }, target);
  assert.equal(target.exitCode, 0); assert.deepEqual(exits, [1, 0]);
});

for (const change of [
  { cleanupPassed: false }, { cleanupCode: 'OWNED_PROCESS_CLEANUP_FAILED' },
  { cleanupCode: 'BROKER_CLEANUP_FAILED' }, { cleanupCode: 'EVIDENCE_WRITE_BLOCKED' },
  { finalEvidenceSaved: false }, { finalEvidenceSaved: undefined },
  { appResourcesReleased: false }, { appResourcesReleased: undefined },
]) test(`unproven cleanup/evidence cannot force process exit: ${JSON.stringify(change)}`, () => {
  const target = { exit() { assert.fail('unverified cleanup must not force exit'); } };
  finishValidationProcess({ ...completed, ...change }, target);
  assert.equal(target.exitCode, 1);
});

test('returned failure cannot remain alive behind a referenced handle after proven cleanup', () => {
  const moduleUrl = new URL('../run-finalization.mjs', import.meta.url).href;
  const prefix = 'setInterval(() => {}, 60000);';
  // This is the old CLI behavior: a terminal failure code does not close handles.
  const old = spawnSync(process.execPath, ['--input-type=module', '-e', `${prefix} process.exitCode = 1;`], { timeout: 500, encoding: 'utf8' });
  assert.equal(old.error?.code, 'ETIMEDOUT');
  const current = spawnSync(process.execPath, ['--input-type=module', '-e',
    `import { finishValidationProcess } from ${JSON.stringify(moduleUrl)}; ${prefix} finishValidationProcess(${JSON.stringify(completed)});`],
  { timeout: 1500, encoding: 'utf8' });
  assert.equal(current.error, undefined); assert.equal(current.status, 1); assert.equal(current.signal, null);
});

const runner = fs.readFileSync(new URL('../validation-runner.mjs', import.meta.url), 'utf8');
function extractFinalizer(source) {
  const normalized = source.replace(/\r\n/g, '\n');
  const marker = '} finally {';
  const start = normalized.lastIndexOf(marker), end = normalized.lastIndexOf('\n}\n\n}');
  assert.ok(start >= 0, 'runner finalizer start marker must exist');
  assert.ok(end >= 0, 'runner finalizer end marker must exist');
  assert.ok(end > start + marker.length, 'runner finalizer markers must be in forward order');
  return normalized.slice(start + marker.length, end);
}
const finalizer = extractFinalizer(runner);
async function runFinalizer({ cleanupError, evidenceError = false, closeRejects = false, launchPending = false,
  hasApp = true, launcherStaysOpen = false, priorLauncherOpen = false } = {}, body = finalizer) {
  const events = [], close = deferred();
  const resource = { launcherClosed: false, closeConfirmed: false,
    app: { close() { events.push('app-close-started'); return closeRejects ? Promise.reject(new Error('private close failure')) : close.promise; } } };
  const appResources = hasApp ? [resource] : [];
  if (priorLauncherOpen) appResources.unshift({ launcherClosed: false, closeConfirmed: false,
    app: { async close() { events.push('prior-app-close'); } } });
  let saves = 0;
  const context = vm.createContext({
    broker: { async close() { events.push('broker-closed'); }, snapshot: () => ({ closed: true, active: false }) },
    ledger: {}, cleanupFailure: undefined, failure: undefined, screenshotEvidence: undefined, finalEvidenceSaved: false, appLaunchPending: launchPending,
    report: { mode: 'mock', status: 'FAIL', code: 'UI_CASES_INCOMPLETE', cleanupPassed: false },
    evidence: '/synthetic-no-write', providerKey: undefined, mode: 'mock', process: { env: {} },
    safeCode: error => error.safeCode,
    appResources,
    waitForResourceSettlement: async pending => {
      const settled = await waitForResourceSettlement(pending, 1);
      events.push(settled ? 'close-settled' : 'close-pending'); return settled;
    },
    cleanOwnedFiles: async () => {
      events.push('owned-cleanup');
      if (cleanupError) throw Object.assign(new Error('private process detail'), { safeCode: cleanupError });
      resource.launcherClosed = !launcherStaysOpen;
      close.resolve();
    },
    saveEvidence: async (_directory, report) => {
      saves += 1; events.push(`save-${report.cleanupCode}`);
      if (saves === 2 && evidenceError) throw new Error('private file detail');
    },
    console: { log() { events.push('final-summary'); } },
  });
  const result = await vm.runInContext(`(async () => {${body}})()`, context);
  close.resolve();
  return { result, events };
}

test('LF and CRLF runner sources compile and execute the same finalizer', async () => {
  const lf = runner.replace(/\r\n/g, '\n'), crlf = lf.replace(/\n/g, '\r\n');
  const bodies = [lf, crlf].map(extractFinalizer);
  assert.equal(bodies[0], bodies[1]);
  const results = [];
  for (const body of bodies) {
    assert.doesNotThrow(() => new vm.Script(`(async () => {${body}})()`));
    results.push(JSON.parse(JSON.stringify(await runFinalizer({}, body))));
  }
  assert.deepEqual(results[0], results[1]);
  assert.equal(results[1].result.cleanupPassed, true);
  assert.equal(results[1].result.appResourcesReleased, true);
});

test('missing or reversed source markers fail before compiling a partial finalizer', () => {
  assert.throws(() => extractFinalizer('\n}\n\n}'), /start marker must exist/);
  assert.throws(() => extractFinalizer('} finally {'), /end marker must exist/);
  assert.throws(() => extractFinalizer('\n}\n\n}\n} finally {'), /forward order/);
});

test('actual runner finalizer drains the retained close after verified OS cleanup', async () => {
  const { result, events } = await runFinalizer();
  assert.equal(result.exitCode, 1); assert.equal(result.cleanupPassed, true); assert.equal(result.finalEvidenceSaved, true);
  assert.equal(result.appResourcesReleased, true);
  assert.deepEqual(events, ['broker-closed', 'save-CLEANUP_PENDING', 'app-close-started', 'close-pending',
    'owned-cleanup', 'close-settled', 'save-CLEANUP_PASSED', 'final-summary']);
});

test('actual runner preserves failed process cleanup and cannot authorize forced exit', async () => {
  const { result } = await runFinalizer({ cleanupError: 'OWNED_PROCESS_CLEANUP_FAILED' });
  assert.equal(result.status, 'BLOCKED'); assert.equal(result.exitCode, 1); assert.equal(result.cleanupPassed, false);
  assert.equal(result.cleanupCode, 'OWNED_PROCESS_CLEANUP_FAILED'); assert.equal(result.finalEvidenceSaved, true);
  finishValidationProcess(result, { exit() { assert.fail('surviving owned processes must remain a failed cleanup'); } });
});

test('failed final evidence write cannot authorize forced exit even after OS cleanup', async () => {
  const { result } = await runFinalizer({ evidenceError: true });
  assert.equal(result.cleanupPassed, true); assert.equal(result.finalEvidenceSaved, false);
  assert.equal(result.cleanupCode, 'EVIDENCE_WRITE_BLOCKED');
  finishValidationProcess(result, { exit() { assert.fail('evidence must be confirmed written before forced exit'); } });
});

for (const options of [{ closeRejects: true }, { launchPending: true }, { launchPending: true, hasApp: false },
  { launcherStaysOpen: true }, { priorLauncherOpen: true }]) {
  test(`unconfirmed Playwright ownership cannot authorize forced exit: ${JSON.stringify(options)}`, async () => {
    const { result } = await runFinalizer(options);
    assert.equal(result.cleanupPassed, true); assert.equal(result.finalEvidenceSaved, true);
    assert.equal(result.appResourcesReleased, false);
    finishValidationProcess(result, { exit() { assert.fail('unsettled Playwright ownership must not trigger its forced exit hook'); } });
  });
}

test('absence of an app is released only when no launch was left in flight', async () => {
  const { result } = await runFinalizer({ hasApp: false });
  assert.equal(result.appResourcesReleased, true);
  const exits = [];
  finishValidationProcess(result, { exit: code => exits.push(code) });
  assert.deepEqual(exits, [1]);
});

test('process cleanup failure survives the safe artifact boundary without private detail or CLI flags', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'offerpilot-cleanup-evidence-'));
  try {
    await saveEvidence(directory, { mode: 'mock', status: 'BLOCKED', code: 'UI_CASES_INCOMPLETE',
      cleanupCode: 'OWNED_PROCESS_CLEANUP_FAILED', cleanupPassed: false, finalEvidenceSaved: true, appResourcesReleased: false,
      privateDetail: 'private process paths', scenarios: CASES.map(id => ({ id, status: 'BLOCKED', code: 'NOT_STARTED', checks: {} })) }, {});
    const text = fs.readFileSync(path.join(directory, 'result.json'), 'utf8');
    assert.equal(JSON.parse(text).cleanupCode, 'OWNED_PROCESS_CLEANUP_FAILED');
    assert.doesNotMatch(text, /private process paths|finalEvidenceSaved|appResourcesReleased/);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
