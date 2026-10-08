import fs from 'node:fs/promises';
import path from 'node:path';
import { _electron } from 'playwright-core';
import { CASES, childEnvironment, demand, safeCode, validateFixedFiles, validateLiveProduct } from './contract.mjs';
import { hash } from '../installed-ui/integrity.mjs';
import { waitForDesktopSurfaces, readDesktopSecurity } from '../installed-ui/desktop-surfaces.mjs';
import { selectOwnedProcesses, sameWindowsPath, validateListeners } from '../installed-ui/contract.mjs';
import { quietCommand, exists } from './prepare.mjs';
import { cleanOwnedFiles } from './cleanup.mjs';
import { saveEvidence } from './safe-evidence.mjs';
import { syntheticApi } from './synthetic-api.mjs';
import { prepareSyntheticProfile, runUiScenarios } from './ui-scenarios.mjs';
import { refreshSyntheticProfile } from './seed-refresh.mjs';

export async function executeValidation({ mode, brokerFactory, providerKey, screenshotFactory, mockContinuation } = {}) {
  demand(['live', 'mock'].includes(mode) && typeof brokerFactory === 'function' &&
    (mockContinuation === undefined || typeof mockContinuation === 'function') &&
    (mode !== 'live' || screenshotFactory === undefined) &&
    (mode !== 'live' || mockContinuation === undefined), 'INVALID_HARNESS');
  if (mode === 'live') validateLiveProduct();
const blocked = code => CASES.map(id => ({ id, status: 'BLOCKED', code, checks: {} }));
const report = { mode, status: 'BLOCKED', code: 'NOT_STARTED', scenarios: blocked('NOT_STARTED'), cleanupPassed: false };
let app, broker, timer, screenshotEvidence, ledger = {}, failure, cleanupFailure;
const root = process.env.RUNNER_TEMP && path.join(process.env.RUNNER_TEMP, mode === 'live' ? 'offerpilot-bounded-ai' : 'offerpilot-bounded-ai-mock');
const evidence = process.env.AI_EVIDENCE_DIR;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function ownedSnapshot(prepared) {
  return JSON.parse(await quietCommand('pwsh.exe', ['-NoProfile', '-NonInteractive', '-File',
    path.resolve('desktop/installed-ui/windows.ps1'), '-Mode', 'snapshot'], {
    ...childEnvironment(process.env), UI_EXE: prepared.exe, UI_BACKEND: prepared.backend,
  }, 30000));
}
try {
  demand(process.platform === 'win32' && process.env.RUNNER_ENVIRONMENT === 'github-hosted' && root && evidence,
    'HOSTED_WINDOWS_REQUIRED');
  demand(process.env.GITHUB_RUN_ATTEMPT === '1', 'RERUN_FORBIDDEN');
  demand(mode === 'mock' || (typeof providerKey === 'string' && providerKey.length >= 8), 'DEDICATED_ENVIRONMENT_KEY_MISSING');
  demand(!process.env.NODE_OPTIONS && !process.env.NODE_DEBUG && !process.env.NODE_DEBUG_NATIVE, 'DEBUG_ENVIRONMENT_FORBIDDEN');
  await validateFixedFiles();
  const statePath = path.join(root, 'prepared.json');
  const prepared = JSON.parse(await fs.readFile(statePath, 'utf8'));
  demand(prepared.schema === 1 && prepared.mode === mode && prepared.root === root && prepared.profile === path.join(process.env.APPDATA, 'OfferPilot Desktop') &&
    prepared.installDir === path.join(root, '安装 Application') && prepared.exe === path.join(prepared.installDir, 'OfferPilot Desktop.exe') &&
    prepared.backend === path.join(prepared.installDir, 'resources/backend/offerpilot-backend.exe') &&
    prepared.helperSha === (mode === 'live' ? process.env.AI_APPROVED_HELPER_SHA : process.env.GITHUB_SHA) && prepared.requestSha === process.env.GITHUB_SHA &&
    prepared.profileOwned === false, 'PREPARED_IDENTITY_MISMATCH');
  demand(await hash(prepared.exe) === prepared.exeSha256 && !await exists(prepared.profile), 'PREPARED_IDENTITY_MISMATCH');
  demand((await ownedSnapshot(prepared)).processes.length === 0, 'PREEXISTING_PRODUCT_PROCESS');
  await fs.mkdir(prepared.profile); // exclusive, never overwrite or adopt a real profile
  prepared.profileOwned = true;
  await fs.writeFile(statePath, JSON.stringify(prepared));
  broker = await brokerFactory({ providerKey, ledgerPath: path.join(root, 'session-ledger.json'),
    sessionId: 'offerpilot-fixed-exe-real-ai-20261008', runId: process.env.GITHUB_RUN_ID,
    runAttempt: Number(process.env.GITHUB_RUN_ATTEMPT), helperCommit: prepared.helperSha, requestCommit: prepared.requestSha });
  let scenarioBroker = broker;
  let capture;
  if (mode === 'mock' && typeof screenshotFactory === 'function') {
    screenshotEvidence = screenshotFactory({ mode: 'mock', directory: evidence });
    scenarioBroker = { ...broker, prepareCase(caseId) {
      const preparedCase = broker.prepareCase(caseId);
      demand(screenshotEvidence.registerToken(preparedCase.clientToken) === true, 'INVALID_HARNESS');
      return preparedCase;
    } };
    capture = (screenId, page, failedCase) => screenshotEvidence.capture(screenId, page,
      { stage: failedCase || screenId.replace(/^haru-/, '') });
  }
  const started = Date.now();
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => {
    void broker.close().catch(() => { cleanupFailure = 'BROKER_CLEANUP_FAILED'; });
    reject(Object.assign(new Error('SESSION_DEADLINE'), { safeCode: 'SESSION_DEADLINE' }));
  }, 600000); });
  await Promise.race([(async () => {
    app = await _electron.launch({ executablePath: prepared.exe, cwd: prepared.installDir,
      env: childEnvironment(process.env), chromiumSandbox: true, bypassCSP: false, timeout: 90000 });
    const identity = await app.evaluate(({ app }) => ({ pid: process.pid, exe: process.execPath, profile: app.getPath('userData') }));
    demand(sameWindowsPath(identity.exe, prepared.exe) && sameWindowsPath(identity.profile, prepared.profile), 'INSTALLED_PROCESS_IDENTITY_MISMATCH');
    const portPath = path.join(prepared.profile, 'desktop-port.json');
    const until = Date.now() + 90000;
    while (!await exists(portPath) && Date.now() < until) await pause(200);
    const saved = JSON.parse(await fs.readFile(portPath, 'utf8'));
    demand(Object.keys(saved).length === 1 && Number.isSafeInteger(saved.port) && saved.port > 0 && saved.port <= 65535,
      'PRODUCT_ORIGIN_INVALID');
    const surfaces = await waitForDesktopSurfaces(app, `http://127.0.0.1:${saved.port}`);
    const page = surfaces.owner.page, haru = surfaces.haru.page;
    const windows = await ownedSnapshot(prepared);
    const owned = selectOwnedProcesses(windows.processes, identity.pid, prepared.exe, prepared.backend);
    validateListeners(windows.listeners, owned.main.pid, owned.backend.pid, saved.port);
    const ids = {};
    for (const [name, surface] of [['owner', page], ['haru', haru]]) {
      surface.setDefaultTimeout(15000);
      ids[name] = await (await app.browserWindow(surface)).evaluate(win => win.id);
    }
    const security = await app.evaluate(readDesktopSecurity, ids);
    for (const value of [security.owner, security.haru]) demand(value.packaged && !value.nodeIntegration &&
      value.contextIsolation && value.sandbox && value.webSecurity && !value.devTools && !value.devToolsOpened &&
      value.unsafeSwitches.length === 0, 'PRODUCT_SECURITY_ASSERTION_FAILED');
    let externalRendererRequest = false;
    app.context().on('request', request => {
      try { const url = new URL(request.url()); if (['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) &&
        url.hostname !== '127.0.0.1') externalRendererRequest = true; } catch { externalRendererRequest = true; }
    });
    await page.getByRole('navigation', { name: '主导航', exact: true }).waitFor();
    await haru.getByRole('main', { name: 'Haru 桌面小窗', exact: true }).waitFor();
    const api = syntheticApi(page);
    const fixture = await prepareSyntheticProfile(api);
    await refreshSyntheticProfile(page, broker, fixture, started + 600000);
    const result = await runUiScenarios({ page, haru, api, broker: scenarioBroker, fixture,
      capture, mode, mockContinuation, deadlineMs: started + 600000 });
    report.scenarios = CASES.map(id => result.results.find(row => row.id === id));
    demand(!externalRendererRequest, 'UNEXPECTED_RENDERER_NETWORK');
    report.status = result.allPassed ? 'PASS' : 'FAIL';
    report.code = result.allPassed ? (report.scenarios.some(row => row.code === 'LIVE_COMPLETION_ONLY')
      ? 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY' : 'ALL_UI_CASES_PASSED') : 'UI_CASES_INCOMPLETE';
  })(), timeout]);
} catch (error) {
  failure = safeCode(error); report.status = 'BLOCKED'; report.code = failure;
} finally {
  clearTimeout(timer);
  if (broker) { try { await broker.close(); } catch { cleanupFailure = 'BROKER_CLEANUP_FAILED'; }
    try { ledger = broker.snapshot(); } catch { cleanupFailure = 'LEDGER_UNAVAILABLE'; } }
  if (ledger.journalFailed) cleanupFailure = 'LEDGER_PERSISTENCE_FAILED';
  report.screenshotEvidence = screenshotEvidence?.snapshot();
  // Save the durable-cost projection before deleting its owned scratch journal.
  if (evidence) { try { await saveEvidence(evidence, { ...report, status: 'BLOCKED', code: 'CLEANUP_PENDING', cleanupCode: 'CLEANUP_PENDING', cleanupPassed: false }, ledger, [providerKey]); }
    catch { cleanupFailure = 'EVIDENCE_WRITE_BLOCKED'; } }
  if (app) { try { await Promise.race([app.close(), pause(15000).then(() => { throw new Error(); })]); }
    catch { /* Independently owned process cleanup below is authoritative. */ } }
  try { await cleanOwnedFiles(process.env, mode); report.cleanupPassed = true; } catch { cleanupFailure = 'PROFILE_CLEANUP_FAILED'; }
  report.cleanupCode = cleanupFailure || 'CLEANUP_PASSED';
  if (failure || cleanupFailure) {
    report.status = 'BLOCKED';
    report.code = failure || (['NOT_STARTED', 'ALL_UI_CASES_PASSED', 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY'].includes(report.code) ? cleanupFailure : report.code);
  }
  if (evidence) {
    try { await saveEvidence(evidence, report, ledger, [providerKey]); }
    catch { report.status = 'BLOCKED'; report.code = failure || report.code; report.cleanupCode = 'EVIDENCE_WRITE_BLOCKED'; }
  }
  console.log(mode === 'mock' ? `MOCK installed UI validation: ${report.status}; no real provider evidence.` :
    report.status === 'PASS' ? (report.code === 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY'
      ? 'Bounded real-provider installed UI cases completed; sustained Pilot streaming NOT proven; experimental evidence only.'
      : 'Bounded real-provider installed UI cases passed; experimental evidence only.') : `Real AI validation blocked or incomplete: ${report.code}`);
  return { ...report, exitCode: report.status === 'PASS' && report.cleanupPassed ? 0 : 1 };
}

}
