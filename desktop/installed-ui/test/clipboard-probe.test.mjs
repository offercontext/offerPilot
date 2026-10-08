import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { SYNTHETIC_JD_SOURCE, assertHostedClipboardEnvironment, assertClipboardSnapshot,
  nativeClipboardInstrumentation, probeInstalledClipboard } from '../clipboard-probe.mjs';

const require = createRequire(import.meta.url);
const { createCapabilities } = require('../../capabilities.cjs');
const origin = 'http://127.0.0.1:18420';

function fixture(t, options = {}) {
  const key = `clipboard-test.${randomUUID()}`;
  let url = origin;
  const contents = new EventEmitter();
  Object.assign(contents, { getURL: () => url, isDestroyed: () => false, mainFrame: {},
    setWindowOpenHandler: () => {} });
  const owner = { webContents: contents };
  const session = new EventEmitter();
  session.setPermissionCheckHandler = fn => { session.check = fn; };
  session.setPermissionRequestHandler = fn => { session.request = fn; };
  const dialog = { showMessageBox: async () => { throw new Error('unexpected native prompt'); } };
  const original = dialog.showMessageBox;
  const originalValue = 'PRIVATE pre-existing clipboard must never be read';
  let value = originalValue;
  const operations = [];
  const clipboard = {
    writeText(text) {
      operations.push({ type: 'write' });
      if (options.failWrite) throw new Error('write failed');
      value = options.ignoreWrites ? originalValue : text;
    },
    readText() {
      assert.equal(operations.some(item => item.type === 'write'), true, 'read cannot predate synthetic write');
      operations.push({ type: 'read' });
      return value;
    },
  };
  createCapabilities({ origin, desktopSession: session, isTrustedContents: candidate => candidate === contents,
    dialog, shell: { openExternal: () => { throw new Error('external launch forbidden'); } },
    BrowserWindow: { fromWebContents: candidate => candidate === contents ? owner : null } }).installWindowPolicy(owner);
  const check = session.check;
  const requestHandler = session.request;
  // A helper must not grant permissions itself or broaden their check policy.
  session.setPermissionCheckHandler = () => { throw new Error('permission check replacement forbidden'); };
  session.setPermissionRequestHandler = () => { throw new Error('permission request replacement forbidden'); };
  const run = (operation, extra = {}) => nativeClipboardInstrumentation({ dialog, clipboard },
    { key, operation, ownerURL: origin, ...extra });
  t.after(() => {
    run('restore');
    assert.equal(session.check, check);
    assert.equal(session.request, requestHandler);
    assert.equal(dialog.showMessageBox, original);
  });
  const request = (permission = 'clipboard-sanitized-write', overrides = {}) => new Promise(resolve => {
    session.request(contents, permission, resolve, { isMainFrame: true, requestingUrl: origin, ...overrides });
  });
  return { run, owner, dialog, clipboard, session, contents, operations, original, request,
    navigate: next => { url = next; },
    install: expectedText => run('install', { owner, expectedText: expectedText ?? SYNTHETIC_JD_SOURCE }) };
}

test('clipboard requires actual Windows on an explicitly hosted GitHub runner', () => {
  const env = { GITHUB_ACTIONS: 'true', RUNNER_ENVIRONMENT: 'github-hosted' };
  assert.doesNotThrow(() => assertHostedClipboardEnvironment(env, 'win32'));
  for (const [candidate, platform] of [[{}, 'win32'], [env, 'linux'],
    [{ ...env, RUNNER_ENVIRONMENT: 'self-hosted' }, 'win32'], [{ ...env, GITHUB_ACTIONS: 'false' }, 'win32']]) {
    assert.throws(() => assertHostedClipboardEnvironment(candidate, platform));
  }
});

