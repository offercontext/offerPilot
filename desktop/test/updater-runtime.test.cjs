'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');

// Resolve exactly as the installed production updater does, not from the
// electron-builder dev dependency (which already had a fixed runtime).
const updaterRequire = createRequire(require.resolve('electron-updater/package.json'));
const { HttpExecutor } = updaterRequire('builder-util-runtime');
const { ElectronHttpExecutor } = require('electron-updater/out/electronHttpExecutor');

function options() {
  return { protocol: 'https:', hostname: 'updates.example.invalid', path: '/release', headers: {
    Authorization: 'Bearer fixture', AUTHORIZATION: 'Bearer fixture', 'PRIVATE-TOKEN': 'fixture',
    'Private_Token': 'fixture', 'Proxy-Authorization': 'fixture', 'X-Api-Key': 'fixture',
    'X-Auth-Token': 'fixture', 'X-Access-Token': 'fixture', 'X-Gitlab-Token': 'fixture',
    Cookie: 'fixture', 'X-CSRF-Token': 'fixture', Accept: 'application/octet-stream', Range: 'bytes=0-9',
  } };
}

test('production updater resolves the fixed runtime and its actual executor inherits it', () => {
  assert.equal(updaterRequire('builder-util-runtime/package.json').version, '9.7.0');
  assert.ok(new ElectronHttpExecutor() instanceof HttpExecutor);
});

for (const [name, url] of [
  ['foreign host', 'https://cdn.example.invalid/asset'],
  ['port change', 'https://updates.example.invalid:8443/asset'],
  ['HTTPS downgrade', 'http://updates.example.invalid/asset'],
]) {
  test(`installed Electron redirect handler removes credential variants on ${name}`, () => {
    const executor = new ElectronHttpExecutor();
    const request = new EventEmitter();
    let aborted = false;
    request.abort = () => { aborted = true; };
    let redirected;
    executor.addRedirectHandlers(request, options(), error => { throw error; }, 0, value => { redirected = value; });
    request.emit('redirect', 302, 'GET', url);
    assert.equal(aborted, true);
    assert.ok(redirected);
    assert.deepEqual(redirected.headers, { Accept: 'application/octet-stream', Range: 'bytes=0-9', 'User-Agent': 'electron-builder', 'Cache-Control': 'no-cache' });
  });
}

test('same-origin and relative redirects preserve credentials and request headers', () => {
  for (const url of ['https://UPDATES.example.invalid:443/asset', '/asset']) {
    const original = options();
    const redirected = HttpExecutor.prepareRedirectUrlOptions(url, original);
    for (const [key, value] of Object.entries(original.headers)) assert.equal(redirected.headers[key], value);
    assert.equal(redirected.path, '/asset');
  }
});

test('cross-origin redirect chains cannot restore removed credentials', () => {
  const foreign = HttpExecutor.prepareRedirectUrlOptions('https://cdn.example.invalid/asset', options());
  const returned = HttpExecutor.prepareRedirectUrlOptions('https://updates.example.invalid/asset', foreign);
  assert.equal(returned.headers.Authorization, undefined);
  assert.equal(returned.headers['PRIVATE-TOKEN'], undefined);
  assert.equal(returned.headers.Range, 'bytes=0-9');
});

test('redirect failure never dispatches the follow-up request', () => {
  const executor = new ElectronHttpExecutor();
  const request = new EventEmitter();
  request.abort = () => {};
  let failure;
  let followups = 0;
  executor.addRedirectHandlers(request, options(), error => { failure = error; }, executor.maxRedirects + 1, () => { followups++; });
  request.emit('redirect', 302, 'GET', 'https://cdn.example.invalid/asset');
  assert.match(failure.message, /Too many redirects/);
  assert.equal(followups, 0);
});

for (const code of ['EBUSY', 'EACCES']) {
  test(`installed updater finalization handles ${code} through the runtime retry API`, async t => {
    const fs = require('node:fs/promises');
    const path = require('node:path');
    const fsExtra = updaterRequire('fs-extra');
    const { AppUpdater } = require('electron-updater/out/AppUpdater');
    const directory = await fs.mkdtemp(path.join(require('node:os').tmpdir(), 'updater-retry-'));
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const rename = fsExtra.rename;
    let attempts = 0;
    t.mock.method(fsExtra, 'rename', async (...args) => {
      if (++attempts === 1) throw Object.assign(new Error(`${code}: fixture file lock`), { code });
      return rename(...args);
    });
    let completed = 0;
    let cleared = 0;
    let saved = 0;
    const helper = {
      cacheDir: directory, cacheDirForPendingUpdate: directory,
      validateDownloadedPath: async () => null,
      setDownloadedFile: async () => { saved++; },
      clear: async () => { cleared++; },
    };
    const context = { listenerCount: () => 0, getOrCreateDownloadHelper: async () => helper, _logger: { info() {}, warn() {} } };
    const pending = AppUpdater.prototype.executeDownload.call(context, {
      fileExtension: 'exe',
      fileInfo: { url: new URL('https://fixture.invalid/update.exe'), info: { url: 'update.exe' } },
      downloadUpdateOptions: { updateInfoAndProvider: { info: { version: '1.2.0' } } },
      task: async filename => fs.writeFile(filename, 'fixture installer bytes'),
      done: async () => { completed++; },
    });
    if (code === 'EBUSY') {
      assert.deepEqual(await pending, [path.join(directory, 'update.exe')]);
      assert.equal(await fs.readFile(path.join(directory, 'update.exe'), 'utf8'), 'fixture installer bytes');
      assert.equal(attempts, 2);
      assert.equal(saved, 1);
      assert.equal(completed, 1);
      assert.equal(cleared, 0);
    } else {
      await assert.rejects(pending, { code: 'EACCES' });
      assert.equal(attempts, 1);
      assert.equal(saved, 0);
      assert.equal(completed, 0);
      assert.equal(cleared, 1);
    }
  });
}

