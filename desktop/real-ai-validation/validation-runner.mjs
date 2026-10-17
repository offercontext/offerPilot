import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { performance } from 'node:perf_hooks';
import { RECOVERY } from './carry-forward-policy.cjs';
import { loadPreparedCarryForward } from './carry-forward-evidence.mjs';
import { recoveryBaselineSnapshot } from './broker.cjs';
import { _electron } from 'playwright-core';
import { CASES, childEnvironment, demand, safeCode, validateFixedFiles, validateLiveProduct } from './contract.mjs';
import { hash } from '../installed-ui/integrity.mjs';
import { waitForDesktopSurfaces, readDesktopSecurity, validatePartitionIsolation } from '../installed-ui/desktop-surfaces.mjs';
import { selectOwnedProcesses, sameWindowsPath, validateListeners, validateSecurity } from '../installed-ui/contract.mjs';
import { observeDevToolsDisabled } from '../installed-ui/devtools-probe.mjs';
import { installTrayObserver, invokeTrayAction } from '../installed-ui/haru-probes.mjs';
import { quietCommand, exists } from './prepare.mjs';
import { cleanOwnedFiles } from './cleanup.mjs';
import { saveEvidence } from './safe-evidence.mjs';
import { syntheticApi } from './synthetic-api.mjs';
import { prepareSyntheticProfile, runUiScenarios } from './ui-scenarios.mjs';
import { refreshSyntheticProfile } from './seed-refresh.mjs';
import { assertHaruRestartFacts } from './mock-haru-session.mjs';
import { createRunLifetime } from './run-lifetime.mjs';
import { trackAppResource, waitForResourceSettlement } from './run-finalization.mjs';

