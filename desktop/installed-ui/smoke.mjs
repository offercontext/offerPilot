import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import net from 'node:net';
import { _electron } from 'playwright-core';
import { getCurrentFuseWire, FuseState, FuseV1Options } from '@electron/fuses';
import { extractFile } from '@electron/asar';
import { hash, treeFiles, verifyPayload, normalizeSourceText } from './integrity.mjs';
import { safeFailure, commandFailure, recordSecurityBeforeValidation } from './diagnostics.mjs';
import { observeDevToolsDisabled } from './devtools-probe.mjs';
import { verifyApplicationDetail } from './detail-ui.mjs';
import { createCoverage, observeRuntime } from './coverage-recorder.mjs';
import { rootSweep, extendedFlows } from './screen-coverage.mjs';
import { reloadOnceWithObserver } from './startup-diagnostics.mjs';
import { PIN, SYNTHETIC, validateRequest, publicApplication, sameWindowsPath,
  selectOwnedProcesses, validateListeners, validateSecurity } from './contract.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const evidence = path.resolve(process.env.UI_EVIDENCE_DIR || path.join(here, 'evidence'));
const source = path.resolve(process.env.UI_SOURCE_DIR || '.installed-ui-source');
const artifact = path.resolve(process.env.UI_ARTIFACT_DIR || '.installed-ui-artifact');
const report = { status: 'running', sourceCommit: PIN.commit, buildCommit: PIN.buildCommit, buildWorkflow: PIN.buildWorkflow,
  buildRunId: PIN.runId, fullRegressionRunId: PIN.fullRegressionRunId, artifactId: PIN.artifactId,
  scope: 'experimental-installed-UI-with-temporary-loopback-debugging', releaseReady: false,
  ordinaryUserUacSmartScreenValidated: false, normalUndebuggedLaunchValidated: false,
  fullRegression: 'independent-not-certified', stages: [], launches: [] };
