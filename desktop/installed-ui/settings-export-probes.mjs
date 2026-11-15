import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { nativeCapabilityInstrumentation, assertNativeDownload } from './capability-probes.mjs';
import { validateSettingsBackup, validateWorkspaceBackup } from './backup-format.mjs';

export const SETTINGS_EXPORTS = Object.freeze({
  settings: Object.freeze({ label: '导出备份', filename: 'offerpilot-settings-backup-v1.json', endpoint: '/api/settings/backup' }),
  workspace: Object.freeze({ label: '导出完整数据', filename: 'offerpilot-backup.zip', endpoint: '/api/backups/export' }),
});

async function readPrivateJson(filename, maximum) {
  const stat = await fs.lstat(filename);
  assert.ok(stat.isFile() && !stat.isSymbolicLink() && stat.size > 0 && stat.size <= maximum,
    'synthetic profile metadata must be a bounded regular file');
  try { return JSON.parse(await fs.readFile(filename, 'utf8')); }
  catch { throw new Error('synthetic profile metadata is not valid JSON'); }
}

export async function inspectSyntheticExportProfile(dataDirectory, freshProfileConfirmed) {
  // This confirmation is supplied only after smoke.mjs proved real APPDATA did
  // not exist BEFORE installation/launch. Never create a second fake profile.
  assert.equal(freshProfileConfirmed, true, 'export requires the pre-launch fresh real-profile proof');
  const directory = await fs.lstat(dataDirectory);
  assert.ok(directory.isDirectory() && !directory.isSymbolicLink(), 'real synthetic data directory required');
  const config = await readPrivateJson(path.join(dataDirectory, 'config.json'), 1024 * 1024);
  // Do not include actual values in assertions/errors or return the config.
  assert.ok(config.api_key === '' && config.auth_token === '', 'synthetic profile must have no provider or auth credentials');
  assert.ok(Array.isArray(config.providers) && config.providers.every(provider => provider.api_key === ''),
    'synthetic profile must have no provider credentials');
  assert.ok(typeof config.confirmation_secret === 'string' && config.confirmation_secret.length >= 16,
    'synthetic profile confirmation guard must remain configured');
  const forbiddenValues = [config.confirmation_secret];
  try {
    const journal = await readPrivateJson(path.join(dataDirectory, 'agent-journal-key.json'), 65536);
    assert.ok(typeof journal.secret === 'string' && journal.secret.length >= 16, 'journal key metadata is invalid');
    forbiddenValues.push(journal.secret);
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  // Secrets remain private memory for absence checks, never written to reports.
  return { forbiddenValues };
}

export async function probeSettingsExport({ app, page, kind, mode, dataDirectory, temporaryDirectory,
  freshProfileConfirmed, clickButton, timeoutMs = 20000, setStage = () => {} }) {
  const spec = SETTINGS_EXPORTS[kind];
  assert.ok(Object.hasOwn(SETTINGS_EXPORTS, kind) && spec && ['cancel', 'save'].includes(mode), 'known Settings export and native choice required');
  assert.equal(typeof clickButton, 'function');
  assert.ok(Number.isFinite(timeoutMs) && timeoutMs > 0);
  setStage('settings-export-profile');
  const { forbiddenValues } = await inspectSyntheticExportProfile(dataDirectory, freshProfileConfirmed);
  // This directory is outside the upload whitelist. Only transient exports go
  // here; cleanup is required on pass AND failure. Raw exports are never evidence.
  const root = await fs.mkdtemp(path.join(temporaryDirectory, 'offerpilot-settings-export-'));
  const savePath = path.join(root, spec.filename);
  const ownerURL = page.url();
  const origin = new URL(ownerURL).origin;
  const key = `offerpilot.settings-export.${randomUUID()}`;
  let owner;
  let installed = false;
  let result;
  let primaryError;
  let responsePayload;
  let expectedSavedPayload;
  let finalNativeBaseline;
  let responseReadDone = false;
  let responseReadFailed = false;
  let cleanupStarted = false;
  const requestEvidence = { exportRequests: 0, exportResponses: 0, unexpectedWrites: 0, externalRequests: 0,
    failedExportRequests: 0, successfulOwnerResponse: false };
  const exportRequest = request => {
    const url = new URL(request.url());
    return url.origin === origin && url.pathname === spec.endpoint && !url.search && !url.hash;
  };
  const onRequest = request => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) requestEvidence.unexpectedWrites++;
    const url = new URL(request.url());
    if (['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) && url.origin !== origin) requestEvidence.externalRequests++;
    if (exportRequest(request)) requestEvidence.exportRequests++;
  };
  const onResponse = response => {
    const request = response.request();
    if (!exportRequest(request)) return;
    requestEvidence.exportResponses++;
    requestEvidence.successfulOwnerResponse = response.status() === 200 && request.method() === 'GET'
      && request.frame() === page.mainFrame();
    // Read only this actual UI response, never a second export with a new timestamp.
    void response.body().then(body => {
      if (cleanupStarted) return;
      if (body.length <= 0 || body.length > 64 * 1024 * 1024) { responseReadFailed = true; return; }
      responsePayload = Buffer.from(body);
    }, () => { responseReadFailed = true; }).finally(() => { responseReadDone = true; });
  };
  const onFailure = request => { if (exportRequest(request)) requestEvidence.failedExportRequests++; };
  const run = (operation, extra = {}) => app.evaluate(nativeCapabilityInstrumentation, { operation, key, ownerURL, ...extra });
  const assertRequests = () => {
    assert.equal(requestEvidence.unexpectedWrites, 0, 'Settings exports must not issue HTTP writes');
    assert.equal(requestEvidence.externalRequests, 0, 'Settings exports must not contact external services');
    assert.equal(requestEvidence.failedExportRequests, 0, 'Settings export GET failed');
  };
  try {
    owner = await app.browserWindow(page);
    // Mark before install so partial installation is independently restored.
    installed = true;
    await run('install', { owner });
    page.on('request', onRequest);
    page.on('response', onResponse);
    page.on('requestfailed', onFailure);
    await run('arm', { active: { mode, urlPolicy: 'owner-settings-export-blob', filename: spec.filename, savePath } });
    setStage('settings-export-click');
    await clickButton(spec.label); // only the public Settings button, no fabricated Blob or GET
    setStage('settings-export-native-terminal');
    const deadline = Date.now() + timeoutMs;
    let snapshot;
    do {
      assertRequests();
      snapshot = await run('snapshot');
      assert.deepEqual(snapshot.failures, [], 'native Settings export observation failed');
      if (snapshot.downloads.some(item => item.terminal) && responseReadDone) break;
      await new Promise(resolve => setTimeout(resolve, 25));
    } while (Date.now() < deadline);
    assert.equal(snapshot.downloads.length, 1, 'exactly one real Settings download required');
    assert.equal(snapshot.dialogs.length, 1, 'exactly one real application save dialog required');
    assert.deepEqual(snapshot.dialogs[0], { mode, expected: true,
      choice: mode === 'cancel' ? 'cancel' : 'controlled-synthetic-path' });
    assert.equal(snapshot.ownerURLUnchanged, true);
    assert.equal(snapshot.externalLaunchAttempts, 0);
    assert.deepEqual(snapshot.messages, []);
    assert.deepEqual(snapshot.navigations, []);
    assertRequests();
    assert.equal(requestEvidence.exportRequests, 1, 'one real button-owned export GET required');
    assert.equal(requestEvidence.exportResponses, 1, 'one real export response required');
    assert.equal(requestEvidence.successfulOwnerResponse, true, 'owner main-frame GET must return 200');
    assert.ok(responseReadDone && !responseReadFailed && responsePayload, 'actual Settings export response bytes unavailable');
    for (const value of forbiddenValues) assert.equal(responsePayload.includes(Buffer.from(value)), false, 'export response must not contain a profile secret');
    const responseStructure = kind === 'settings' ? validateSettingsBackup(responsePayload)
      : validateWorkspaceBackup(responsePayload, { forbiddenValues });
    expectedSavedPayload = kind === 'settings'
      ? Buffer.from(JSON.stringify(JSON.parse(responsePayload.toString('utf8')), null, 2)) : responsePayload;
    setStage('settings-export-file-verify');
    const files = await fs.readdir(root);
    let structure = responseStructure;
    let bytes = 0;
    let sha256;
    if (mode === 'cancel') {
      assertNativeDownload(snapshot.downloads[0], mode, 0);
      assert.deepEqual(files, [], 'cancelled Settings export must leave no file');
    } else {
      assert.deepEqual(files, [spec.filename], 'only the chosen Settings export file may exist');
      const stat = await fs.lstat(savePath);
      assert.ok(stat.isFile() && !stat.isSymbolicLink() && stat.size > 0 && stat.size <= 64 * 1024 * 1024,
        'saved export must be a bounded regular file');
      const payload = await fs.readFile(savePath);
      bytes = payload.length;
      assertNativeDownload(snapshot.downloads[0], mode, bytes);
      assert.ok(payload.equals(expectedSavedPayload), 'saved export must exactly equal this button-owned response');
      sha256 = createHash('sha256').update(payload).digest('hex');
    }
    finalNativeBaseline = snapshot;
    result = { kind, mode, native: snapshot.downloads[0], requestEvidence, structure, bytes, sha256,
      sourceResponseRepresentation: kind === 'settings' ? 'validated-json-stringify-pretty-2' : 'unchanged-response-bytes',
      cancelledDirectoryEmpty: mode === 'cancel', savedBytesMatchActualUiDownload: mode === 'save', syntheticProfileWithoutProviderCredentials: true,
      nativeDialogChoicesAutomated: true, nativeDialogPointerValidated: false,
      permissionHandlersReplaced: false, downloadPolicy: 'electron-native-default', rawExportRetained: false };
  } catch (error) { primaryError = error; }
  cleanupStarted = true;
  const cleanupErrors = [];
  if (installed) try {
    // Capture final observations in the SAME native call that removes listeners.
    // Events arriving while host-side file/ZIP checks ran must still fail closed.
    const restored = await run('restore', { cancelPending: true, captureFinalSnapshot: true });
    if (!primaryError) {
      assert.deepEqual(restored.snapshot, finalNativeBaseline, 'native Settings export evidence changed during file validation');
      assertRequests();
      assert.equal(requestEvidence.exportRequests, 1);
      assert.equal(requestEvidence.exportResponses, 1);
      assert.equal(requestEvidence.successfulOwnerResponse, true);
      result.native = restored.snapshot.downloads[0];
      result.requestEvidence = { ...requestEvidence };
    }
  } catch { cleanupErrors.push(new Error('Settings export final native observation or cleanup failed')); }
  for (const [event, handler] of [['request', onRequest], ['response', onResponse], ['requestfailed', onFailure]]) {
    try { page.removeListener(event, handler); } catch { cleanupErrors.push(new Error('Settings export request observer cleanup failed')); }
  }
  if (owner) try { await owner.dispose(); } catch { cleanupErrors.push(new Error('Settings export owner cleanup failed')); }
  try { await fs.rm(root, { recursive: true, force: true }); } catch { cleanupErrors.push(new Error('transient Settings export cleanup failed')); }
  expectedSavedPayload?.fill(0);
  responsePayload?.fill(0);
  forbiddenValues.fill('');
  if (primaryError && cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'Settings export and cleanup failed');
  if (primaryError) throw primaryError;
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Settings export cleanup failed');
  return { ...result, instrumentationRestored: true, transientExportRemoved: true };
}