test('production permission policy independently confirms cancel, allow, and cancel with exact synthetic readbacks', async t => {
  const f = fixture(t);
  f.install();
  assert.deepEqual(f.operations, [], 'install must never inspect the pre-existing clipboard');
  for (const [sequence, choice] of ['cancel', 'allow', 'cancel'].entries()) {
    assert.deepEqual(f.run('arm', { sequence, choice }), {
      baselineMatched: true, syntheticBaselineWrittenBeforeRead: true,
    });
    const allowed = await f.request();
    assert.equal(allowed, choice === 'allow');
    if (allowed) f.clipboard.writeText(SYNTHETIC_JD_SOURCE); // Simulate Chromium's write AFTER the real product decision.
    assert.deepEqual(f.run('readback', { sequence }), { exactExpectedValue: true, comparisonOnly: true });
    const snapshot = f.run('snapshot');
    assertClipboardSnapshot(snapshot, sequence + 1);
    assert.equal(snapshot.messages.length, sequence + 1);
    assert.equal(f.session.check(f.contents, 'clipboard-sanitized-write', origin,
      { isMainFrame: true, requestingUrl: origin }), false, 'permission is not stored');
  }
  const snapshot = f.run('snapshot');
  assert.equal(snapshot.syntheticBaselineWrites, 3);
  assert.equal(snapshot.comparisonOnlyReads, 6);
  assert.equal(f.operations[0].type, 'write');
  assert.doesNotMatch(JSON.stringify(snapshot), /PRIVATE|baseline|example\.invalid/);
  assert.equal(await f.request('clipboard-read'), false, 'renderer never gains clipboard read access');
  assert.equal(await f.request('deprecated-sync-clipboard-read'), false);
  const restored = f.run('restore');
  assert.equal(restored.restored, true);
  assert.equal(restored.preExistingClipboardNeverRead, true);
  assert.equal(restored.oldClipboardRestorationAttempted, false);
  assertClipboardSnapshot(restored.finalSnapshot, 3);
});

test('one native answer is consumed and a second request cannot reuse allow', async t => {
  const f = fixture(t); f.install();
  f.run('arm', { sequence: 0, choice: 'cancel' }); await f.request();
  f.run('arm', { sequence: 1, choice: 'allow' }); assert.equal(await f.request(), true);
  assert.equal(await f.request(), false);
  assert.deepEqual(f.run('snapshot').failures, ['unexpected-native-confirmation']);
  assert.throws(() => f.run('readback', { sequence: 1 }), /not safe/);
});

test('an unrelated native prompt is cancelled and cannot be passed off as a clipboard confirmation', async t => {
  const f = fixture(t); f.install();
  f.run('arm', { sequence: 0, choice: 'cancel' });
  assert.equal(await f.request('media', { mediaTypes: ['audio'] }), false);
  const snapshot = f.run('snapshot');
  assert.equal(snapshot.messages[0].expected, false);
  assert.equal(snapshot.messages[0].choice, 'cancel');
  assert.throws(() => assertClipboardSnapshot(snapshot, 1));
  assert.throws(() => f.run('readback', { sequence: 0 }), /not safe/);
});

test('failed baseline write never reads the pre-existing clipboard', t => {
  const f = fixture(t, { failWrite: true }); f.install();
  assert.throws(() => f.run('arm', { sequence: 0, choice: 'cancel' }), /write failed/);
  assert.deepEqual(f.operations, [{ type: 'write' }]);
  assert.throws(() => f.run('readback', { sequence: 0 }), /not safe/);
});

test('unestablished baseline fails without leaking a value in returned evidence or errors', t => {
  const f = fixture(t, { ignoreWrites: true }); f.install();
  assert.throws(() => f.run('arm', { sequence: 0, choice: 'cancel' }), error => {
    assert.equal(error.message, 'synthetic clipboard baseline not established'); return true;
  });
  assert.equal(f.run('snapshot').messages.length, 0);
  assert.throws(() => f.run('readback', { sequence: 0 }), /not safe/);
});

