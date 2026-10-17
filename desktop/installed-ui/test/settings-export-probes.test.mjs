import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { SETTINGS_EXPORTS, inspectSyntheticExportProfile, probeSettingsExport } from '../settings-export-probes.mjs';
import { settingsPayload, configPayload, makeWorkspaceZip } from './backup-fixtures.mjs';

const { createCapabilities } = createRequire(import.meta.url)('../../capabilities.cjs');
const origin = 'http://127.0.0.1:18420';
const confirmationSecret = 'synthetic-confirmation-only-123456789';
async function fixture(t, options = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-settings-export-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const dataDirectory = path.join(directory, 'data');
  const temporaryDirectory = path.join(directory, 'transient');
  await fs.mkdir(dataDirectory); await fs.mkdir(temporaryDirectory);
  await fs.writeFile(path.join(dataDirectory, 'config.json'), JSON.stringify({ ...configPayload(), confirmation_secret: confirmationSecret,
    ...(options.config || {}) }));
  const session = new EventEmitter();
  session.setPermissionCheckHandler = fn => { session.check = fn; };
  session.setPermissionRequestHandler = fn => { session.request = fn; };
  const contents = new EventEmitter();
  Object.assign(contents, { session, mainFrame: {}, isDestroyed: () => false,
    getURL: () => `${origin}/?view=settings`, setWindowOpenHandler: fn => { contents.open = fn; } });
  let disposed = 0;
  let cancelled = 0;
  let clicked = 0;
  let lateDownload = () => {};
  const owner = { webContents: contents, dispose: async () => { disposed++; } };
  const dialog = { showSaveDialogSync: () => { throw new Error('native dialog was not automated'); },
    showMessageBox: async () => { throw new Error('unexpected native message'); } };
  const shell = { openExternal: async () => { throw new Error('unexpected OS launch'); } };
  const originals = { ...dialog, ...shell };
  const policy = createCapabilities({ origin, desktopSession: session, isTrustedContents: value => value === contents,
    dialog, shell, BrowserWindow: { fromWebContents: value => value === contents ? owner : null } });
  policy.installWindowPolicy(owner);
  const baseline = { download: session.listeners('will-download'), navigate: contents.listeners('will-navigate'),
    request: session.request, check: session.check, open: contents.open };
  session.setPermissionCheckHandler = session.setPermissionRequestHandler = () => { throw new Error('policy must remain installed'); };
  contents.setWindowOpenHandler = () => { throw new Error('window policy must remain installed'); };
  session.webRequest = new Proxy({}, { get() { throw new Error('webRequest policy must remain installed'); } });
  const page = new EventEmitter();
  const frame = {};
  page.url = () => contents.getURL();
  page.mainFrame = () => frame;
  const app = { browserWindow: async candidate => { assert.equal(candidate, page); return owner; },
    evaluate: async (fn, args) => {
      if (args.operation === 'restore' && options.lateDownload) lateDownload();
      if (args.operation === 'restore' && options.lateWrite) page.emit('request', { url: () => `${origin}/api/unexpected`, method: () => 'POST' });
      const result = fn({ dialog, shell }, args);
      if (options.restoreFailure && args.operation === 'restore') throw new Error('native restoration acknowledgement failed');
      return structuredClone(result);
    } };
  async function run(kind, mode, extra = {}) {
    const spec = SETTINGS_EXPORTS[kind];
    const source = kind === 'settings' ? Buffer.from(JSON.stringify(settingsPayload())) : makeWorkspaceZip();
    const bytes = kind === 'settings' ? Buffer.from(JSON.stringify(JSON.parse(source), null, 2)) : source;
    const request = { url: () => `${origin}${spec.endpoint}`, method: () => options.write ? 'POST' : 'GET', frame: () => options.wrongFrame ? {} : frame };
    const clickButton = async label => {
      clicked++;
      assert.equal(label, spec.label);
      if (options.buttonFailure) throw new Error('button is not actionable');
      page.emit('request', request);
      if (options.externalRequest) page.emit('request', { url: () => 'https://example.invalid/', method: () => 'GET' });
      const response = { request: () => request, status: () => options.responseStatus || 200,
        body: async () => {
          if (options.responseHangs) return new Promise(() => {});
          if (options.delayedBody) await new Promise(resolve => setTimeout(resolve, 40));
          if (options.duplicateRawField) return Buffer.from(source.toString().replace('"log_level":', '"log_level":"hidden-unapproved","log_level":'));
          if (options.invalidUtf8) return Buffer.concat([source.subarray(0, 1), Buffer.from([0xff]), source.subarray(1)]);
          if (options.rawSecret) return Buffer.from(source.toString().replace('{', `{"confirmation_secret":"${confirmationSecret}",`));
          return source;
        } };
      page.emit('response', response);
      if (options.failedGet) page.emit('requestfailed', request);
      if (options.noDownload) return;
      const item = new EventEmitter();
      let selected;
      const url = options.foreignBlob ? 'blob:https://example.invalid/export' : `blob:${origin}/real-ui-generated`;
      Object.assign(item, { getURLChain: () => options.redirect ? [url, url] : [url], getInitiatorOrigin: () => origin,
        getFilename: () => options.wrongFilename ? 'unexpected.bin' : spec.filename,
        setSavePath: value => { selected = value; }, getSavePath: () => selected,
        getReceivedBytes: () => bytes.length, getTotalBytes: () => bytes.length,
        cancel: () => { cancelled++; item.emit('done', {}, 'cancelled'); } });
      const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
      lateDownload = () => session.emit('will-download', event, item, contents, contents.mainFrame);
      session.emit('will-download', event, item, contents, options.missingFrame ? null : contents.mainFrame);
      if (event.defaultPrevented) {
        if (options.cancelWritesFile) await fs.writeFile(path.join(temporaryDirectory, (await fs.readdir(temporaryDirectory))[0], 'unexpected.bin'), 'bad');
        return;
      }
      if (selected) await fs.writeFile(selected, options.corruptSave ? Buffer.from('corrupt') : bytes);
      if (!options.noDone) item.emit('done', {}, options.interrupted ? 'interrupted' : 'completed');
      if (options.doubleDownload) session.emit('will-download', event, item, contents, contents.mainFrame);
    };
    return probeSettingsExport({ app, page, kind, mode, dataDirectory, temporaryDirectory,
      freshProfileConfirmed: options.noFreshProof ? false : true, clickButton, timeoutMs: 150, ...extra });
  }
  async function assertClean({ expectedDisposed = 1 } = {}) {
    assert.deepEqual(await fs.readdir(temporaryDirectory), []);
    assert.equal(dialog.showSaveDialogSync, originals.showSaveDialogSync);
    assert.equal(dialog.showMessageBox, originals.showMessageBox);
    assert.equal(shell.openExternal, originals.openExternal);
    assert.deepEqual(session.listeners('will-download'), baseline.download);
    assert.deepEqual(contents.listeners('will-navigate'), baseline.navigate);
    assert.equal(session.request, baseline.request); assert.equal(session.check, baseline.check); assert.equal(contents.open, baseline.open);
    assert.equal(page.listenerCount('request') + page.listenerCount('response') + page.listenerCount('requestfailed'), 0);
    assert.equal(disposed, expectedDisposed);
  }
  return { run, assertClean, dataDirectory, temporaryDirectory, clicked: () => clicked, cancelled: () => cancelled };
}