let stage = 'environment';
let current;
let owned = [];
let exe;
let backend;
let userData;
let installDir;
let environment;
let fatalNetwork = false;
let coverage;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function exists(filename) { try { await fs.access(filename); return true; } catch { return false; } }
async function writeReport() {
  await fs.writeFile(path.join(evidence, 'result.json'), JSON.stringify(report, null, 2) + '\n');
}
async function checkpoint(name, extra = {}) {
  report.stages.push({ name, result: 'passed', ...extra });
  await writeReport();
  console.log(`Passed: ${name}`);
}
async function command(command, args, env = process.env, timeout = 240000) {
  const tool = command === 'pwsh.exe' ? 'powershell' : '7zip';
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    const chunks = [];
    let bytes = 0;
    const timer = setTimeout(() => { child.kill(); reject(commandFailure('COMMAND_TIMEOUT', tool)); }, timeout);
    child.stdout.on('data', (chunk) => {
      bytes += chunk.length;
      if (bytes < 8 * 1024 * 1024) chunks.push(chunk);
    });
    // Drain, but never publish stderr: it can contain debug endpoints or tokens.
    child.stderr.resume();
    child.once('error', (error) => { clearTimeout(timer); reject(commandFailure(error.code, tool)); });
    child.once('close', (code) => {
      clearTimeout(timer);
      if (code !== 0 || bytes >= 8 * 1024 * 1024) reject(commandFailure(bytes >= 8 * 1024 * 1024 ? 'COMMAND_OUTPUT_LIMIT' : 'COMMAND_EXIT', tool, code));
      else resolve(Buffer.concat(chunks).toString('utf8').replace(/^\uFEFF/, '').trim());
    });
  });
}
async function windows(mode, extra = {}) {
  return JSON.parse(await command('pwsh.exe', ['-NoProfile', '-NonInteractive', '-File', path.join(here, 'windows.ps1'), '-Mode', mode],
    { ...environment, ...extra }, mode === 'install' ? 210000 : 30000));
}
async function waitUntil(callback, timeout = 30000) {
  const deadline = Date.now() + timeout;
  do { if (await callback()) return; await delay(400); } while (Date.now() < deadline);
  throw new Error('condition timed out');
}
async function endpointOpen({ address = '127.0.0.1', port }) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: address, port });
    const done = (open) => { socket.destroy(); resolve(open); };
    socket.once('connect', () => done(true));
    socket.once('error', () => done(false));
    socket.setTimeout(1000, () => done(true)); // Inconclusive must not count as closed.
  });
}
async function savedPort() {
  const value = JSON.parse(await fs.readFile(path.join(userData, 'desktop-port.json'), 'utf8'));
  assert.deepEqual(Object.keys(value), ['port']);
  assert.ok(Number.isInteger(value.port) && value.port > 0 && value.port <= 65535);
  return value.port;
}
async function screenshot(page, name) {
  await page.screenshot({ path: path.join(evidence, `${name}.png`), timeout: 15000 });
}
async function launch(number) {
  assert.equal((await windows('snapshot')).processes.length, 0, 'no installed processes may predate launch');
  stage = `launch-${number}-electron-start`;
  const app = await _electron.launch({ executablePath: exe, cwd: installDir, env: environment,
    chromiumSandbox: true, bypassCSP: false, acceptDownloads: false, timeout: 90000 });
  current = { app };
  app.context().on('request', (request) => {
    const url = new URL(request.url());
    if (['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) && url.hostname !== '127.0.0.1') fatalNetwork = true;
  });
  // Capture ownership before UI waits, so an early renderer timeout still has safe cleanup.
  stage = `launch-${number}-main-identity`;
  const identity = await app.evaluate(({ app }) => ({ pid: process.pid, exe: process.execPath, userData: app.getPath('userData') }));
  assert.ok(sameWindowsPath(identity.exe, exe));
  assert.ok(sameWindowsPath(identity.userData, userData), 'must use the real APPDATA profile');
  const early = await windows('snapshot');
  const main = early.processes.find((item) => item.pid === identity.pid && sameWindowsPath(item.path, exe));
  assert.ok(main?.created, 'main process must be confirmed by CIM before UI waits');
  owned = [main, ...early.processes.filter((item) => item.parentPid === main.pid && sameWindowsPath(item.path, backend))];
  stage = `launch-${number}-first-window`;
  const page = await app.firstWindow({ timeout: 90000 });
  current.page = page;
  const runtime = observeRuntime(page);
  page.setDefaultTimeout(20000);
  stage = `launch-${number}-navigation-render`;
  await page.getByRole('navigation', { name: '主导航', exact: true }).waitFor();
  stage = `launch-${number}-saved-origin`;
  const port = await savedPort();
  const origin = new URL(page.url());
  assert.equal(origin.protocol, 'http:');
  assert.equal(origin.hostname, '127.0.0.1');
  assert.equal(Number(origin.port), port);
  const snapshot = await windows('snapshot');
  stage = `launch-${number}-backend-ownership`;
  const processes = selectOwnedProcesses(snapshot.processes, identity.pid, exe, backend);
  owned = [processes.backend, processes.main];
  stage = `launch-${number}-loopback-listeners`;
  const listeners = validateListeners(snapshot.listeners, processes.main.pid, processes.backend.pid, port);
  stage = `launch-${number}-runtime-security`;
  const security = await app.evaluate(({ app, BrowserWindow }) => {
    const windows = BrowserWindow.getAllWindows();
    if (windows.length !== 1) throw new Error('one application window required');
    const contents = windows[0].webContents;
    const prefs = contents.getLastWebPreferences();
    return { packaged: app.isPackaged, nodeIntegration: prefs.nodeIntegration,
      contextIsolation: prefs.contextIsolation, sandbox: prefs.sandbox, webSecurity: prefs.webSecurity,
      devTools: prefs.devTools, devToolsOpened: contents.isDevToolsOpened(),
      unsafeSwitches: ['no-sandbox', 'disable-web-security', 'disable-site-isolation-trials',
        'allow-running-insecure-content', 'ignore-certificate-errors'].filter((name) => app.commandLine.hasSwitch(name)) };
  });
  const info = { number, mainPid: processes.main.pid, backendPid: processes.backend.pid,
    mainCreated: processes.main.created, backendCreated: processes.backend.created,
    port, realAppData: true, observedExternalRendererRequests: fatalNetwork, ...listeners };
  report.launches.push(info);
  current = { app, page, info, runtime, debugEndpoints: snapshot.listeners.filter((item) => item.pid === identity.pid).map(({ address, port }) => ({ address, port })) };
  // Persist bounded observations first: a failed assertion must not erase its evidence.
  await recordSecurityBeforeValidation(info, security, writeReport);
  stage = `launch-${number}-devtools-disabled-probe`;
  security.devToolsProbe = await app.evaluate(observeDevToolsDisabled);
  await recordSecurityBeforeValidation(info, security, writeReport);
  stage = `launch-${number}-runtime-security`;
  validateSecurity(security);
  info.securityValidation = 'passed';
  stage = `launch-${number}-external-network`;
  info.observedExternalRendererRequests = fatalNetwork;
  await writeReport();
  assert.equal(fatalNetwork, false, 'external renderer request observed');
  await checkpoint(`launch-${number}-identity-and-security`);
  return current;
}
async function closeNormally() {
  const { app, page, info, debugEndpoints } = current;
  stage = `launch-${info.number}-request-normal-close`;
  const window = await app.browserWindow(page);
  // BrowserWindow.close() follows the same close/before-quit path as the window's X button.
  // Schedule after the protocol reply so disconnect is not mistaken for success/failure.
  await window.evaluate((window) => { setTimeout(() => window.close(), 0); });
  stage = `launch-${info.number}-process-exit`;
  await waitUntil(async () => (await windows('snapshot')).processes.length === 0, 45000);
  stage = `launch-${info.number}-port-release`;
  await waitUntil(async () => !(await endpointOpen({ port: info.port })) &&
    (await Promise.all(debugEndpoints.map(endpointOpen))).every((value) => !value), 15000);
  assert.equal(await savedPort(), info.port);
  stage = `launch-${info.number}-external-network-after-close`;
  info.observedExternalRendererRequests = fatalNetwork;
  await writeReport();
  assert.equal(fatalNetwork, false);
  info.normalClose = true;
  info.mainBackendAndRendererExited = true;
  info.backendAndDebugPortsClosed = true;
  current = undefined;
  owned = [];
  await checkpoint(`launch-${info.number}-normal-close-and-port-release`);
}
async function openList(page, record, shot) {
  stage = `${shot}-list-navigation`;
  const back = page.getByRole('button', { name: '返回上一层', exact: true });
  if (await back.isVisible()) await back.click();
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button', { name: '投递', exact: true }).click();
  await page.getByRole('tab', { name: '列表', exact: true }).click();
  const list = page.locator('section[aria-label="投递列表"]');
  await list.waitFor();
  stage = `${shot}-list-search`;
  await list.getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill(SYNTHETIC.company_name);
  const rows = list.locator('tbody tr[data-row-key]').filter({ hasText: SYNTHETIC.company_name });
  await rows.first().waitFor();
  stage = `${shot}-list-record-identity`;
  assert.equal(await rows.count(), 1, 'exactly one matching application row required');
  assert.equal(await rows.getAttribute('data-row-key'), String(record.id), 'persisted UI row ID must match POST response');
  assert.ok((await rows.innerText()).includes(SYNTHETIC.position_name));
  await screenshot(page, shot);
  return rows;
}

await fs.mkdir(evidence, { recursive: true });
try {
  assert.equal(process.platform, 'win32', 'actual Windows required');
  assert.ok(process.env.RUNNER_TEMP && process.env.APPDATA);
  stage = 'request-pin';
  validateRequest(JSON.parse(await fs.readFile(path.join(here, 'request.json'), 'utf8')));
  stage = 'fresh-real-profile';
  // Do not change APPDATA or pass --user-data-dir: main.cjs deliberately overrides userData.
  userData = path.join(process.env.APPDATA, 'OfferPilot Desktop');
  assert.equal(await exists(userData), false, 'existing real profile is a blocker; never delete it');
  const scratch = await fs.mkdtemp(path.join(process.env.RUNNER_TEMP, 'OfferPilot 中文 UI '));
  installDir = path.join(scratch, '安装 Application');
  exe = path.join(installDir, 'OfferPilot Desktop.exe');
  backend = path.join(installDir, 'resources', 'backend', 'offerpilot-backend.exe');
  // Pass only OS runtime variables and local model-map setting. No CI/provider credentials.
  environment = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    /^(SystemRoot|WINDIR|SystemDrive|COMSPEC|PATH|PATHEXT|TEMP|TMP|APPDATA|LOCALAPPDATA|USERPROFILE|HOMEDRIVE|HOMEPATH|ProgramFiles|ProgramFiles\(x86\)|ProgramData|ALLUSERSPROFILE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE|OS|SESSIONNAME)$/i.test(key)));
  Object.assign(environment, { LITELLM_LOCAL_MODEL_COST_MAP: 'True', PYTHONUTF8: '1', PYTHONUNBUFFERED: '1',
    UI_EXE: exe, UI_BACKEND: backend, UI_USER_DATA: userData, UI_INSTALL_DIR: installDir });
  await checkpoint(stage, { freshRealProfile: true, unicodeSpaceInstallationPath: true });

  stage = 'installer-sha256';
  const installer = path.join(artifact, PIN.installer);
  assert.equal(await hash(installer), PIN.installerSha256);
  const unpack = path.join(scratch, 'installer-payload');
  const payload = path.join(scratch, 'app-payload');
  const sevenZip = path.join(process.env.ProgramFiles, '7-Zip', '7z.exe');
  stage = 'installer-archive-extract';
  await command(sevenZip, ['x', installer, `-o${unpack}`, '-y'], environment);
  stage = 'installer-payload-discovery';
  const archives = (await treeFiles(unpack)).filter((name) => path.basename(name) === 'app-64.7z');
  assert.equal(archives.length, 1, 'one x64 NSIS payload required');
  stage = 'installer-payload-extract';
  await command(sevenZip, ['x', path.join(unpack, archives[0]), `-o${payload}`, '-y'], environment);
  await checkpoint('installer-hash-and-payload', { installerSha256: PIN.installerSha256 });

  stage = 'silent-current-user-install';
  const installed = await windows('install', { UI_INSTALLER: installer });
  assert.equal(installed.exitCode, 0);
  assert.equal(await exists(userData), false, 'installer must not silently launch into a profile');
  await checkpoint(stage, { exitCode: 0, mode: 'NSIS /S /currentuser', destination: 'fresh RUNNER_TEMP path with Chinese and spaces' });

  stage = 'installed-required-resources';
  for (const name of ['OfferPilot Desktop.exe', 'resources/app.asar', 'resources/backend/offerpilot-backend.exe',
    'resources/backend/_internal', 'resources/web/index.html', 'resources/web/assets', 'resources/LICENSE']) {
    assert.equal(await exists(path.join(installDir, name)), true, 'required installed resource missing');
  }
  stage = 'installed-payload-byte-hashes';
  const checkedPayloadFiles = await verifyPayload(installDir, payload);
  stage = 'packaged-source-text';
  for (const name of ['main.cjs', 'lifecycle.cjs']) {
    assert.equal(normalizeSourceText(extractFile(path.join(installDir, 'resources', 'app.asar'), name)),
      normalizeSourceText(await fs.readFile(path.join(source, 'desktop', name))), 'packaged desktop source differs from pin');
  }
  assert.equal(await hash(path.join(installDir, 'resources', 'LICENSE')), await hash(path.join(source, 'LICENSE')));
  stage = 'packaged-version-identity';
  const packageJson = JSON.parse(extractFile(path.join(installDir, 'resources', 'app.asar'), 'package.json').toString());
  assert.equal(packageJson.name, 'offerpilot-desktop');
  assert.equal(packageJson.version, '0.1.0-desktop.1');
  assert.equal(packageJson.main, 'main.cjs');
  const exeHashBefore = await hash(exe);
  stage = 'read-existing-inspect-fuse';
  const fuses = await getCurrentFuseWire(exe); // Read-only; never flip a fuse.
  assert.equal(fuses[FuseV1Options.EnableNodeCliInspectArguments], FuseState.ENABLE, 'existing inspect fuse must permit testing without modification');
  assert.equal(await hash(exe), exeHashBefore);
  await checkpoint('installed-resource-integrity', { checkedPayloadFiles, installedExeSha256: exeHashBefore,
    sourceMainAndLifecycleMatch: 'CRLF-normalized-text', nodeCliInspectFuseAlreadyEnabled: true, fuseReadOnly: true });

  stage = 'first-launch-ui';
  const first = await launch(1);
  stage = 'first-ui-screenshot';
  await screenshot(first.page, '01-first-launch');
  coverage = await createCoverage({ app: first.app, page: first.page, evidence, pin: PIN,
    installedExeSha256: exeHashBefore, runtime: first.runtime, setStage: (value) => { stage = value; } });
  await reloadOnceWithObserver(coverage, first.page, first.runtime);
  await rootSweep(coverage, first.page, 'empty-before-fixtures');
  stage = 'onboarding-create-application';
  await first.page.locator('button[data-onboarding-action="create_first_application"]').click();
  const form = first.page.getByRole('dialog', { name: '添加投递', exact: true });
  await form.waitFor();
  stage = 'fill-synthetic-form';
  await form.getByLabel('公司', { exact: true }).fill(SYNTHETIC.company_name);
  await form.getByLabel('岗位', { exact: true }).fill(SYNTHETIC.position_name);
  await form.getByLabel('备注', { exact: true }).fill(SYNTHETIC.notes);
  await form.getByText('准备投递', { exact: true }).waitFor();
  await form.getByText('稍后补充 JD', { exact: true }).waitFor();
  stage = 'duplicate-check-required';
  await form.getByRole('button', { name: '核对并检查重复', exact: true }).click();
  await form.getByText('未发现符合规则的重复记录', { exact: true }).waitFor();
  stage = 'confirm-save-ui-response';
  const [response] = await Promise.all([first.page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.origin === `http://127.0.0.1:${first.info.port}` && url.pathname === '/api/applications' && response.request().method() === 'POST';
  }), form.getByRole('button', { name: '确认保存', exact: true }).click()]);
  assert.ok([200, 201].includes(response.status()));
  const record = publicApplication(await response.json());
  report.application = record; // Only these five whitelisted fields; no raw response/header/body dumps.
  stage = 'saved-detail-content';
  await verifyApplicationDetail(first.page, SYNTHETIC, (step) => { stage = `saved-detail-${step}`; });
  await screenshot(first.page, '02-saved-detail');
  await openList(first.page, record, '03-saved-list');
  await extendedFlows(coverage, first.page, record);
  report.screenCoverage = coverage.report.summary;
  report.screenCoverageFile = 'coverage.json';
  await writeReport();
  stage = 'coverage-unresolved-write-barrier';
  assert.equal(first.runtime.hasPendingWrite(), false, 'unresolved UI write blocks further UI mutation/restart steps');
  stage = 'theme-ui-toggle';
  const oldTheme = await first.page.locator('html').getAttribute('data-theme');
  assert.ok(['light', 'dark'].includes(oldTheme));
  await first.page.getByRole('button', { name: '切换明暗模式', exact: true }).click();
  const theme = oldTheme === 'light' ? 'dark' : 'light';
  await waitUntil(async () => await first.page.locator('html').getAttribute('data-theme') === theme);
  await checkpoint('first-launch-ui', { createdByUi: true, duplicateCheck: 'no-matches', themeSelectedByUi: theme });
  stage = 'first-normal-close';
  await closeNormally();

  stage = 'restart-persistence';
  const second = await launch(2);
  stage = 'restart-process-and-origin-identity';
  assert.equal(second.info.port, first.info.port, 'saved origin must persist');
  assert.notEqual(second.info.mainPid, first.info.mainPid, 'relaunch must be a new main process');
  assert.notEqual(second.info.backendPid, first.info.backendPid, 'relaunch must be a new backend process');
  assert.notEqual(second.info.mainCreated, first.info.mainCreated);
  assert.notEqual(second.info.backendCreated, first.info.backendCreated);
  stage = 'restart-theme-persistence';
  assert.equal(await second.page.locator('html').getAttribute('data-theme'), theme);
  const row = await openList(second.page, record, '04-restarted-list');
  stage = 'restart-detail-content';
  await row.click();
  await verifyApplicationDetail(second.page, SYNTHETIC, (step) => { stage = `restart-detail-${step}`; });
  await screenshot(second.page, '05-restarted-detail');
  stage = 'data-location';
  assert.equal(await exists(path.join(userData, 'data', 'data.db')), true);
  assert.equal((await treeFiles(installDir)).some((name) => /\.(db|sqlite|sqlite3)(-|$)/i.test(name)), false);
  await checkpoint('restart-persistence', { sameUiRecordId: record.id, sameChineseContent: true, sameTheme: theme, samePort: first.info.port });
  stage = 'second-normal-close';
  await closeNormally();
  stage = 'final-integrity';
  assert.equal(await hash(exe), exeHashBefore, 'test must not modify installed executable/fuses');
  stage = 'final-external-network';
  report.observedExternalRendererRequests = fatalNetwork;
  await writeReport();
  assert.equal(fatalNetwork, false);
  await checkpoint(stage, { executableUnchanged: true, observedExternalRendererRequests: false });
  stage = 'expanded-screen-coverage';
  assert.equal(coverage.report.summary.counts.FAIL, 0, 'installed screen assertions failed; lifecycle results remain independent');
  report.status = coverage.report.summary.status === 'incomplete' ? 'passed-with-coverage-limitations' : 'passed';
} catch (error) {
  // Keep failures failed, including launch/close/cleanup/security/persistence failures.
  // Playwright errors may embed process logs and websocket endpoints: do not serialize them.
  report.status = 'failed';
  report.failedStage = stage;
  report.failure = safeFailure(error);
  console.error(`Installed UI validation failed at ${stage}; see whitelisted evidence.`);
  if (current?.page && !current.page.isClosed()) {
    try { await screenshot(current.page, 'failure'); } catch { /* Evidence unavailable is not success. */ }
  }
  if (current?.app) {
    try { await Promise.race([current.app.close(), delay(10000).then(() => { throw new Error('close timeout'); })]); }
    catch { report.cleanupNormalCloseFailed = true; }
  }
  if (environment) {
    try {
      const snapshot = await windows('snapshot');
      // A fresh unique install path and OS-created process identities prevent unrelated PID kills.
      // Prefer the already captured main/backend identities; permit only their exact-path children.
      const allowed = new Map(owned.map((item) => [item.pid, item]));
      let changed = true;
      while (changed) {
        changed = false;
        for (const item of snapshot.processes) if (!allowed.has(item.pid) && allowed.has(item.parentPid)) {
          allowed.set(item.pid, item); changed = true;
        }
      }
      report.cleanup = await windows('cleanup', { UI_OWNED_IDENTITIES: JSON.stringify([...allowed.values()].reverse()) });
      report.cleanupRemainingInstalledProcesses = (await windows('snapshot')).processes.length;
    } catch { report.cleanupFailed = true; }
  }
  process.exitCode = 1;
} finally {
  if (coverage) {
    await coverage.finish();
    report.screenCoverage = coverage.report.summary;
    report.screenCoverageFile = 'coverage.json';
  }
  await writeReport();
  console.log(`Installed UI result: ${report.status}. Experimental evidence only; not a release gate.`);
}
