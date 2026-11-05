import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import net from 'node:net';
import { loadHaruAssetExpectations, observeHaruAssets } from '../live2d-assets.mjs';
import { waitForDialogSettled, waitForPaintedCharacter } from './dialog-readiness.mjs';
import { loadOriginalEvidence } from './retry.mjs';
import { preferenceObservation, assertDevToolsPreference, assertDevToolsProbe, probeDevToolsDisabled } from './security.mjs';
import { createPresenceSuppression, capturePresenceRequest, createSerialRequestQueue } from './presence-guard.mjs';
import { PROFILE, DRAFT, MAIL, TEXT, samePath, buttonName, classifyRequest, safeRequest, ownedProcesses, earlyOwnedProcesses,
  validateSockets, sha256, treeDigest, assertNativeMenu } from './contract.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const retryOriginal = process.env.LOCALIZATION_RETRY_ORIGINAL === '1';
const productRoot = path.resolve(process.env.LOCALIZATION_PRODUCT_SOURCE_DIR || root);
const evidence = path.resolve(process.env.LOCALIZATION_EVIDENCE_DIR || path.join(here, 'evidence'));
const report = {
  status: 'running', scope: retryOriginal ? 'same-original-installer-localization-retry' : 'new-unsigned-NSIS-installed-localization-smoke', releaseReady: false,
  sourceSHA: process.env.LOCALIZATION_SOURCE_SHA || process.env.GITHUB_SHA,
  runURL: process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY && process.env.GITHUB_RUN_ID
    ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}` : null,
  instrumentation: {
    renderer: 'real product Settings clicks and synthetic unsent text, no fixtures or response replacement',
    electron: 'Playwright inspector launch of installed executable; security switches remain enabled',
    localeReads: 'read-only trusted owner/Haru preload getLocale and DOM lang',
    applicationMenu: 'read-only native Menu object; explicit production quit callback invoked through inspector',
    tray: 'transparent setContextMenu observation; production call, this and return preserved',
    nativeMenuPointerValidated: false, unsignedSmartScreenValidated: false, undebuggedLaunchValidated: false,
    devTools: 'negative openDevTools event/state/contents probes on both installed windows; runtime must match the source lock; omitted getter requires an explicitly reviewed exact Electron version plus hash-verified packaged constructor policies',
    network: 'renderer requests abort on non-GET/HEAD or unexpected origin; process TCP snapshots at checkpoints',
    presence: 'all POSTs remain aborted; exact owner presence heartbeats are separately counted only after real backend opt-out, identity, frame and schema verification; UI is tested with this background write suppressed',
    networkLimit: 'Renderer guard begins after Playwright attaches. OS snapshots are not continuous backend packet capture.',
  },
  exclusions: ['real mail', 'real models', 'credential setup', 'mail submission', 'business confirmation',
    'full release gate', 'language switch inside a modal that blocks Settings', 'unsent draft persistence across process exit'],
  stages: [], launchAttempts: [], launches: [], screenshots: [], blockedRequests: [], apiReads: 0, localReads: 0,
  requiredStages: [
    'install-new-nsis', 'launch-1-installed-identity-security-and-locale',
    'launch-1-zh-CN-mail-preview-boundary', 'launch-1-settings-zh-CN-to-en-US',
    'launch-1-en-US-mail-preview-boundary', 'launch-1-explicit-menu-exit',
    'launch-2-installed-identity-security-and-locale', 'launch-2-settings-en-US-to-zh-CN',
    'launch-2-explicit-menu-exit', 'launch-3-installed-identity-security-and-locale',
    'launch-3-explicit-menu-exit', 'immutable-installed-payload',
  ],
};
let stage = 'environment';
let active;
let owned = [];
let paths;
let environment;
let installedIdentity;
let sourceIdentity;
let originalEvidence;
let _electron;
const presenceTrackers = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const exists = async filename => fs.access(filename).then(() => true, () => false);
let reportWrite = Promise.resolve();
async function saveReport() {
  report.suppressedPresence = presenceTrackers.map(({ number, tracker }) => ({ launch: number, ...tracker.summary() }));
  const text = JSON.stringify(report, null, 2) + '\n';
  reportWrite = reportWrite.then(() => fs.writeFile(path.join(evidence, 'result.json'), text));
  await reportWrite;
}
function ensureSafe({ allowPendingPresence = false } = {}) {
  assert.equal(report.blockedRequests.length, 0, 'unexpected request was aborted; acceptance fails closed');
  for (const { tracker } of presenceTrackers) {
    if (allowPendingPresence && tracker === active?.presence && !active.presenceVerified) continue;
    tracker.assertSettled();
  }
}
async function checkpoint(name, details = {}) {
  if (active) await verifyPresenceContext();
  ensureSafe();
  report.stages.push({ name, status: 'passed', ...details });
  await saveReport();
  console.log(`Passed: ${name}`);
}
async function launchPhase(name, observations) {
  stage = `launch-${active.number}-${name}`;
  active.attempt.phase = name;
  if (observations) active.attempt.observations.push({ phase: name, ...observations });
  await saveReport();
}
async function until(test, timeout = 30000, safety = {}) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { ensureSafe(safety); if (await test()) return; await delay(200); }
  throw new Error('bounded-wait-timeout');
}
async function command(program, args, env = process.env, timeout = 240000) {
  return new Promise((resolve, reject) => {
    const child = spawn(program, args, { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    const chunks = [];
    let bytes = 0;
    const timer = setTimeout(() => { child.kill(); reject(new Error('owned-command-timeout')); }, timeout);
    child.stdout.on('data', chunk => { bytes += chunk.length; if (bytes <= 16 * 1024 * 1024) chunks.push(chunk); });
    // Never publish child stderr, inspector endpoints, tokens, or a whole environment.
    child.stderr.resume();
    child.once('error', () => { clearTimeout(timer); reject(new Error('command-start-failed')); });
    child.once('close', code => {
      clearTimeout(timer);
      if (code !== 0 || bytes > 16 * 1024 * 1024) reject(new Error('command-failed'));
      else resolve(Buffer.concat(chunks).toString('utf8').replace(/^\uFEFF/, '').trim());
    });
  });
}
async function windows(mode, extra = {}) {
  return JSON.parse(await command('pwsh.exe', ['-NoProfile', '-NonInteractive', '-File', path.join(here, 'windows.ps1'), '-Mode', mode], { ...environment, ...extra }));
}
async function port() {
  const saved = JSON.parse(await fs.readFile(path.join(paths.userData, 'desktop-port.json'), 'utf8'));
  assert.ok(Number.isInteger(saved.port) && saved.port > 0 && saved.port < 65536, 'saved backend port must be a valid TCP port');
  return saved.port;
}
async function shot(page, label) {
  ensureSafe();
  await page.screenshot({ path: path.join(evidence, `${label}.png`), timeout: 15000 });
  report.screenshots.push({ file: `${label}.png`, label, launch: active.number });
  await saveReport();
}
async function verifyPayload() {
  const identity = {};
  for (const [key, relative] of [['exeSha256', 'OfferPilot Desktop.exe'], ['asarSha256', 'resources/app.asar'], ['backendExeSha256', 'resources/backend/offerpilot-backend.exe']]) {
    const hash = await sha256(path.join(paths.install, relative));
    const expected = originalEvidence ? originalEvidence.evidence['result.json'].installedIdentityBefore[key] : await sha256(path.join(paths.build, relative));
    assert.equal(hash, expected, 'installed file differs from the original same-built payload identity');
    identity[key] = hash;
  }
  for (const name of ['web', 'backend']) {
    const installed = await treeDigest(path.join(paths.install, 'resources', name));
    const built = originalEvidence ? originalEvidence.evidence[`installed-${name}-sha256.json`] : await treeDigest(path.join(paths.build, 'resources', name));
    assert.deepEqual(installed, built, 'installed resources differ from the same-built payload');
    identity[`${name}Tree`] = { sha256: installed.sha256, files: installed.files.length };
    await fs.writeFile(path.join(evidence, `installed-${name}-sha256.json`), JSON.stringify(installed, null, 2) + '\n');
  }
  return identity;
}
async function verifySource() {
  const tracked = (await command('git', ['-C', productRoot, 'ls-files', '-z', '--', 'desktop/*.cjs', 'desktop/*.py', 'desktop/*.spec',
    'desktop/package.json', 'desktop/package-lock.json', 'src/offerpilot', 'web/src', 'web/index.html', 'web/vite.config.ts',
    'web/package.json', 'web/package-lock.json', 'pyproject.toml', 'uv.lock'])).split('\0').filter(Boolean).sort();
  assert.ok(tracked.length > 0, 'product source manifest must not be empty');
  const files = await Promise.all(tracked.map(async file => ({ file, sha256: await sha256(path.join(productRoot, file)) })));
  const desktopRequire = createRequire(path.join(root, 'desktop/package.json'));
  const { extractFile } = await import(pathToFileURL(desktopRequire.resolve('@electron/asar')).href);
  const desktopPackage = JSON.parse(await fs.readFile(path.join(productRoot, 'desktop/package.json'), 'utf8'));
  const packedSources = desktopPackage.build.files.filter(name => name.endsWith('.cjs'));
  assert.ok(['main.cjs', 'preload.cjs', 'locale.cjs', 'haru.cjs'].every(file => packedSources.includes(file)), 'ASAR source mapping must include main, preload, locale and Haru modules');
  for (const file of packedSources) {
    assert.deepEqual(extractFile(path.join(paths.install, 'resources/app.asar'), file),
      await fs.readFile(path.join(productRoot, 'desktop', file)), 'installed ASAR source must match the immutable product checkout');
  }
  const identity = { sourceSHA: report.sourceSHA, files, packedSourcesVerified: packedSources };
  if (originalEvidence) assert.deepEqual(identity, originalEvidence.evidence['source-sha256.json'], 'product checkout must match the original retained source manifest');
  else assert.deepEqual(await treeDigest(path.join(paths.install, 'resources/web')), await treeDigest(path.join(productRoot, 'web/dist')),
    'installed frontend assets must match this checkout build');
  report.devToolsConstructorPolicy = {
    lockedElectron: desktopPackage.devDependencies.electron,
    installedSourcesHashMatched: true,
    ownerPackagedDisabled: /devTools:\s*!app\.isPackaged\b/.test(extractFile(path.join(paths.install, 'resources/app.asar'), 'main.cjs').toString('utf8')),
    haruDisabled: /devTools:\s*false\b/.test(extractFile(path.join(paths.install, 'resources/app.asar'), 'haru.cjs').toString('utf8')),
  };
  return identity;
}
async function installRequestGuard(app) {
  const launch = active;
  await app.context().routeWebSocket('**/*', socket => {
    report.blockedRequests.push(safeRequest(socket.url(), 'WEBSOCKET', { reason: 'unexpected-websocket' }));
    socket.close({ code: 1008, reason: 'Read-only localization validation' });
    void saveReport();
  });
  await app.context().route('**/*', route => {
    const request = route.request();
    let captured;
    try { captured = capturePresenceRequest(request); } catch { /* Still abort; unverifiable origins cannot be suppressed. */ }
    return launch.requestQueue.schedule(async () => {
      let decision, origin;
      try { origin = `http://127.0.0.1:${await port()}`; decision = classifyRequest(request.url(), request.method(), origin); }
      catch { decision = { allowed: false, reason: 'unverified-backend-origin' }; }
      if (!decision.allowed) {
        let aborted = false;
        try {
          await route.abort('blockedbyclient'); aborted = true;
          assert.ok(captured, 'presence frame provenance must be captured before abort');
          launch.presence.recordAborted({ ...captured, origin, aborted });
        } catch {
          report.blockedRequests.push(safeRequest(request.url(), request.method(),
            { reason: aborted ? decision.reason : 'request-abort-unconfirmed' }));
        }
        await saveReport();
        return;
      }
      report.localReads++;
      if (new URL(request.url()).pathname.startsWith('/api/')) report.apiReads++;
      await route.continue();
    });
  });
}
async function verifyPresenceContext() {
  await active.requestQueue.drain();
  const observed = await active.page.evaluate(async () => {
    const response = await fetch('/api/companion', { method: 'GET', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('companion-opt-out-read-failed');
    const snapshot = await response.json();
    return { ownerRole: window.offerpilotDesktop?.role, snapshot: { identity: snapshot.identity, enabled: snapshot.settings?.enabled } };
  });
  active.presence.verifyContext({ origin: active.origin, ownerPage: active.page, ownerMainFrame: active.page.mainFrame(),
    ownerURL: active.page.url(), ...observed });
  active.presenceVerified = true;
  await active.requestQueue.drain();
  ensureSafe();
}
async function launch(number, locale) {
  stage = `launch-${number}-preflight`;
  assert.deepEqual((await windows('snapshot')).processes, [], 'old installed processes must exit before restart');
  const startedAt = Date.now();
  const attempt = { number, phase: 'electron-start', observations: [] };
  report.launchAttempts.push(attempt);
  stage = `launch-${number}-electron-start`;
  await saveReport();
  const app = await _electron.launch({ executablePath: paths.exe, cwd: paths.install, env: environment,
    chromiumSandbox: true, bypassCSP: false, timeout: 90000 });
  active = { app, number, child: app.process(), startedAt, attempt,
    presence: createPresenceSuppression(), presenceVerified: false, requestQueue: createSerialRequestQueue() };
  presenceTrackers.push({ number, tracker: active.presence });
  // Guard installation must remain the first awaited action after attachment.
  stage = `launch-${number}-request-guards`;
  attempt.phase = 'request-guards';
  await installRequestGuard(app);
  await launchPhase('main-identity');
  const identity = await app.evaluate(({ app }) => ({ pid: process.pid, exe: process.execPath, userData: app.getPath('userData'), packaged: app.isPackaged }));
  await launchPhase('main-identity', { packaged: identity.packaged === true,
    installedExecutableMatches: samePath(identity.exe, paths.exe), realProfileMatches: samePath(identity.userData, paths.userData) });
  assert.equal(identity.packaged, true, 'installed Electron must report app.isPackaged true');
  assert.ok(samePath(identity.exe, paths.exe), 'inspector process executable must equal the installed executable');
  assert.ok(samePath(identity.userData, paths.userData), 'real APPDATA must be used; no redirected profile');
  await launchPhase('owned-main-process');
  owned = ownedProcesses((await windows('snapshot')).processes, identity.pid, paths.exe, paths.backend);
  await launchPhase('backend-port-file');
  await until(() => exists(path.join(paths.userData, 'desktop-port.json')), 90000, { allowPendingPresence: true });
  active.port = await port();
  active.origin = `http://127.0.0.1:${active.port}`;
  await launchPhase('renderer-roles');
  await until(async () => {
    const pages = app.windows();
    assert.ok(pages.length <= 2, 'unexpected extra product window');
    if (pages.length !== 2) return false;
    const candidates = await Promise.all(pages.map(async page => ({ page, url: page.url(), role: await page.evaluate(() => window.offerpilotDesktop?.role).catch(() => null) })));
    for (const candidate of candidates) {
      if (!candidate.role) return false;
      const url = new URL(candidate.url);
      await launchPhase('renderer-roles', { role: ['owner', 'haru'].includes(candidate.role) ? candidate.role : 'unexpected',
        originMatches: url.origin === active.origin, rootPath: url.pathname === '/',
        queryRole: url.search === '' ? 'owner' : url.search === '?desktopSurface=haru' ? 'haru' : 'unexpected' });
      assert.equal(url.origin, active.origin, 'renderer origin must equal the authenticated backend origin');
      assert.equal(url.pathname, '/', 'renderer must load the packaged root document');
      if (candidate.role === 'owner') { assert.equal(url.search, '', 'owner renderer must have no surface query'); active.page = candidate.page; }
      else if (candidate.role === 'haru') { assert.equal(url.search, '?desktopSurface=haru', 'Haru renderer must have its exact bounded surface query'); active.haru = candidate.page; }
      else assert.fail('unrecognized preload role');
    }
    assert.ok(active.page && active.haru && active.page !== active.haru, 'distinct owner and Haru renderers must both exist');
    return true;
  }, 90000, { allowPendingPresence: true });
  for (const page of [active.page, active.haru]) page.setDefaultTimeout(20000);
  await launchPhase('verify-disabled-companion-presence');
  await verifyPresenceContext();
  await launchPhase('initial-owner-localized-landmark', { matched: await active.page.getByRole('navigation', { name: TEXT[locale].navigation, exact: true }).count(),
    lang: await active.page.evaluate(() => ['zh-CN', 'en-US'].includes(document.documentElement.lang) ? document.documentElement.lang : 'unexpected') });
  await active.page.getByRole('navigation', { name: TEXT[locale].navigation, exact: true }).waitFor();
  await launchPhase('initial-companion-localized-landmark', { matched: await active.haru.getByRole('main', { name: TEXT[locale].haru, exact: true }).count(),
    lang: await active.haru.evaluate(() => ['zh-CN', 'en-US'].includes(document.documentElement.lang) ? document.documentElement.lang : 'unexpected') });
  await active.haru.getByRole('main', { name: TEXT[locale].haru, exact: true }).waitFor();
  await launchPhase('runtime-security-read');
  const security = await app.evaluate(({ app, BrowserWindow, session }) => {
    const windows = BrowserWindow.getAllWindows();
    const owner = windows.find(win => !new URL(win.webContents.getURL()).search);
    const haru = windows.find(win => new URL(win.webContents.getURL()).search === '?desktopSurface=haru');
    const details = windows.map(win => {
      const prefs = win.webContents.getLastWebPreferences();
      return { windowId: win.id, nodeIntegration: prefs.nodeIntegration, contextIsolation: prefs.contextIsolation, sandbox: prefs.sandbox,
        webSecurity: prefs.webSecurity, devTools: prefs.devTools, devToolsOpened: win.webContents.isDevToolsOpened() };
    });
    return { details, runtimeElectron: process.versions.electron, packaged: app.isPackaged,
      isolated: owner.webContents.session !== haru.webContents.session,
      ownerPartition: owner.webContents.session === session.fromPartition('persist:offerpilot-desktop'),
      haruPartition: haru.webContents.session === session.fromPartition('offerpilot-haru'),
      haruEphemeral: haru.webContents.session.getStoragePath() === null,
      unsafeSwitches: ['no-sandbox', 'disable-web-security', 'disable-site-isolation-trials', 'allow-running-insecure-content', 'ignore-certificate-errors'].filter(name => app.commandLine.hasSwitch(name)) };
  });
  const safeBoolean = value => typeof value === 'boolean' ? value : null;
  await launchPhase('runtime-security-assertions', { security: {
    details: security.details.map(({ windowId, devTools, ...detail }) => ({
      ...Object.fromEntries(Object.entries(detail).map(([name, value]) => [name, safeBoolean(value)])),
      devToolsPreference: preferenceObservation(devTools),
    })),
    runtimeElectronMatchesLocked: security.runtimeElectron === report.devToolsConstructorPolicy.lockedElectron,
    ...Object.fromEntries(['packaged', 'isolated', 'ownerPartition', 'haruPartition', 'haruEphemeral'].map(name => [name, safeBoolean(security[name])])),
    unsafeSwitches: security.unsafeSwitches,
  } });
  assert.deepEqual(security.details.map(({ windowId, devTools, ...detail }) => detail), Array.from({ length: 2 }, () => ({ nodeIntegration: false, contextIsolation: true,
    sandbox: true, webSecurity: true, devToolsOpened: false })), 'both installed renderers must retain every exposed packaged security preference');
  for (const name of ['packaged', 'isolated', 'ownerPartition', 'haruPartition', 'haruEphemeral']) assert.equal(security[name], true, `installed security invariant must remain true: ${name}`);
  assert.deepEqual(security.unsafeSwitches, [], 'installed Electron must have no unsafe Chromium switches');
  for (const detail of security.details) assertDevToolsPreference(detail.devTools, security.runtimeElectron, report.devToolsConstructorPolicy);
  for (const [index, detail] of security.details.entries()) {
    await launchPhase('devtools-blocked-open-probe', { productWindowIndex: index, devToolsPreference: preferenceObservation(detail.devTools) });
    detail.devToolsProbe = await app.evaluate(probeDevToolsDisabled, detail.windowId);
    await launchPhase('devtools-blocked-open-probe', { productWindowIndex: index, probe: detail.devToolsProbe });
    assertDevToolsProbe(detail.devToolsProbe);
  }
  await launchPhase('owned-backend-and-sockets');
  const snapshot = await windows('snapshot');
  await launchPhase('owned-backend-and-sockets', { processCount: snapshot.processes.length,
    listenerCount: snapshot.listeners.length, observedOutboundTcpCount: snapshot.outbound.length });
  owned = ownedProcesses(snapshot.processes, identity.pid, paths.exe, paths.backend);
  assert.equal(owned.length, 2, 'actual installed backend must be a direct owned child');
  validateSockets(snapshot, owned[1].pid, active.port);
  active.info = { number, initialLocale: locale, mainPid: owned[0].pid, backendPid: owned[1].pid,
    port: active.port, realAppData: true, security, snapshotsWithoutOutboundTcp: 1 };
  report.launches.push(active.info);
  await launchPhase('owner-haru-read-isolation');
  const ownerHealth = () => active.page.evaluate(async () => (await fetch('/api/health', { signal: AbortSignal.timeout(5000) })).status);
  assert.equal(await ownerHealth(), 200, 'trusted owner must reach the authenticated backend');
  const haruRead = await active.haru.evaluate(async () => {
    try { return { blocked: false, status: (await fetch('/api/health', { signal: AbortSignal.timeout(5000) })).status }; }
    catch (error) { return { blocked: error.name === 'TypeError' }; }
  });
  assert.deepEqual(haruRead, { blocked: true }, 'Haru must remain unable to read the backend; a timeout or HTTP 401 is not equivalent');
  assert.equal(await ownerHealth(), 200, 'trusted backend must remain available after Haru read denial');
  active.info.haruApiReadIsolation = { ownerHealthBeforeAndAfter: 200, haruGetBlockedBeforeHttp: true, onlyReadRequests: true };
  if (number === 1) {
    // One read-only reload after identity, origin and security verification;
    // attach before navigation so initial cached requests cannot be missed.
    await launchPhase('haru-observed-assets-reload');
    const expectedHaruAssets = await loadHaruAssetExpectations({
      sourceRoot: path.join(productRoot, 'web', 'public'), servedRoot: path.join(paths.install, 'resources', 'web'),
    });
    const haruAssets = observeHaruAssets(active.haru, { origin: active.origin, expected: expectedHaruAssets });
    try {
      await active.haru.reload({ waitUntil: 'domcontentloaded' });
      await active.haru.getByRole('main', { name: TEXT[locale].haru, exact: true }).waitFor();
      active.info.live2dAssets = await haruAssets.finish();
      await saveReport();
      ensureSafe();
      assert.equal(active.info.live2dAssets.status, 'passed', 'All four Haru responses must complete with source/installed bytes');
    } finally {
      active.info.live2dAssets ||= await haruAssets.finish();
      haruAssets.dispose();
      await saveReport();
    }
  }
  await launchPhase('observe-tray-and-expand-haru');
  await app.evaluate(({ Tray }) => {
    const original = Tray.prototype.setContextMenu;
    globalThis.__offerpilotLocalizationTrayLabels = null;
    Tray.prototype.setContextMenu = function (menu) {
      const result = original.call(this, menu);
      globalThis.__offerpilotLocalizationTrayLabels = menu.items.filter(item => item.type !== 'separator').map(item => item.label);
      return result;
    };
  });
  await active.haru.getByRole('button', { name: TEXT[locale].expand, exact: true }).click();
  await launchPhase('wait-unconfigured-owner-mirror');
  await until(() => active.haru.evaluate(async () => {
    const state = await window.offerpilotDesktop.getState();
    return state.connected && state.snapshot?.aiConfigured === false;
  }));
  await launchPhase('real-haru-close-collapses-to-character');
  await active.haru.getByRole('button', { name: TEXT[locale].close, exact: true }).click();
  await active.haru.getByRole('button', { name: TEXT[locale].expand, exact: true }).waitFor();
  const collapsed = await active.haru.evaluate(async () => window.offerpilotDesktop.getState());
  assert.equal(collapsed.expanded, false, 'X must collapse the conversation');
  assert.equal(collapsed.visible, true, 'X must keep the character visible');
  await waitForPaintedCharacter(active.haru);
  await shot(active.haru, `${number}-${locale}-haru-close-keeps-character`);
  await active.haru.getByRole('button', { name: TEXT[locale].expand, exact: true }).click();
  await launchPhase('real-haru-pin-controls');
  const pin = active.haru.getByRole('button', { name: TEXT[locale].pin, exact: true });
  const before = await pin.getAttribute('aria-pressed');
  await pin.click();
  await until(async () => await pin.getAttribute('aria-pressed') !== before);
  await pin.click();
  await until(async () => await pin.getAttribute('aria-pressed') === before);
  await launchPhase('initial-locale-and-menus');
  await assertLocale(locale, `${number}-initial`);
  await checkpoint(`launch-${number}-installed-identity-security-and-locale`);
}
async function assertLocale(locale, label) {
  const copy = TEXT[locale];
  await until(async () => (await Promise.all([active.page, active.haru].map(page => page.evaluate(async () => ({
    locale: await window.offerpilotLocale.getLocale(), lang: document.documentElement.lang,
    writer: typeof window.offerpilotLocale.setLocale,
  }))))).every(value => value.locale === locale && value.lang === locale));
  const readers = await Promise.all([active.page, active.haru].map(page => page.evaluate(() => typeof window.offerpilotLocale.setLocale)));
  assert.deepEqual(readers, ['function', 'undefined'], 'Haru must remain read-only for locale');
  await active.page.getByRole('navigation', { name: copy.navigation, exact: true }).waitFor();
  await active.haru.getByRole('main', { name: copy.haru, exact: true }).waitFor();
  const menus = await active.app.evaluate(({ Menu }) => ({
    application: Menu.getApplicationMenu().items.map(item => ({ label: item.label,
      children: item.submenu?.items.map(child => ({ label: child.label, enabled: child.enabled })) || [] })),
    tray: globalThis.__offerpilotLocalizationTrayLabels,
  }));
  assertNativeMenu(menus.application, locale);
  assert.deepEqual(menus.tray, copy.tray, 'observed native tray menu must track locale');
  active.info.menuObservations ??= [];
  active.info.menuObservations.push({ label, locale, ...menus });
  ensureSafe();
}
async function businessSnapshot() {
  const result = await active.page.evaluate(async () => {
    const read = async route => {
      const response = await fetch(route, { method: 'GET', credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('read-only-backend-snapshot-failed');
      return response.json();
    };
    const [applications, events, conversations, suggestions, status, secure] = await Promise.all([
      '/api/applications', '/api/application-events', '/api/chat/conversations', '/api/job-mail/suggestions',
      '/api/job-mail/status', '/api/job-mail/secure-setup/status',
    ].map(read));
    return { applications, events, conversations, suggestions,
      mail: { connection: status.connection, run: status.run, budget: status.budget, capabilities: status.capabilities },
      secure: { available: secure.available, configured: secure.configured, deletion_pending: secure.deletion_pending,
        credential_input_allowed: secure.credential_input_allowed, reason: secure.reason } };
  });
  assert.equal(result.secure.configured, false);
  assert.equal(result.secure.credential_input_allowed, false);
  assert.equal(result.secure.reason, 'local_setup_not_enabled');
  assert.equal(result.mail.connection, null);
  assert.equal(result.mail.run, null);
  assert.deepEqual(result.applications, []);
  assert.deepEqual(result.events, []);
  assert.deepEqual(result.conversations, []);
  ensureSafe();
  return result;
}
async function settings(locale) {
  const copy = TEXT[locale];
  await active.page.getByRole('navigation', { name: copy.navigation, exact: true }).getByRole('button', { name: copy.settings, exact: true }).click();
  await active.page.getByRole('combobox', { name: copy.language, exact: true }).waitFor();
  await active.page.getByRole('heading', { name: copy.mailbox, exact: true }).waitFor();
}
async function switchLocale(from, to) {
  stage = `launch-${active.number}-settings-${from}-to-${to}`;
  const baseline = await businessSnapshot();
  const draft = active.haru.locator('#desktop-haru-input');
  assert.equal(await draft.inputValue(), DRAFT);
  const select = active.page.getByRole('combobox', { name: TEXT[from].language, exact: true });
  // AntD's actual combobox input is covered by its selected-text span.
  await select.locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " ant-select ")][1]').locator('.ant-select-selector').click();
  await active.page.locator('.ant-select-item-option-content').filter({ hasText: to === 'en-US' ? /^English$/ : /^简体中文$/ }).click();
  await active.page.locator('.ant-select-dropdown:visible').waitFor({ state: 'hidden' });
  await assertLocale(to, `${active.number}-after-settings-click`);
  assert.equal(await draft.inputValue(), DRAFT, 'unsent Haru text must remain byte-for-byte unchanged');
  assert.equal(await active.haru.getByRole('button', { name: TEXT[to].send, exact: true }).isDisabled(), true);
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(paths.userData, 'desktop-locale.json'), 'utf8')), { locale: to });
  assert.deepEqual(await businessSnapshot(), baseline, 'language switch must not alter backend business or mailbox state');
  await shot(active.page, `${active.number}-${to}-settings-after-real-switch`);
  await shot(active.haru, `${active.number}-${to}-haru-draft-preserved`);
  validateSockets(await windows('snapshot'), active.info.backendPid, active.port);
  active.info.snapshotsWithoutOutboundTcp++;
  await checkpoint(stage, { settingsClick: true, draftPreserved: true, nativeAndHaruSynchronized: true, businessStateUnchanged: true });
}
async function mailPreview(locale) {
  stage = `launch-${active.number}-${locale}-mail-preview-boundary`;
  const copy = TEXT[locale];
  const baseline = await businessSnapshot();
  const panel = active.page.locator('section[aria-labelledby="job-mail-settings-title"]');
  await panel.scrollIntoViewIfNeeded();
  await shot(active.page, `${active.number}-${locale}-mail-settings-disabled-setup`);
  await panel.getByRole('button', { name: copy.guide, exact: true }).click();
  const guide = active.page.getByRole('dialog', { name: locale === 'zh-CN' ? '安全配置 QQ 邮箱' : 'Secure QQ Mail setup', exact: true });
  await waitForDialogSettled(guide);
  assert.equal(await guide.locator('input[type="password"]').count(), 0, 'disabled native setup must not solicit credentials');
  await shot(active.page, `${active.number}-${locale}-mail-safe-connection-guide`);
  await guide.getByRole('button', { name: locale === 'zh-CN' ? '关闭并释放临时会话' : 'Close and release temporary session', exact: true }).click();
  await guide.waitFor({ state: 'hidden' });
  await panel.getByRole('button', { name: copy.paste, exact: true }).click();
  const dialog = active.page.getByRole('dialog', { name: copy.paste, exact: true });
  await dialog.waitFor();
  const inputs = dialog.locator('input:not([type="hidden"])');
  assert.equal(await inputs.count(), 3);
  await inputs.nth(0).fill(MAIL.subject);
  await inputs.nth(1).fill(MAIL.sender);
  await inputs.nth(2).fill(MAIL.received);
  await dialog.locator('textarea').fill(MAIL.body);
  await dialog.getByRole('button', { name: copy.preview, exact: true }).click();
  await dialog.getByRole('button', { name: copy.submit, exact: true }).waitFor();
  assert.equal(await dialog.locator('pre').textContent(), MAIL.body, 'preview must retain untrusted mail as literal text');
  assert.ok((await dialog.textContent()).includes(MAIL.subject));
  assert.ok((await dialog.textContent()).includes(MAIL.sender));
  assert.ok((await dialog.textContent()).includes(MAIL.received));
  assert.equal(await dialog.locator('img,iframe,video,a[href]').count(), 0, 'mail HTML must not create active content');
  await shot(active.page, `${active.number}-${locale}-mail-preview-unsubmitted`);
  assert.deepEqual(await businessSnapshot(), baseline, 'local preview must not submit or mutate backend state');
  await dialog.getByRole('button', { name: copy.back, exact: true }).click();
  assert.deepEqual(await inputs.evaluateAll(nodes => nodes.map(node => node.value)), [MAIL.subject, MAIL.sender, MAIL.received]);
  assert.equal(await dialog.locator('textarea').inputValue(), MAIL.body);
  await dialog.getByRole('button', { name: buttonName(copy.cancel) }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.deepEqual(await businessSnapshot(), baseline);
  await checkpoint(stage, { previewOnly: true, rawContentPreserved: true, backPreservesDraft: true,
    mailSubmitted: false, credentialsEntered: false, modelCalled: false });
}
async function quitNormally() {
  stage = `launch-${active.number}-explicit-menu-exit`;
  const current = active;
  await verifyPresenceContext();
  const quit = await current.app.evaluate(({ Menu }) => {
    const matches = Menu.getApplicationMenu().items.flatMap(item => item.submenu?.items || [])
      .filter(item => ['退出并停止本地服务', 'Quit and Stop Local Service'].includes(item.label));
    if (matches.length !== 1 || matches[0].enabled === false || typeof matches[0].click !== 'function') throw new Error('explicit-quit-not-found');
    setTimeout(() => matches[0].click(matches[0], undefined, {}), 0);
    return true;
  });
  assert.equal(quit, true);
  await until(async () => (await windows('snapshot')).processes.length === 0, 60000);
  const portOpen = () => new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port: current.port });
    const done = open => { socket.destroy(); resolve(open); };
    socket.setTimeout(1000, () => done(false)); socket.once('connect', () => done(true)); socket.once('error', () => done(false));
  });
  await until(async () => !(await portOpen()), 10000);
  await current.requestQueue.drain();
  ensureSafe();
  current.info.explicitProductionMenuQuit = true;
  current.info.mainAndBackendExited = true;
  current.info.backendPortClosedBeforeRestart = true;
  active = undefined;
  owned = [];
  await checkpoint(stage);
}

