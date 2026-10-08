import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

// Deliberately synthetic, including NUL and non-UTF-8 bytes. Never export owner data.
export const CAPABILITY_BYTES = Object.freeze([79, 102, 102, 101, 114, 80, 105, 108, 111, 116,
  32, 105, 110, 115, 116, 97, 108, 108, 101, 100, 32, 112, 114, 111, 98, 101, 10, 0, 255, 128, 13, 10]);
const FILENAME = 'offerpilot-synthetic-capability.bin';
const EXTERNAL_URL = 'https://example.invalid/offerpilot-installed-capability-probe';

// Serialized into Electron's main process. Dialog choices are automated; an
// unexpected shell.openExternal attempt is blocked, recorded, and fails closed.
// Existing permission, window-open, webRequest and application policy handlers
// remain untouched. Extra event listeners observe the real native decision.
export function nativeCapabilityInstrumentation({ dialog, shell }, args) {
  const key = Symbol.for(args.key);
  if (args.operation === 'install') {
    if (globalThis[key]) throw new Error('capability instrumentation already installed');
    const owner = args.owner;
    const contents = owner.webContents;
    if (contents.isDestroyed() || contents.getURL() !== args.ownerURL) throw new Error('capability owner mismatch');
    const session = contents.session;
    const state = { contents, session, owner, origin: new URL(args.ownerURL).origin,
      active: null, downloads: [], dialogs: [], messages: [], navigations: [], failures: [], externalLaunchAttempts: 0,
      items: new Map(), downloadRecords: new WeakMap(),
      originalSave: dialog.showSaveDialogSync, originalMessage: dialog.showMessageBox,
      originalExternal: shell.openExternal };
    const fail = (code) => state.failures.push(code);
    state.beforeDownload = (_event, item, source, frame) => {
      const active = state.active;
      if (!active || !['cancel', 'save'].includes(active.mode)) { fail('unexpected-download'); return; }
      const record = { mode: active.mode, sameOwner: source === contents, mainFrame: frame === contents.mainFrame,
        chainMatched: false, initiatorMatched: false, filenameMatched: false, defaultPrevented: null,
        terminal: null, state: null, savePathMatched: false, receivedBytes: null, totalBytes: null };
      state.downloads.push(record);
      try {
        const urls = item.getURLChain();
        record.chainMatched = urls.length === 1 && urls[0] === active.url;
        record.initiatorMatched = new URL(item.getInitiatorOrigin()).origin === state.origin;
        record.filenameMatched = item.getFilename() === active.filename;
      } catch { fail('download-metadata-unavailable'); }
      const onDone = (_event, terminalState) => {
        state.items.delete(item);
        record.terminal = 'done';
        record.state = terminalState;
        try {
          record.savePathMatched = item.getSavePath() === active.savePath;
          record.receivedBytes = item.getReceivedBytes();
          record.totalBytes = item.getTotalBytes();
        } catch { fail('download-terminal-metadata-unavailable'); }
      };
      state.items.set(item, { record, onDone });
      state.downloadRecords.set(item, record);
      item.once('done', onDone);
    };
    state.afterDownload = (event, item) => {
      const record = state.downloadRecords.get(item);
      if (!record) return;
      if (typeof event.defaultPrevented !== 'boolean') { fail('native-prevention-unobservable'); return; }
      record.defaultPrevented = event.defaultPrevented;
      // preventDefault() is terminal cancellation. Electron invalidates this
      // DownloadItem after the event and does not promise a later `done` event.
      if (record.defaultPrevented) {
        if (!record.terminal) record.terminal = 'will-download-prevented';
        const pending = state.items.get(item);
        if (pending) item.removeListener('done', pending.onDone);
        state.items.delete(item);
      }
    };
    state.onNavigate = (event, url) => {
      if (url !== state.active?.externalURL) { fail('unexpected-navigation'); return; }
      state.navigations.push({ urlMatched: true,
        prevented: event.defaultPrevented === true });
    };
    state.saveWrapper = (win, options) => {
      const active = state.active;
      const record = state.downloads.at(-1);
      const expected = win === owner && active && ['cancel', 'save'].includes(active.mode)
        && record?.mode === active.mode && record.sameOwner && record.mainFrame
        && record.chainMatched && record.initiatorMatched && record.filenameMatched
        && options?.defaultPath === active.filename
        && options?.properties?.includes('showOverwriteConfirmation')
        && options?.properties?.includes('dontAddToRecent');
      state.dialogs.push({ mode: active?.mode ?? 'unexpected', expected: Boolean(expected),
        choice: expected && active.mode === 'save' ? 'controlled-synthetic-path' : 'cancel' });
      if (!expected) { fail('unexpected-save-dialog'); return undefined; }
      return active.mode === 'save' ? active.savePath : undefined;
    };
    state.messageWrapper = async (win, options) => {
      const expected = win === owner && state.active?.mode === 'external'
        && options?.detail === state.active.externalURL && options?.cancelId === 0 && options?.defaultId === 0
        && Array.isArray(options?.buttons) && options.buttons.length === 2;
      state.messages.push({ expected: Boolean(expected), choice: 'cancel' });
      if (!expected) fail('unexpected-message-dialog');
      return { response: 0, checkboxChecked: false };
    };
    state.externalWrapper = async () => {
      state.externalLaunchAttempts++;
      fail('unexpected-external-browser-launch');
      // This is a safety tripwire, never fabricated successful OS-launch proof.
      // The production cancellation path must make zero calls to pass.
      throw new Error('external launch blocked during capability probe');
    };
    globalThis[key] = state;
    // Register before changing dialog functions, so restore can recover even if
    // a later setup step throws. No existing listener is removed or replaced.
    session.prependListener('will-download', state.beforeDownload);
    session.on('will-download', state.afterDownload);
    contents.on('will-navigate', state.onNavigate);
    dialog.showSaveDialogSync = state.saveWrapper;
    dialog.showMessageBox = state.messageWrapper;
    shell.openExternal = state.externalWrapper;
    if (dialog.showSaveDialogSync !== state.saveWrapper || dialog.showMessageBox !== state.messageWrapper
      || shell.openExternal !== state.externalWrapper) {
      throw new Error('capability dialog automation could not be installed');
    }
    return { installed: true };
  }
  const state = globalThis[key];
  if (!state) {
    if (args.operation === 'restore') return { restored: true, absent: true };
    throw new Error('capability instrumentation missing');
  }
  if (args.operation === 'arm') {
    state.active = args.active;
    return { armed: true };
  }
  if (args.operation === 'snapshot') {
    return { downloads: state.downloads, dialogs: state.dialogs, messages: state.messages,
      navigations: state.navigations, failures: state.failures, externalLaunchAttempts: state.externalLaunchAttempts,
      ownerURLUnchanged: !state.contents.isDestroyed() && state.contents.getURL() === args.ownerURL };
  }
  if (args.operation === 'restore') {
    const failures = [];
    const attempt = (fn) => { try { fn(); } catch { failures.push('instrumentation-restore-failed'); } };
    attempt(() => state.session.removeListener('will-download', state.beforeDownload));
    attempt(() => state.session.removeListener('will-download', state.afterDownload));
    attempt(() => state.contents.removeListener('will-navigate', state.onNavigate));
    for (const [item, { onDone }] of state.items) attempt(() => item.removeListener('done', onDone));
    attempt(() => { dialog.showSaveDialogSync = state.originalSave; });
    attempt(() => { dialog.showMessageBox = state.originalMessage; });
    attempt(() => { shell.openExternal = state.originalExternal; });
    if (dialog.showSaveDialogSync !== state.originalSave || dialog.showMessageBox !== state.originalMessage
      || shell.openExternal !== state.originalExternal) {
      failures.push('instrumentation-restore-failed');
    }
    delete globalThis[key];
    if (failures.length) throw new Error('capability instrumentation could not be fully restored');
    return { restored: true };
  }
  throw new Error('unknown capability instrumentation operation');
}