test('installed NSIS updater honors application signature anchors and verifier results', async () => {
  const { NsisUpdater } = require('electron-updater/out/NsisUpdater');
  const { productionAdapter } = require('../updater.cjs');
  const policy = { provider: 'github', owner: 'offercontext', repo: 'offerPilot', publisherName: 'CN=Fixture Publisher' };
  const adapter = Object.create(NsisUpdater.prototype);
  adapter.configOnDisk = { value: Promise.resolve(policy) };
  adapter._verifyUpdateCodeSignature = () => { throw Error('must replace the default verifier'); };
  const original = adapter.verifyUpdateCodeSignature;
  assert.equal(await productionAdapter(policy, () => adapter), adapter);
  assert.notEqual(adapter.verifyUpdateCodeSignature, original);
  // Exercise the real NSIS call contract without PowerShell or real files.
  const calls = [];
  adapter.verifyUpdateCodeSignature = async (names, filename) => { calls.push({ names, filename }); return null; };
  assert.equal(await adapter.verifySignature('fixture.exe'), null);
  assert.deepEqual(calls, [{ names: [policy.publisherName], filename: 'fixture.exe' }]);
  adapter.verifyUpdateCodeSignature = async () => 'Fixture signature mismatch';
  assert.equal(await adapter.verifySignature('fixture.exe'), 'Fixture signature mismatch');
  adapter.configOnDisk = { value: Promise.resolve({ ...policy, publisherName: undefined }) };
  await assert.rejects(productionAdapter(policy, () => adapter), /missing or mismatched/);
});

test('installed updater keeps semantic version ordering, downgrade and rollout gates', async () => {
  const { NsisUpdater } = require('electron-updater/out/NsisUpdater');
  const { createUpdater } = require('../updater.cjs');
  const adapter = new NsisUpdater(null, { version: '1.2.0' });
  const updater = createUpdater({ version: '1.2.0', adapter });
  assert.equal(adapter.autoDownload, false);
  assert.equal(adapter.autoInstallOnAppQuit, false);
  assert.equal(adapter.allowPrerelease, false);
  assert.equal(adapter.disableWebInstaller, true);
  assert.equal(await adapter.isUpdateAvailable({ version: '1.1.9' }), false);
  assert.equal(await adapter.isUpdateAvailable({ version: '1.2.0' }), false);
  assert.equal(await adapter.isUpdateAvailable({ version: '1.10.0' }), true);
  await assert.rejects(adapter.isUpdateAvailable({ version: 'invalid' }), { code: 'ERR_UPDATER_INVALID_VERSION' });
  adapter.isUserWithinRollout = () => false;
  assert.equal(await adapter.isUpdateAvailable({ version: '1.10.0' }), false);
  updater.dispose();
});

test('installed NSIS download rejects signature failures and disabled web installers', async () => {
  const { NsisUpdater } = require('electron-updater/out/NsisUpdater');
  for (const scenario of ['valid', 'invalid-signature', 'web-installer']) {
    const adapter = Object.create(NsisUpdater.prototype);
    adapter.configOnDisk = { value: Promise.resolve({ publisherName: 'CN=Fixture Publisher' }) };
    adapter._verifyUpdateCodeSignature = async () => scenario === 'invalid-signature' ? 'Fixture mismatch' : null;
    let downloads = 0;
    let clears = 0;
    adapter.httpExecutor = { download: async () => { downloads++; } };
    const webInstaller = scenario === 'web-installer';
    adapter.executeDownload = ({ task }) => task('fixture.exe', {}, webInstaller ? 'package.7z' : null, async () => { clears++; });
    const file = { url: new URL('https://fixture.invalid/fixture.exe'), info: { url: 'fixture.exe' }, ...(webInstaller ? { packageInfo: { path: 'https://fixture.invalid/package.7z' } } : {}) };
    const result = adapter.doDownloadUpdate({
      updateInfoAndProvider: { info: { version: '1.10.0' }, provider: { resolveFiles: () => [file] } },
      disableWebInstaller: true, disableDifferentialDownload: true,
    });
    if (scenario === 'valid') {
      await result;
      assert.equal(downloads, 1);
      assert.equal(clears, 0);
    } else {
      await assert.rejects(result, { code: webInstaller ? 'ERR_UPDATER_WEB_INSTALLER_DISABLED' : 'ERR_UPDATER_INVALID_SIGNATURE' });
      assert.equal(downloads, webInstaller ? 0 : 1);
      assert.equal(clears, webInstaller ? 0 : 1);
    }
  }
});