export async function executeValidation({ mode, brokerFactory, providerKey, screenshotFactory, mockContinuation, mockHaruSessionFactory, mockPilotProofFactory, scope = mode === 'live' ? RECOVERY.scope : 'full-suite' } = {}) {
  demand(['live', 'mock'].includes(mode) && typeof brokerFactory === 'function' &&
    [RECOVERY.scope, 'full-suite'].includes(scope) && (mode !== 'live' || scope === RECOVERY.scope) &&
    (scope !== RECOVERY.scope || (screenshotFactory === undefined && mockContinuation === undefined && mockHaruSessionFactory === undefined)) &&
    (mode !== 'mock' || scope !== RECOVERY.scope || typeof mockPilotProofFactory === 'function') &&
    (mockContinuation === undefined || typeof mockContinuation === 'function') &&
    (mockHaruSessionFactory === undefined || typeof mockHaruSessionFactory === 'function') &&
    (mockPilotProofFactory === undefined || typeof mockPilotProofFactory === 'function') &&
    (mode !== 'live' || screenshotFactory === undefined) &&
    (mode !== 'live' || (mockContinuation === undefined && mockHaruSessionFactory === undefined && mockPilotProofFactory === undefined)), 'INVALID_HARNESS');
  if (mode === 'live') validateLiveProduct();
const recovery = scope === RECOVERY.scope;
const selectedCases = recovery ? RECOVERY.allowedCases : CASES;
const started = Date.now(), executionStartedAt = performance.now();
const deadlineMs = started + (recovery ? RECOVERY.executionMs : 600000);
const blocked = code => selectedCases.map(id => ({ id, status: 'BLOCKED', code, checks: {} }));
const report = { mode, scope, status: 'BLOCKED', code: 'NOT_STARTED', scenarios: blocked('NOT_STARTED'), cleanupPassed: false };
let app, broker, lifetime, screenshotEvidence, ledger = {}, failure, cleanupFailure, finalEvidenceSaved = false, appLaunchPending = false;
const appResources = [];
const root = process.env.RUNNER_TEMP && path.join(process.env.RUNNER_TEMP, mode === 'live' ? 'offerpilot-bounded-ai' : 'offerpilot-bounded-ai-mock');
const evidence = process.env.AI_EVIDENCE_DIR;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function ownedSnapshot(prepared) {
  return JSON.parse(await quietCommand('pwsh.exe', ['-NoProfile', '-NonInteractive', '-File',
    path.resolve('desktop/installed-ui/windows.ps1'), '-Mode', 'snapshot'], {
    ...childEnvironment(process.env), UI_EXE: prepared.exe, UI_BACKEND: prepared.backend,
  }, 30000));
}
const endpointOpen = ({ address = '127.0.0.1', port }) => new Promise(resolve => {
  const socket = net.connect({ host: address, port });
  const done = open => { socket.destroy(); resolve(open); };
  socket.once('connect', () => done(true)); socket.once('error', () => done(false));
  socket.setTimeout(1000, () => done(true)); // Inconclusive is never closed.
});
async function until(predicate, limit = 15000) {
  const stop = Date.now() + limit;
  do { lifetime?.assertActive(); const ready = await predicate(); lifetime?.assertActive(); if (ready) return; await pause(200); } while (Date.now() < stop);
  demand(false, 'UI_TIMEOUT');
}
let externalRendererRequest = false;
async function launchOwned(prepared) {
  lifetime.assertActive();
  demand((await ownedSnapshot(prepared)).processes.length === 0
    && await hash(prepared.exe) === prepared.exeSha256, 'PREEXISTING_PRODUCT_PROCESS');
  lifetime.assertActive();
  appLaunchPending = true;
  app = await _electron.launch({ executablePath: prepared.exe, cwd: prepared.installDir,
    env: childEnvironment(process.env), chromiumSandbox: true, bypassCSP: false, timeout: 90000 });
  appResources.push(trackAppResource(app));
  appLaunchPending = false;
  lifetime.assertActive();
  app.context().on('request', request => {
    try { const url = new URL(request.url()); if (['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol)
      && url.hostname !== '127.0.0.1') externalRendererRequest = true; } catch { externalRendererRequest = true; }
  });
  const identity = await app.evaluate(({ app }) => ({ pid: process.pid, exe: process.execPath, profile: app.getPath('userData') }));
  demand(sameWindowsPath(identity.exe, prepared.exe) && sameWindowsPath(identity.profile, prepared.profile), 'INSTALLED_PROCESS_IDENTITY_MISMATCH');
  const portPath = path.join(prepared.profile, 'desktop-port.json');
  await until(() => exists(portPath), 90000);
  const saved = JSON.parse(await fs.readFile(portPath, 'utf8'));
  demand(Object.keys(saved).length === 1 && Number.isSafeInteger(saved.port) && saved.port > 0 && saved.port <= 65535,
    'PRODUCT_ORIGIN_INVALID');
  const origin = `http://127.0.0.1:${saved.port}`;
  const surfaces = await waitForDesktopSurfaces(app, origin);
  const page = surfaces.owner.page, haru = surfaces.haru.page;
  const windows = await ownedSnapshot(prepared);
  const owned = selectOwnedProcesses(windows.processes, identity.pid, prepared.exe, prepared.backend);
  validateListeners(windows.listeners, owned.main.pid, owned.backend.pid, saved.port);
  const ids = {};
  for (const [name, surface] of [['owner', page], ['haru', haru]]) {
    surface.setDefaultTimeout(15000); ids[name] = await (await app.browserWindow(surface)).evaluate(win => win.id);
  }
  const security = await app.evaluate(readDesktopSecurity, ids);
  try {
    for (const role of ['owner', 'haru']) {
      security[role].devToolsProbe = await app.evaluate(observeDevToolsDisabled, ids[role]);
      validateSecurity(security[role]);
    }
    validatePartitionIsolation(security.partitionIsolation);
  } catch { demand(false, 'PRODUCT_SECURITY_ASSERTION_FAILED'); }
  await page.getByRole('navigation', { name: '主导航', exact: true }).waitFor();
  await haru.getByRole('main', { name: 'Haru 桌面小窗', exact: true }).waitFor();
  return { app, page, haru, ids, identity, origin, owned, port: saved.port,
    endpoints: windows.listeners.filter(value => [owned.main.pid, owned.backend.pid].includes(value.pid)) };
}
function nativeControls(session, prepared) {
  demand(mode === 'mock' && broker?.mode === 'MOCK', 'INVALID_HARNESS');
  const visible = role => session.app.evaluate(({ BrowserWindow }, id) => {
    const win = BrowserWindow.fromId(id);
    return win && !win.isDestroyed() ? win.isVisible() : null;
  }, session.ids[role]);
  return {
    origin: session.origin,
    async assertIdentity() {
      const current = await waitForDesktopSurfaces(session.app, session.origin);
      demand(current.owner.page === session.page && current.haru.page === session.haru, 'INSTALLED_PROCESS_IDENTITY_MISMATCH');
      const snapshot = await ownedSnapshot(prepared);
      for (const process of [session.owned.main, session.owned.backend]) demand(snapshot.processes.some(value =>
        value.pid === process.pid && value.created === process.created && sameWindowsPath(value.path, process.path)), 'INSTALLED_PROCESS_IDENTITY_MISMATCH');
    },
    async prepareTray() {
      await session.app.evaluate(installTrayObserver);
      const pin = session.haru.getByRole('button', { name: 'Haru 始终置顶', exact: true });
      const initial = await pin.getAttribute('aria-pressed');
      demand(['true', 'false'].includes(initial), 'HARU_SYNC_FAILED');
      for (const expected of [initial === 'true' ? 'false' : 'true', initial]) {
        await pin.click(); await until(async () => await pin.getAttribute('aria-pressed') === expected);
      }
    },
    async hideOwner() {
      await (await session.app.browserWindow(session.page)).evaluate(win => win.close());
      await until(async () => await visible('owner') === false);
      demand(!session.page.isClosed(), 'HARU_SYNC_FAILED');
    },
    async waitVisible(role) { demand(['owner', 'haru'].includes(role), 'INVALID_HARNESS'); await until(async () => await visible(role) === true); },
    async waitHidden(role) { demand(['owner', 'haru'].includes(role), 'INVALID_HARNESS'); await until(async () => await visible(role) === false); },
    async hideHaru() { await session.app.evaluate(invokeTrayAction, 'hide'); await until(async () => await visible('haru') === false); },
    async restoreHaru() { await session.app.evaluate(invokeTrayAction, 'show'); await until(async () => await visible('haru') === true); },
  };
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
  demand(prepared.schema === 1 && prepared.mode === mode && prepared.scope === scope && prepared.root === root && prepared.profile === path.join(process.env.APPDATA, 'OfferPilot Desktop') &&
    prepared.installDir === path.join(root, '安装 Application') && prepared.exe === path.join(prepared.installDir, 'OfferPilot Desktop.exe') &&
    prepared.backend === path.join(prepared.installDir, 'resources/backend/offerpilot-backend.exe') &&
    prepared.helperSha === (mode === 'live' ? process.env.AI_APPROVED_HELPER_SHA : process.env.GITHUB_SHA) && prepared.requestSha === process.env.GITHUB_SHA &&
    prepared.profileOwned === false, 'PREPARED_IDENTITY_MISMATCH');
  const carryForward = mode === 'live' ? await loadPreparedCarryForward(prepared) : undefined;
  const binding = { sessionId: RECOVERY.sessionId, runId: process.env.GITHUB_RUN_ID,
    runAttempt: Number(process.env.GITHUB_RUN_ATTEMPT), helperCommit: prepared.helperSha, requestCommit: prepared.requestSha };
  // Preserve the authenticated historical cost even if launch/profile/broker creation later fails.
  if (mode === 'live') ledger = recoveryBaselineSnapshot(carryForward, binding);
  demand(await hash(prepared.exe) === prepared.exeSha256 && !await exists(prepared.profile), 'PREPARED_IDENTITY_MISMATCH');
  demand((await ownedSnapshot(prepared)).processes.length === 0, 'PREEXISTING_PRODUCT_PROCESS');
  await fs.mkdir(prepared.profile); // exclusive, never overwrite or adopt a real profile
  prepared.profileOwned = true;
  await fs.writeFile(statePath, JSON.stringify(prepared));
  broker = await brokerFactory({ providerKey, ledgerPath: path.join(root, 'session-ledger.json'),
    ...binding, carryForward, ...(recovery ? { executionStartedAt } : {}) });
  demand((broker.snapshot().carryForward !== undefined) === recovery, 'INVALID_HARNESS');
  let scenarioBroker = broker;
  const mockPilotProof = mockPilotProofFactory?.({ mode, broker });
  demand(mockPilotProofFactory === undefined || typeof mockPilotProof === 'function', 'INVALID_HARNESS');
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
  lifetime = createRunLifetime(deadlineMs, recovery ? { monotonicDeadline: executionStartedAt + RECOVERY.executionMs } : {});
  await lifetime.run(async () => {
    const first = await launchOwned(prepared);
    const { page, haru } = first;
    const mockSession = mockHaruSessionFactory?.({ mode, broker });
    if (mockHaruSessionFactory) demand(mockSession && typeof mockSession.afterPilot === 'function'
      && typeof mockSession.afterRestart === 'function' && typeof mockSession.diagnostic === 'function', 'INVALID_HARNESS');
    const native = mockSession ? nativeControls(first, prepared) : undefined;
    const synthetic = syntheticApi(page);
    const api = async (...args) => { lifetime.assertActive(); const value = await synthetic(...args); lifetime.assertActive(); return value; };
    const fixture = await prepareSyntheticProfile(api);
    await refreshSyntheticProfile(page, broker, fixture, deadlineMs, { mode, scope });
    if (recovery) {
      const elapsedBeforeUiMs = Math.floor(performance.now() - executionStartedAt);
      const remainingBeforeUiMs = Math.floor(broker.remainingMs());
      if (Number.isSafeInteger(elapsedBeforeUiMs) && elapsedBeforeUiMs >= 0 && elapsedBeforeUiMs <= 600000 &&
        Number.isSafeInteger(remainingBeforeUiMs) && remainingBeforeUiMs >= 0 && remainingBeforeUiMs <= RECOVERY.executionMs)
        report.executionTiming = { windowMs: RECOVERY.executionMs, elapsedBeforeUiMs, remainingBeforeUiMs };
    }
    const result = await runUiScenarios({ page, haru, api, broker: scenarioBroker, fixture,
      capture, mode, scope, mockContinuation, mockPilotProof,
      mockHaruSession: mockSession ? args => mockSession.afterPilot({ ...args, native, capture }) : undefined,
      deadlineMs });
    report.scenarios = selectedCases.map(id => result.results.find(row => row.id === id));
    if (result.allPassed && mockSession) {
      demand(mode === 'mock' && broker.mode === 'MOCK' && !broker.snapshot().active, 'INVALID_HARNESS');
      const beforeRestart = JSON.stringify(broker.snapshot());
      await first.app.evaluate(invokeTrayAction, 'quit');
      let exited;
      await until(async () => { exited = await ownedSnapshot(prepared); return exited.processes.length === 0; }, 45000);
      let portsClosed = false;
      await until(async () => { portsClosed = (await Promise.all(first.endpoints.map(endpointOpen))).every(open => !open); return portsClosed; });
      const second = await launchOwned(prepared);
      const restart = assertHaruRestartFacts(first, second, exited, portsClosed);
      const secondNative = nativeControls(second, prepared);
      try {
        const checks = await mockSession.afterRestart({ page: second.page, haru: second.haru,
          native: secondNative, capture, restart, deadlineMs });
        demand(JSON.stringify(broker.snapshot()) === beforeRestart, 'UNEXPECTED_PROVIDER_REQUESTS');
        Object.assign(report.scenarios.find(row => row.id === 'pilot-stream').checks, checks);
      } catch (error) {
        // Reuse the existing fixed failure-image guard only for these same
        // native windows. A secondary diagnostic failure never replaces it.
        try {
          lifetime.assertActive(); await secondNative.assertIdentity();
          for (const [id, surface] of [['failure-owner', second.page], ['failure-haru', second.haru]]) {
            await capture?.(id, surface, 'pilot-stream');
          }
        } catch { /* Keep the original restart failure. */ }
        throw error;
      } finally { report.haruSessionDiagnostic = mockSession.diagnostic(); }
    }
    lifetime.assertActive();
    demand(!externalRendererRequest, 'UNEXPECTED_RENDERER_NETWORK');
    report.status = result.allPassed ? 'PASS' : 'FAIL';
    const completionOnly = report.scenarios.some(row => row.code === 'LIVE_COMPLETION_ONLY');
    report.code = !result.allPassed ? 'UI_CASES_INCOMPLETE' : recovery
      ? (completionOnly ? 'PILOT_HARU_RECOVERY_COMPLETION_ONLY' : 'PILOT_HARU_RECOVERY_PASSED')
      : (completionOnly ? 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY' : 'ALL_UI_CASES_PASSED');
  }, async () => {
    try { await broker.close(); } catch { cleanupFailure = 'BROKER_CLEANUP_FAILED'; }
  });
} catch (error) {
  failure = safeCode(error); report.status = 'BLOCKED'; report.code = failure;
} finally {
  if (broker) { try { await broker.close(); } catch { cleanupFailure = 'BROKER_CLEANUP_FAILED'; }
    try { ledger = broker.snapshot(); } catch { cleanupFailure = 'LEDGER_UNAVAILABLE'; } }
  if (ledger.journalFailed) cleanupFailure = 'LEDGER_PERSISTENCE_FAILED';
  report.screenshotEvidence = screenshotEvidence?.snapshot();
  // Save the durable-cost projection before deleting its owned scratch journal.
  if (evidence) { try { await saveEvidence(evidence, { ...report, status: 'BLOCKED', code: 'CLEANUP_PENDING', cleanupCode: 'CLEANUP_PENDING', cleanupPassed: false }, ledger, [providerKey]); }
    catch { cleanupFailure = 'EVIDENCE_WRITE_BLOCKED'; } }
  let appClosing;
  if (appResources.length) {
    appClosing = Promise.allSettled(appResources.map(resource => Promise.resolve().then(() => resource.app.close())
      .then(() => { resource.closeConfirmed = true; })));
    await waitForResourceSettlement(appClosing, 15000);
  }
  try { await cleanOwnedFiles(process.env, mode); report.cleanupPassed = true; }
  catch (error) { cleanupFailure = safeCode(error) === 'OWNED_PROCESS_CLEANUP_FAILED'
    ? 'OWNED_PROCESS_CLEANUP_FAILED' : 'PROFILE_CLEANUP_FAILED'; }
  if (appClosing) await waitForResourceSettlement(appClosing, 2000);
  report.cleanupCode = cleanupFailure || 'CLEANUP_PASSED';
  if (failure || cleanupFailure) {
    report.status = 'BLOCKED';
    report.code = failure || (['NOT_STARTED', 'ALL_UI_CASES_PASSED', 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY', 'PILOT_HARU_RECOVERY_PASSED', 'PILOT_HARU_RECOVERY_COMPLETION_ONLY'].includes(report.code) ? cleanupFailure : report.code);
  }
  if (evidence) {
    try { await saveEvidence(evidence, report, ledger, [providerKey]); finalEvidenceSaved = true; }
    catch { report.status = 'BLOCKED'; report.code = failure || report.code; report.cleanupCode = 'EVIDENCE_WRITE_BLOCKED'; }
  }
  console.log(mode === 'mock' ? `MOCK installed UI validation: ${report.status}; no real provider evidence.` :
    report.status === 'PASS' ? (report.code === 'PILOT_HARU_RECOVERY_COMPLETION_ONLY'
      ? 'Single Pilot/Haru recovery completed; sustained Pilot streaming NOT proven; experimental evidence only.'
      : 'Single Pilot/Haru recovery passed; experimental evidence only.') : `Real AI validation blocked or incomplete: ${report.code}`);
  return { ...report, finalEvidenceSaved,
    appResourcesReleased: !appLaunchPending && appResources.every(resource => resource.launcherClosed && resource.closeConfirmed),
    exitCode: report.status === 'PASS' && report.cleanupPassed ? 0 : 1 };
}

}