// These renderer functions must stay standalone for Playwright serialization.
export async function readPermissionDecisions(timeoutMs = 15000) {
  if (typeof Notification === 'undefined' || typeof Notification.requestPermission !== 'function') {
    throw new Error('Notification permission API missing');
  }
  if (!navigator.permissions || typeof navigator.permissions.query !== 'function') {
    throw new Error('Chromium Permissions API missing');
  }
  // Read camera/microphone permission state only: never open a device or acquire
  // media, clipboard, location, file, credential, or owner data.
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('permission decision timed out')), timeoutMs);
  });
  try {
    return await Promise.race([deadline, (async () => {
      const camera = (await navigator.permissions.query({ name: 'camera' })).state;
      const microphone = (await navigator.permissions.query({ name: 'microphone' })).state;
      const notification = await Notification.requestPermission();
      return { notification, camera, microphone };
    })()]);
  } finally { clearTimeout(timer); }
}

export function assertDeniedPermissions(result) {
  for (const permission of ['notification', 'camera', 'microphone']) {
    assert.equal(result?.[permission], 'denied', `${permission} must be explicitly denied`);
  }
}

function makeRendererBlob(bytes) {
  return URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: 'application/octet-stream' }));
}

function startRendererDownload({ url, filename }) {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  try { anchor.click(); } finally { anchor.remove(); }
}