await fs.mkdir(evidence, { recursive: true });
try {
  assert.equal(process.platform, 'win32', 'installed acceptance requires actual Windows');
  assert.equal(process.env.GITHUB_ACTIONS, 'true', 'dedicated CI runner required; never use a user computer');
  assert.ok(process.env.RUNNER_TEMP && process.env.APPDATA, 'Windows CI runtime paths are required');
  assert.match(report.sourceSHA || '', /^[0-9a-f]{40}$/, 'full original product source SHA is required');
  report.harnessSHA = await command('git', ['-C', root, 'rev-parse', 'HEAD']);
  assert.equal(report.harnessSHA, process.env.GITHUB_SHA, 'harness checkout must equal this workflow revision');
  if (retryOriginal) {
    for (const key of ['LOCALIZATION_PRODUCT_SOURCE_DIR', 'LOCALIZATION_ORIGINAL_EVIDENCE_DIR']) assert.ok(process.env[key] && path.isAbsolute(process.env[key]), `${key} must be absolute`);
    assert.notEqual(productRoot, root, 'same-installer retry keeps the original product checkout separate from its harness');
    originalEvidence = await loadOriginalEvidence(process.env.LOCALIZATION_ORIGINAL_EVIDENCE_DIR);
    assert.equal(report.sourceSHA, originalEvidence.provenance.sourceSHA, 'retry source must equal the pinned original build');
    assert.equal(process.env.LOCALIZATION_BUILD_DIR, undefined, 'retry must use retained original manifests, not rebuilt payloads');
    report.originalBuild = originalEvidence.provenance;
    report.retryRebuildPerformed = false;
    for (const file of ['web/package-lock.json', 'desktop/package-lock.json']) assert.equal(await sha256(path.join(root, file)),
      await sha256(path.join(productRoot, file)), 'harness dependency lock must remain identical to the original product lock');
  } else assert.equal(productRoot, root, 'ordinary new-build validation uses its own product checkout');
  const sourceSHA = await command('git', ['-C', productRoot, 'rev-parse', 'HEAD']);
  assert.equal(sourceSHA, report.sourceSHA, 'product source SHA must equal the immutable product checkout');
  for (const key of ['LOCALIZATION_INSTALLER', 'LOCALIZATION_INSTALL_DIR', ...(retryOriginal ? [] : ['LOCALIZATION_BUILD_DIR'])]) assert.ok(process.env[key] && path.isAbsolute(process.env[key]), `${key} must be absolute`);
  paths = { installer: process.env.LOCALIZATION_INSTALLER, install: process.env.LOCALIZATION_INSTALL_DIR,
    build: process.env.LOCALIZATION_BUILD_DIR, userData: path.join(process.env.APPDATA, PROFILE) };
  const relativeInstall = path.relative(process.env.RUNNER_TEMP, paths.install);
  assert.ok(relativeInstall && !relativeInstall.startsWith('..') && !path.isAbsolute(relativeInstall), 'install only beneath this runner temp directory');
  paths.exe = path.join(paths.install, 'OfferPilot Desktop.exe');
  paths.backend = path.join(paths.install, 'resources/backend/offerpilot-backend.exe');
  assert.equal(await exists(paths.userData), false, 'existing real profile blocks acceptance; never delete it');
  assert.equal(await exists(paths.install), false, 'install directory must be fresh');
  const webRequire = createRequire(path.join(root, 'web/package.json'));
  // require resolution selects Playwright's CJS entry; dynamic import of that
  // file exposes only default in current Playwright, not a named _electron.
  ({ _electron } = webRequire('playwright-core'));
  assert.equal(typeof _electron?.launch, 'function', 'locked frontend Playwright Electron support is required');
  report.playwrightVersion = webRequire('playwright-core/package.json').version;
  environment = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    /^(SystemRoot|WINDIR|SystemDrive|COMSPEC|PATH|PATHEXT|TEMP|TMP|APPDATA|LOCALAPPDATA|USERPROFILE|HOMEDRIVE|HOMEPATH|ProgramFiles|ProgramFiles\(x86\)|ProgramData|ALLUSERSPROFILE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE|OS|SESSIONNAME)$/i.test(key)));
  Object.assign(environment, { LITELLM_LOCAL_MODEL_COST_MAP: 'True', PYTHONUTF8: '1', PYTHONUNBUFFERED: '1',
    GITHUB_ACTIONS: 'true', RUNNER_TEMP: process.env.RUNNER_TEMP, LOCALIZATION_INSTALLER: paths.installer,
    LOCALIZATION_INSTALL_DIR: paths.install, LOCALIZATION_USER_DATA: paths.userData,
    LOCALIZATION_EXE: paths.exe, LOCALIZATION_BACKEND: paths.backend });
  report.installer = { filename: path.basename(paths.installer), sha256: await sha256(paths.installer) };
  if (originalEvidence) {
    assert.equal(report.installer.filename, originalEvidence.provenance.installer, 'retry must use the exact original installer filename');
    assert.equal(report.installer.sha256, originalEvidence.provenance.installerSha256, 'retry must use the exact original installer bytes');
  }
  report.installedExecutable = paths.exe;
  report.profilePolicy = 'real APPDATA/OfferPilot Desktop asserted absent; no profile deletion or user-data-dir override';
  await saveReport();
  stage = 'install-new-nsis';
  assert.equal((await windows('install')).exitCode, 0, 'original NSIS must install successfully on the fresh runner');
  installedIdentity = await verifyPayload();
  report.installedIdentityBefore = installedIdentity;
  sourceIdentity = await verifySource();
  await fs.writeFile(path.join(evidence, 'source-sha256.json'), JSON.stringify(sourceIdentity, null, 2) + '\n');
  await checkpoint(stage, { nsisActuallyInstalled: true, sameBuiltPayloadVerified: true,
    payloadIdentitySource: retryOriginal ? 'pinned-original-build-evidence' : 'same-job-unpacked-build' });

  await launch(1, 'zh-CN');
  await settings('zh-CN');
  const baseline = await businessSnapshot();
  report.initialBusinessState = baseline;
  await active.haru.locator('#desktop-haru-input').fill(DRAFT);
  await shot(active.page, '1-zh-CN-settings-initial');
  await shot(active.haru, '1-zh-CN-haru-original-draft');
  await mailPreview('zh-CN');
  await switchLocale('zh-CN', 'en-US');
  await mailPreview('en-US');
  await quitNormally();

  await launch(2, 'en-US');
  await settings('en-US');
  assert.deepEqual(await businessSnapshot(), baseline, 'restart must not create business or mail state');
  await shot(active.page, '2-en-US-settings-persisted-after-restart');
  await shot(active.haru, '2-en-US-haru-persisted-after-restart');
  await active.haru.locator('#desktop-haru-input').fill(DRAFT);
  await switchLocale('en-US', 'zh-CN');
  await quitNormally();

  await launch(3, 'zh-CN');
  await settings('zh-CN');
  assert.deepEqual(await businessSnapshot(), baseline);
  await shot(active.page, '3-zh-CN-settings-persisted-after-restart');
  await shot(active.haru, '3-zh-CN-haru-persisted-after-restart');
  await quitNormally();
  stage = 'immutable-installed-payload';
  report.installedIdentityAfter = await verifyPayload();
  assert.deepEqual(report.installedIdentityAfter, installedIdentity, 'installed product files must remain unmodified');
  assert.deepEqual(await verifySource(), sourceIdentity, 'source files and packed source mapping must remain unchanged');
  assert.equal(await sha256(paths.installer), report.installer.sha256);
  assert.ok(report.apiReads > 0, 'guard must observe actual backend GET traffic');
  await checkpoint(stage, { installerAndInstalledPayloadUnchanged: true });
  report.status = 'passed';
} catch (error) {
  report.status = 'failed';
  // Do not serialize Playwright errors: they may contain full request URLs or inspector endpoints.
  report.failure = { stage, kind: error instanceof assert.AssertionError ? 'assertion-failed' : 'operation-failed' };
  if (error instanceof assert.AssertionError && !error.generatedMessage) report.failure.assertion = error.message.slice(0, 250);
  else if (/^[a-z][a-z-]{1,100}$/.test(error?.message || '')) report.failure.code = error.message;
  if (['TimeoutError', 'TypeError', 'Error', 'AssertionError'].includes(error?.name)) report.failure.errorType = error.name;
  console.error(`Installed localization failed at: ${stage}`);
  process.exitCode = 1;
  // Fresh product-only surfaces contain no credentials or user account data.
  // Never capture a page that left the authenticated product origin.
  if (active && report.blockedRequests.length === 0) {
    for (const [role, page] of [['owner', active.page], ['haru', active.haru]]) {
      if (!page) continue;
      try {
        const url = new URL(page.url());
        if (url.origin === active.origin && url.pathname === '/' && url.search === (role === 'owner' ? '' : '?desktopSurface=haru')) {
          await shot(page, `failure-${report.failure.stage}-${role}`);
        }
      } catch { report.failure.screenshotIncomplete = true; }
    }
  }
} finally {
  if (active) {
    try { await quitNormally(); report.failureCleanup = 'explicit-menu-exit'; }
    catch {
      if (!owned.length && active.child) {
        try {
          const snapshot = await windows('snapshot');
          // Playwright uses shell:true on Windows: this handle may be cmd.exe.
          // Independently identify only the installed EXE in its live lineage.
          // Never kill the wrapper or infer that doing so stops Electron.
          owned = earlyOwnedProcesses(snapshot.processes, { handlePid: active.child.pid,
            handleRunning: active.child.exitCode === null && active.child.signalCode === null,
            startedAt: active.startedAt, observedAt: Date.now() }, paths.exe, paths.backend);
        } catch { report.failureCleanup = 'early-installed-process-lineage-unconfirmed'; }
      }
      if (owned.length) {
        try {
          const snapshot = await windows('snapshot');
          const main = owned[0];
          // Refresh only if the independently captured main identity still exists.
          // This includes a backend that became ready after an early UI failure.
          if (snapshot.processes.some(item => item.pid === main.pid && item.parentPid === main.parentPid && item.created === main.created && samePath(item.path, main.path))) {
            owned = ownedProcesses(snapshot.processes, main.pid, paths.exe, paths.backend);
          }
          report.failureCleanup = await windows('cleanup', { LOCALIZATION_OWNED_IDENTITIES: JSON.stringify(owned) });
          const end = Date.now() + 15000;
          while (Date.now() < end && (await windows('snapshot')).processes.length > 0) await delay(250);
          report.failureCleanup.allInstalledProcessesExited = (await windows('snapshot')).processes.length === 0;
        }
        catch { report.failureCleanup = 'owned-process-cleanup-unconfirmed'; }
      } else report.failureCleanup ??= 'no-independently-verified-process-identity';
    }
  }
  const passed = new Set(report.stages.map(item => item.name));
  report.requiredStageResults = report.requiredStages.map(name => ({ name,
    status: passed.has(name) ? 'passed' : name === report.failure?.stage ? 'failed' : 'not-run-or-incomplete' }));
  if (report.status === 'passed' && report.requiredStageResults.some(item => item.status !== 'passed')) {
    report.status = 'failed';
    report.failure = { stage: 'stage-accounting', kind: 'required-stage-incomplete' };
    process.exitCode = 1;
  }
  report.completedAt = new Date().toISOString();
  await saveReport();
}