test('readback is forbidden before a baseline, while pending, after navigation, or for an incorrect request', async t => {
  const f = fixture(t); f.install();
  assert.throws(() => f.run('readback', { sequence: 0 }), /not safe/);
  f.run('arm', { sequence: 0, choice: 'cancel' });
  assert.throws(() => f.run('readback', { sequence: 0 }), /not safe/);
  await f.request();
  assert.throws(() => f.run('readback', { sequence: 1 }), /not confirmed/);
  f.navigate(`${origin}/different-document`);
  assert.throws(() => f.run('readback', { sequence: 0 }), /not safe/);
});

test('unsafe input, out-of-order requests, rearming, extra confirmations and failed readback cannot pass', async t => {
  const f = fixture(t);
  assert.throws(() => f.install('https://owner.example/private'), /non-synthetic/);
  f.install();
  assert.throws(() => f.run('arm', { sequence: 1, choice: 'allow' }), /sequence/);
  f.run('arm', { sequence: 0, choice: 'cancel' });
  assert.throws(() => f.run('arm', { sequence: 0, choice: 'cancel' }), /not safe/);
  await f.request();
  f.clipboard.writeText('unexpected synthetic modification');
  assert.equal(f.run('readback', { sequence: 0 }).exactExpectedValue, false);
  assert.throws(() => assertClipboardSnapshot({ ...f.run('snapshot'), ownerURLUnchanged: false }, 1));
  assert.throws(() => assertClipboardSnapshot(f.run('snapshot'), 0));
});

test('cleanup restores dialog even while armed without reading or restoring prior clipboard content', t => {
  const f = fixture(t); f.install();
  f.run('arm', { sequence: 0, choice: 'cancel' });
  const before = f.operations.length;
  f.run('restore');
  assert.equal(f.operations.length, before);
  assert.equal(f.dialog.showMessageBox, f.original);
  assert.deepEqual(f.run('restore'), { restored: true, absent: true });
});

test('probe refuses unsupported hosts before accessing app/page/clipboard', async () => {
  if (process.platform === 'win32' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.RUNNER_ENVIRONMENT === 'github-hosted') return;
  await assert.rejects(probeInstalledClipboard({}), /clipboard probe requires/);
});

test('helper preserves product API, permission handlers and ordinary UI clicks', async () => {
  const source = await readFile(new URL('../clipboard-probe.mjs', import.meta.url), 'utf8');
  assert.ok(source.includes('await copyButton.click()'));
  assert.ok(source.includes('assertHostedClipboardEnvironment();'));
  for (const denied of ['setPermissionRequestHandler(', 'setPermissionCheckHandler(', 'grantPermissions(',
    'force: true', 'navigator.clipboard.writeText =', 'clipboard.readHTML', 'clipboard.readImage', 'clipboard.clear(']) {
    assert.equal(source.includes(denied), false, denied);
  }
});

test('main-process observer survives Playwright serialization without module-scope dependencies', () => {
  const serialized = vm.runInNewContext(`(${nativeClipboardInstrumentation.toString()})`);
  const original = async () => ({ response: 0 });
  const dialog = { showMessageBox: original };
  let value;
  const operations = [];
  const clipboard = { writeText: text => { operations.push('write'); value = text; },
    readText: () => { operations.push('read'); return value; } };
  const owner = { webContents: { isDestroyed: () => false, getURL: () => origin } };
  const args = { key: `serialized-${randomUUID()}`, ownerURL: origin };
  serialized({ dialog, clipboard }, { ...args, operation: 'install', owner, expectedText: SYNTHETIC_JD_SOURCE });
  assert.equal(serialized({ dialog, clipboard }, { ...args, operation: 'arm', sequence: 0, choice: 'cancel' }).baselineMatched, true);
  assert.deepEqual(operations, ['write', 'read']);
  serialized({ dialog, clipboard }, { ...args, operation: 'restore' });
  assert.equal(dialog.showMessageBox, original);
});