function assertNativeDownload(record, mode, byteLength) {
  assert.ok(record, `${mode}: native will-download event missing`);
  for (const key of ['sameOwner', 'mainFrame', 'chainMatched', 'initiatorMatched', 'filenameMatched']) {
    assert.equal(record[key], true, `${mode}: ${key}`);
  }
  assert.equal(record.defaultPrevented, mode === 'cancel', `${mode}: native prevention`);
  if (mode === 'cancel') {
    assert.ok(record.terminal === 'will-download-prevented'
      || (record.terminal === 'done' && record.state === 'cancelled'), 'cancel: native terminal cancellation required');
  } else {
    assert.equal(record.terminal, 'done', 'save: native done event required');
    assert.equal(record.state, 'completed', 'save: native completed state required');
    assert.equal(record.savePathMatched, true, 'save: native path must equal controlled path');
    assert.equal(record.receivedBytes, byteLength, 'save: native received byte count');
    assert.equal(record.totalBytes, byteLength, 'save: native total byte count');
  }
}

export async function probeInstalledCapabilities({ app, page, directory, timeoutMs = 15000, setStage = () => {} }) {
  assert.ok(directory, 'capability evidence directory required');
  assert.ok(Number.isFinite(timeoutMs) && timeoutMs > 0, 'positive capability timeout required');
  assert.equal(typeof setStage, 'function', 'capability stage callback required');
  await fs.mkdir(directory, { recursive: true });
  const root = await fs.mkdtemp(path.join(path.resolve(directory), 'capability-probes-'));
  const saveDirectory = path.join(root, 'saved');
  await fs.mkdir(saveDirectory);
  const savePath = path.join(saveDirectory, FILENAME);
  const bytes = Buffer.from(CAPABILITY_BYTES);
  const key = `offerpilot.installed-capabilities.${randomUUID()}`;
  const ownerURL = page.url();
  const owner = await app.browserWindow(page);
  const urls = [];
  const run = (operation, extra = {}) => app.evaluate(nativeCapabilityInstrumentation,
    { operation, key, ownerURL, ...extra });
  const snapshot = () => run('snapshot');
  const waitFor = async (predicate, label) => {
    const deadline = Date.now() + timeoutMs;
    do {
      const value = await snapshot();
      assert.deepEqual(value.failures, [], 'native capability observation failed');
      if (predicate(value)) return value;
      await new Promise((resolve) => setTimeout(resolve, 25));
    } while (Date.now() < deadline);
    throw new Error(`capability ${label}: native terminal evidence timed out`);
  };
  let evidence;
  let primaryError;
  try {
    setStage('native-instrumentation');
    await run('install', { owner });
    for (const mode of ['cancel', 'save']) {
      setStage(`blob-${mode}`);
      const url = await page.evaluate(makeRendererBlob, CAPABILITY_BYTES);
      urls.push(url);
      assert.equal(new URL(url).origin, new URL(ownerURL).origin, 'Blob must belong to installed owner origin');
      await run('arm', { active: { mode, url, filename: FILENAME, savePath } });
      await page.evaluate(startRendererDownload, { url, filename: FILENAME });
      const observed = await waitFor((value) => value.downloads.some((item) => item.mode === mode && item.terminal), mode);
      const records = observed.downloads.filter((item) => item.mode === mode);
      assert.equal(records.length, 1, `${mode}: exactly one native download required`);
      assertNativeDownload(records[0], mode, bytes.length);
      const dialogs = observed.dialogs.filter((item) => item.mode === mode);
      assert.equal(dialogs.length, 1, `${mode}: exactly one application save dialog required`);
      assert.equal(dialogs[0].expected, true);
      assert.equal(dialogs[0].choice, mode === 'cancel' ? 'cancel' : 'controlled-synthetic-path');
      // Check the actual directory used by the next approved native download,
      // not an unrelated empty fixture directory. This is a point-in-time check
      // before the separate successful save intentionally creates its file.
      if (mode === 'cancel') assert.deepEqual(await fs.readdir(saveDirectory), [], 'cancelled download directory must stay empty');
      else {
        assert.deepEqual(await fs.readdir(saveDirectory), [FILENAME], 'save must create only the selected synthetic file');
        assert.deepEqual(await fs.readFile(savePath), bytes, 'saved bytes must exactly match the synthetic Blob');
      }
    }
    setStage('permission-denials');
    await run('arm', { active: { mode: 'permissions' } });
    const permissions = await page.evaluate(readPermissionDecisions, timeoutMs);
    // Assertions deliberately sit outside API exception handlers. A resolved
    // unexpected grant, an absent API, and an exception are all failing probes.
    assertDeniedPermissions(permissions);
    setStage('external-cancel');
    await run('arm', { active: { mode: 'external', externalURL: EXTERNAL_URL } });
    await page.evaluate((url) => { window.location.href = url; }, EXTERNAL_URL);
    const final = await waitFor((value) => value.navigations.length > 0 && value.messages.length > 0, 'external cancellation');
    assert.deepEqual(final.navigations, [{ urlMatched: true, prevented: true }], 'external navigation must be prevented natively');
    assert.deepEqual(final.messages, [{ expected: true, choice: 'cancel' }], 'external confirmation must be safely cancelled');
    assert.equal(final.externalLaunchAttempts, 0, 'cancel must never attempt an external browser launch');
    assert.equal(final.ownerURLUnchanged, true, 'owner must remain on its original page');
    assert.equal(page.url(), ownerURL, 'renderer must remain on its original page');
    assert.equal(final.downloads.length, 2);
    assert.equal(final.dialogs.length, 2);
    assert.deepEqual(await fs.readdir(saveDirectory), [FILENAME]);
    assert.deepEqual(await fs.readFile(savePath), bytes);
    evidence = {
      mechanism: 'real-installed-owner-renderer-and-native-electron-events',
      nativeDialogChoicesAutomated: true, nativePointerInteractionValidated: false,
      permissionHandlersReplaced: false, webRequestHandlersReplaced: false,
      downloadBehavior: 'electron-native-default-required',
      evidenceDirectory: path.basename(root),
      blobCancel: { ...final.downloads[0], downloadDirectoryEmptyAfterCancel: true, approvedFileAbsentAfterCancel: true },
      blobSave: { ...final.downloads[1], exactSyntheticBytes: true, bytes: bytes.length,
        sha256: createHash('sha256').update(bytes).digest('hex'), file: `saved/${FILENAME}` },
      permissions: { ...permissions, notificationRequest: 'real-api', mediaProbe: 'permission-state-query-only',
        deviceAcquisitionAttempted: false },
      externalNavigation: { ...final.navigations[0], confirmationChoice: 'cancel', ownerURLUnchanged: true,
        externalLaunchAttempts: final.externalLaunchAttempts, nativeExternalLaunchBlockedDuringProbe: true,
        systemBrowserLaunchValidated: false },
    };
  } catch (error) { primaryError = error; }
  const cleanupErrors = [];
  if (!primaryError) {
    try { setStage('cleanup'); }
    catch (error) { primaryError = error; }
  }
  // Each cleanup is independent. Even renderer failure must restore main-process
  // native dialogs and observers. Never swallow restoration failures.
  for (const url of urls) {
    try { await page.evaluate((value) => URL.revokeObjectURL(value), url); }
    catch { cleanupErrors.push(new Error('capability Blob URL cleanup failed')); }
  }
  try { await run('restore'); }
  catch { cleanupErrors.push(new Error('capability native instrumentation cleanup failed')); }
  try { await owner.dispose(); }
  catch { cleanupErrors.push(new Error('capability owner handle cleanup failed')); }
  if (primaryError && cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'capability probe and cleanup failed');
  if (primaryError) throw primaryError;
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'capability probe cleanup failed');
  return { ...evidence, instrumentationRestored: true };
}