for (const kind of Object.keys(SETTINGS_EXPORTS)) for (const mode of ['cancel', 'save']) {
  test(`${kind} real policy ${mode}: button GET, native ownership/terminal, structure and no retained export`, async t => {
    const f = await fixture(t);
    const result = await f.run(kind, mode);
    assert.equal(result.native.sameOwner, true); assert.equal(result.native.mainFrame, true);
    assert.equal(result.requestEvidence.successfulOwnerResponse, true);
    assert.equal(result.requestEvidence.unexpectedWrites, 0);
    assert.equal(result.nativeDialogPointerValidated, false);
    assert.equal(result.nativeDialogChoicesAutomated, true);
    assert.equal(result.savedBytesMatchActualUiDownload, mode === 'save');
    assert.equal(result.cancelledDirectoryEmpty, mode === 'cancel');
    assert.equal(result.transientExportRemoved, true);
    assert.equal(result.structure.valid, true);
    if (mode === 'save') { assert.ok(result.bytes > 0); assert.match(result.sha256, /^[a-f0-9]{64}$/); }
    assert.doesNotMatch(JSON.stringify(result), /confirmation_secret|auth_token|provider.*key|config.json|data.db/);
    await f.assertClean();
  });
}
test('response-body hydration is bounded and may finish after native cancellation', async t => {
  const f = await fixture(t, { delayedBody: true });
  assert.equal((await f.run('settings', 'cancel')).structure.valid, true);
  await f.assertClean();
});
for (const [option, mode] of [['responseHangs','cancel'], ['buttonFailure','save'], ['noDownload','cancel'], ['noDone','save'],
  ['foreignBlob','save'], ['wrongFilename','save'], ['redirect','save'], ['missingFrame','save'], ['corruptSave','save'],
  ['cancelWritesFile','cancel'], ['interrupted','save'], ['doubleDownload','save'], ['write','save'], ['wrongFrame','save'],
  ['failedGet','save'], ['externalRequest','save'], ['restoreFailure','save'], ['responseStatus','save'],
  ['duplicateRawField','save'], ['invalidUtf8','save'], ['rawSecret','save'], ['lateDownload','save'], ['lateWrite','save']]) {
  test(`${option} cannot become an export PASS and all transient files/instrumentation are removed`, async t => {
    const f = await fixture(t, { [option]: option === 'responseStatus' ? 500 : true });
    await assert.rejects(f.run('settings', mode));
    await f.assertClean();
    if (option === 'noDone') assert.equal(f.cancelled(), 1, 'incomplete native save must be cancelled before temporary cleanup');
  });
}
for (const options of [{ noFreshProof: true }, { config: { api_key: 'unapproved-provider-key' } },
  { config: { providers: [{ api_key: 'unapproved-provider-key' }] } }, { config: { auth_token: 'unapproved-auth' } }]) {
  test('preflight refuses a non-fresh or credentialed profile before clicking or creating exports', async t => {
    const f = await fixture(t, options);
    await assert.rejects(f.run('workspace', 'save'));
    assert.equal(f.clicked(), 0);
    await f.assertClean({ expectedDisposed: 0 });
  });
}
test('profile inspection returns secrets only privately for absence checks, never the raw config', async t => {
  const f = await fixture(t);
  assert.deepEqual(Object.keys(await inspectSyntheticExportProfile(f.dataDirectory, true)), ['forbiddenValues']);
});