// VM dependencies exercise orchestration only; these tests are not actual
// Windows/clipboard/UI evidence, and provide no production host-check bypass.
function orchestration(t, options = {}) {
  const f = fixture(t);
  const choices = ['cancel', 'allow', 'cancel'];
  const toasts = [];
  const captures = [];
  let toast;
  let clicks = 0;
  let disposed = 0;
  const hostCheck = () => assertHostedClipboardEnvironment(
    { GITHUB_ACTIONS: 'true', RUNNER_ENVIRONMENT: 'github-hosted' }, 'win32');
  const context = vm.createContext({ assert, randomUUID, setTimeout, URL,
    SYNTHETIC_JD_SOURCE, CHOICES: choices, SUCCESS: '来源已复制', DENIED: '无法复制，请手动选择来源文字',
    assertHostedClipboardEnvironment: hostCheck, nativeClipboardInstrumentation, assertClipboardSnapshot });
  const invoke = vm.runInContext(`(${probeInstalledClipboard.toString()})`, context);
  const page = { url: () => origin, bringToFront: async () => {},
    getByText: text => ({ waitFor: async ({ state }) => {
      if (state === 'hidden') { if (toast === text) toast = undefined; return; }
      assert.equal(toast, text, 'matching current product toast required'); toasts.push(text);
    } }) };
  f.owner.dispose = async () => { disposed++; };
  const app = {
    browserWindow: async () => f.owner,
    evaluate: async (fn, args) => {
      if (args.operation === 'restore' && options.lateConfirmation && clicks === 3) await f.request();
      return fn({ dialog: f.dialog, clipboard: f.clipboard }, args);
    },
  };
  const copyButton = { count: async () => 1, isVisible: async () => true,
    evaluate: async () => !options.wrongSource,
    click: async () => {
      if (options.clickFails) throw new Error('copy control failed');
      const allowed = await f.request();
      if (allowed) f.clipboard.writeText(options.corruptCopy ? 'synthetic wrong source' : SYNTHETIC_JD_SOURCE);
      toast = allowed ? '来源已复制' : '无法复制，请手动选择来源文字';
      clicks++;
    } };
  return { run: () => invoke({ app, page, copyButton, timeoutMs: 100,
    capture: async label => { captures.push(label); if (options.captureFails) throw new Error('capture failed'); } }),
    check: () => { assert.equal(f.dialog.showMessageBox, f.original); return { clicks, disposed, captures, toasts, operations: f.operations }; } };
}

test('orchestration requires each product toast, captures all three choices, and restores instrumentation', async t => {
  const f = orchestration(t);
  const result = await f.run();
  assert.equal(result.instrumentationRestored, true);
  assert.equal(result.steps.length, 3);
  assert.equal(result.preExistingClipboardNeverRead, true);
  assert.equal(result.clipboardContentsIncludedInEvidence, false);
  assert.equal(result.nativePointerInteractionValidated, false);
  const observed = f.check();
  assert.equal(observed.clicks, 3);
  assert.equal(observed.disposed, 1);
  assert.deepEqual(observed.captures, ['clipboard-0-cancel', 'clipboard-1-allow', 'clipboard-2-cancel']);
  assert.equal(observed.operations[0].type, 'write');
});

for (const option of ['clickFails', 'corruptCopy', 'captureFails', 'lateConfirmation']) {
  test(`orchestration ${option} fails instead of returning successful evidence and restores native dialog`, async t => {
    const f = orchestration(t, { [option]: true });
    await assert.rejects(f.run());
    assert.equal(f.check().disposed, 1);
  });
}

test('wrong displayed JD source is rejected before any clipboard or owner handle access', async t => {
  const f = orchestration(t, { wrongSource: true });
  await assert.rejects(f.run(), /exact synthetic JD source/);
  const observed = f.check();
  assert.deepEqual(observed.operations, []);
  assert.equal(observed.clicks, 0);
  assert.equal(observed.disposed, 0);
});
