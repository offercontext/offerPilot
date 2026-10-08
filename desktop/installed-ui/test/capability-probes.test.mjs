import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { CAPABILITY_BYTES, assertDeniedPermissions, probeInstalledCapabilities, readPermissionDecisions } from '../capability-probes.mjs';

const require = createRequire(import.meta.url);
const { createCapabilities } = require('../../capabilities.cjs');
const origin = 'http://127.0.0.1:18420';

async function fixture(t, options = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-capability-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const session = new EventEmitter();
  session.setPermissionCheckHandler = (fn) => { session.check = fn; };
  session.setPermissionRequestHandler = (fn) => { session.request = fn; };
  const contents = new EventEmitter();
  Object.assign(contents, { mainFrame: {}, session, getURL: () => origin, isDestroyed: () => false,
    setWindowOpenHandler: (fn) => { contents.open = fn; } });
  let disposed = 0;
  const owner = { webContents: contents, dispose: async () => { disposed++; } };
  const unexpectedDialogs = [];
  const dialog = {
    showSaveDialogSync: () => { unexpectedDialogs.push('save'); throw new Error('unautomated native dialog'); },
    showMessageBox: async () => { unexpectedDialogs.push('message'); throw new Error('unautomated native message'); },
  };
  const originals = { ...dialog };
  const openedExternal = [];
  const shell = { openExternal: async (url) => { openedExternal.push(url); } };
  const originalExternal = shell.openExternal;
  const afterCancelEntries = [];
  const policy = createCapabilities({ origin, desktopSession: session, isTrustedContents: (candidate) => candidate === contents,
    dialog, shell,
    BrowserWindow: { fromWebContents: (candidate) => candidate === contents ? owner : null } });
  policy.installWindowPolicy(owner);
  const originalCheck = session.check;
  const originalRequest = session.request;
  const originalOpen = contents.open;
  const originalDownloadListeners = session.listeners('will-download');
  const originalNavigationListeners = contents.listeners('will-navigate');
  // Any attempt to replace product policy in the probe is a test failure.
  session.setPermissionCheckHandler = () => { throw new Error('must not replace permission check'); };
  session.setPermissionRequestHandler = () => { throw new Error('must not replace permission request'); };
  contents.setWindowOpenHandler = () => { throw new Error('must not replace window policy'); };
  session.webRequest = new Proxy({}, { get() { throw new Error('must not touch webRequest handlers'); } });
  if (options.setupFailure) session.on = () => { throw new Error('instrumentation setup failed'); };
  const blobs = new Map();
  const items = [];
  const writes = [];
  let blobSequence = 0;
  let attempt = 0;
  const nativeEvent = () => ({ defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
  const tasks = [];
  function download(url, filename) {
    attempt++;
    const item = new EventEmitter();
    const blob = blobs.get(url);
    let selected;
    Object.assign(item, {
      getURLChain: () => [url], getInitiatorOrigin: () => origin, getFilename: () => filename,
      setSavePath: (value) => { selected = value; }, getSavePath: () => options.wrongNativePath ? 'wrong-path' : selected,
      getReceivedBytes: () => blob.size + (options.wrongNativeByteCount ? 1 : 0), getTotalBytes: () => blob.size,
    });
    items.push(item);
    const event = nativeEvent();
    session.emit('will-download', event, item, contents, options.missingNativeFrame ? null : contents.mainFrame);
    if (event.defaultPrevented) return; // Electron need not emit done for prevented items.
    tasks.push((async () => {
      if (!selected) return;
      const payload = Buffer.from(await blob.arrayBuffer());
      await fs.writeFile(selected, options.corruptSave ? Buffer.from('corrupt') : payload);
      writes.push(selected);
      if (!options.noSaveDone) item.emit('done', {}, options.interruptedSave ? 'interrupted' : 'completed');
    })());
  }
  const requestNotification = () => {
    if (options.notificationHangs) return new Promise(() => {});
    if (options.notificationError) return Promise.reject(new Error('permission API failed'));
    if (options.notification !== undefined) return Promise.resolve(options.notification);
    return new Promise((resolve) => session.request(contents, 'notifications', (allowed) => resolve(allowed ? 'granted' : 'denied'),
      { isMainFrame: true, requestingUrl: origin }));
  };
  const permissionQueries = [];
  const context = vm.createContext({ Blob, Uint8Array, setTimeout, clearTimeout, Notification: options.notificationMissing ? undefined : {
    requestPermission: requestNotification,
  }, navigator: { permissions: options.permissionsMissing ? undefined : { query: async ({ name }) => {
    permissionQueries.push(name);
    if (options.permissionQueryError) throw new Error('unsupported permission query');
    const override = options[name];
    return { state: override ?? (session.check(contents, 'media', origin, {
      isMainFrame: true, requestingUrl: origin, mediaType: name === 'camera' ? 'video' : 'audio',
    }) ? 'granted' : 'denied') };
  } } }, URL: class extends URL {
    static createObjectURL(blob) { const url = `blob:${origin}/synthetic-${++blobSequence}`; blobs.set(url, blob); return url; }
    static revokeObjectURL(url) { blobs.delete(url); }
  }, document: {
    body: { append() {} },
    createElement(tag) {
      assert.equal(tag, 'a');
      return { href: '', download: '', click() { download(this.href, this.download); }, remove() {} };
    },
  }, window: { location: { set href(url) {
    contents.emit('will-navigate', nativeEvent(), url);
    // Simulate an app regression that bypasses the cancelled confirmation.
    if (options.forceExternalAttempt) void shell.openExternal(url).catch(() => {});
  } } } });
  const page = { url: () => origin, evaluate: async (fn, args) => {
    if (options.rendererFailure && fn.name === 'startRendererDownload') throw new Error('renderer evaluation failed');
    if (options.revokeFailure && fn.toString().includes('revokeObjectURL')) throw new Error('renderer cleanup failed');
    context.__args = args;
    return await vm.runInContext(`(${fn.toString()})(__args)`, context);
  } };
  const app = { browserWindow: async (candidate) => { assert.equal(candidate, page); return owner; },
    evaluate: async (fn, args) => {
      if (options.noNativeEvent && args.operation === 'arm' && args.active.mode === 'cancel') {
        context.document.createElement = () => ({ click() {}, remove() {} });
      }
      const result = await fn({ dialog, shell }, args);
      if (args.operation === 'arm' && args.active.mode === 'save') {
        afterCancelEntries.push(...await fs.readdir(path.dirname(args.active.savePath)));
      }
      if (options.restoreFailure && args.operation === 'restore') throw new Error('main process cleanup acknowledgement failed');
      if (options.injectCancelledFile && args.operation === 'snapshot' && attempt === 1) {
        const [entry] = await fs.readdir(directory);
        await fs.writeFile(path.join(directory, entry, 'saved', 'unexpected.bin'), 'unexpected');
      }
      return structuredClone(result);
    } };
  t.after(async () => { await Promise.all(tasks); });
  const checkRestored = ({ blobURLsCleaned = true } = {}) => {
    assert.equal(dialog.showSaveDialogSync, originals.showSaveDialogSync);
    assert.equal(dialog.showMessageBox, originals.showMessageBox);
    assert.equal(shell.openExternal, originalExternal);
    assert.equal(session.check, originalCheck);
    assert.equal(session.request, originalRequest);
    assert.equal(contents.open, originalOpen);
    assert.deepEqual(session.listeners('will-download'), originalDownloadListeners);
    assert.deepEqual(contents.listeners('will-navigate'), originalNavigationListeners);
    for (const item of items) assert.equal(item.listenerCount('done'), 0);
    if (blobURLsCleaned) assert.equal(blobs.size, 0);
    assert.equal(disposed, 1);
    assert.deepEqual(unexpectedDialogs, []);
  };
  return { app, page, directory, writes, openedExternal, afterCancelEntries, permissionQueries, checkRestored,
    run: (extra = {}) => probeInstalledCapabilities({ app, page, directory, timeoutMs: 200, ...extra }) };
}

test('real product policy cancellation writes nothing; completed native save exactly preserves binary Blob', async (t) => {
  const f = await fixture(t);
  const stages = [];
  const result = await f.run({ setStage: (stage) => stages.push(stage) });
  assert.deepEqual(stages, ['native-instrumentation', 'blob-cancel', 'blob-save', 'permission-denials', 'external-cancel', 'cleanup']);
  assert.equal(result.blobCancel.terminal, 'will-download-prevented');
  assert.equal(result.blobCancel.defaultPrevented, true);
  assert.equal(result.blobCancel.downloadDirectoryEmptyAfterCancel, true);
  assert.equal(result.blobCancel.approvedFileAbsentAfterCancel, true);
  assert.equal(result.blobSave.state, 'completed');
  assert.equal(result.blobSave.bytes, CAPABILITY_BYTES.length);
  assert.equal(f.writes.length, 1);
  assert.deepEqual(await fs.readFile(f.writes[0]), Buffer.from(CAPABILITY_BYTES));
  assert.deepEqual(f.afterCancelEntries, [], 'the real selected-save directory was empty between cancel and save');
  assert.equal(result.nativeDialogChoicesAutomated, true);
  assert.equal(result.nativePointerInteractionValidated, false);
  assert.equal(result.instrumentationRestored, true);
  assert.deepEqual(result.permissions, { notification: 'denied', camera: 'denied', microphone: 'denied',
    notificationRequest: 'real-api', mediaProbe: 'permission-state-query-only', deviceAcquisitionAttempted: false });
  assert.deepEqual(f.permissionQueries, ['camera', 'microphone']);
  assert.deepEqual(f.openedExternal, []);
  assert.equal(result.externalNavigation.prevented, true);
  assert.equal(result.externalNavigation.externalLaunchAttempts, 0);
  assert.equal(result.externalNavigation.nativeExternalLaunchBlockedDuringProbe, true);
  f.checkRestored();
});

for (const permission of ['notification', 'camera', 'microphone']) {
  for (const decision of ['granted', 'prompt']) {
    test(`${permission}=${decision} fails instead of becoming a caught denial`, async (t) => {
      const f = await fixture(t, { [permission]: decision });
      await assert.rejects(f.run(), new RegExp(`${permission} must be explicitly denied`));
      f.checkRestored();
    });
  }
}

for (const [option, pattern] of [
  ['notificationMissing', /Notification permission API missing/],
  ['permissionsMissing', /Chromium Permissions API missing/],
  ['notificationError', /permission API failed/],
  ['permissionQueryError', /unsupported permission query/],
  ['notificationHangs', /permission decision timed out/],
  ['corruptSave', /saved bytes must exactly match/],
  ['interruptedSave', /native completed state required/],
  ['noSaveDone', /native terminal evidence timed out/],
  ['wrongNativePath', /native path must equal controlled path/],
  ['wrongNativeByteCount', /native received byte count/],
  ['missingNativeFrame', /mainFrame/],
  ['injectCancelledFile', /cancelled download directory must stay empty/],
  ['rendererFailure', /renderer evaluation failed/],
  ['noNativeEvent', /native terminal evidence timed out/],
  ['setupFailure', /instrumentation setup failed/],
]) {
  test(`${option} fails closed and always restores native instrumentation`, async (t) => {
    const f = await fixture(t, { [option]: true });
    await assert.rejects(f.run(), pattern);
    f.checkRestored();
  });
}

test('renderer cleanup errors cannot prevent restoring native dialogs and listeners', async (t) => {
  const f = await fixture(t, { revokeFailure: true });
  await assert.rejects(f.run(), (error) => error instanceof AggregateError
    && error.errors.length === 2 && error.errors.every((item) => /Blob URL cleanup failed/.test(item.message)));
  f.checkRestored({ blobURLsCleaned: false });
});

test('failed cleanup acknowledgement is reported rather than a passing restoration claim', async (t) => {
  const f = await fixture(t, { restoreFailure: true });
  await assert.rejects(f.run(), (error) => error instanceof AggregateError
    && /native instrumentation cleanup failed/.test(error.errors[0].message));
  f.checkRestored();
});

test('an unexpected permission grant is preserved when cleanup also fails', async (t) => {
  const f = await fixture(t, { notification: 'granted', restoreFailure: true });
  const stages = [];
  await assert.rejects(f.run({ setStage: (stage) => stages.push(stage) }), (error) => error instanceof AggregateError
    && /notification must be explicitly denied/.test(error.errors[0].message)
    && /native instrumentation cleanup failed/.test(error.errors[1].message));
  assert.equal(stages.at(-1), 'permission-denials', 'cleanup must not replace the original failing phase');
  f.checkRestored();
});

test('a failing cleanup stage reporter cannot prevent native instrumentation restoration', async (t) => {
  const f = await fixture(t);
  await assert.rejects(f.run({ setStage: (stage) => {
    if (stage === 'cleanup') throw new Error('stage reporter failed');
  } }), /stage reporter failed/);
  f.checkRestored();
});

test('a regression attempting an OS browser launch is blocked, recorded as failure, and restored', async (t) => {
  const f = await fixture(t, { forceExternalAttempt: true });
  await assert.rejects(f.run(), /native capability observation failed/);
  assert.deepEqual(f.openedExternal, [], 'the original native launch must never be reached');
  f.checkRestored();
});

test('permission validation rejects absent, malformed, and resolved unexpected permission values', () => {
  for (const result of [null, {}, { notification: 'denied' },
    { notification: 'denied', camera: 'denied', microphone: true },
    { notification: 'denied', camera: 'denied', microphone: 'default' }]) {
    assert.throws(() => assertDeniedPermissions(result));
  }
});

test('renderer permission probe never asks for device contents', async () => {
  const calls = [];
  const context = vm.createContext({ setTimeout, clearTimeout, Notification: { requestPermission: async () => 'denied' },
    navigator: { permissions: { query: async ({ name }) => { calls.push(name); return { state: 'denied' }; } },
      get mediaDevices() { throw new Error('must not access media devices'); },
      get clipboard() { throw new Error('must not access clipboard'); },
      get geolocation() { throw new Error('must not access location'); } } });
  const result = await vm.runInContext(`(${readPermissionDecisions.toString()})()`, context);
  assertDeniedPermissions(result);
  assert.deepEqual(calls, ['camera', 'microphone']);
});
