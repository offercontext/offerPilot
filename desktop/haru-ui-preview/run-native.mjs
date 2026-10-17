import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rename, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { observeLayout, assertLayout, settleVisual } from './visual.mjs';
const root = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(root, '../..');
const evidence = path.join(root, 'evidence');
const execFileAsync = promisify(execFile);
const report = { schemaVersion: 1, scope: 'WINDOWS_SOURCE_ELECTRON_REAL_APPSHELL_SYNTHETIC_READ_ONLY_API', passed: false,
  productionModules: ['desktop/haru.cjs', 'desktop/preload.cjs', 'desktop/haru-protocol.cjs', 'desktop/capabilities.cjs', 'complete web/dist AppShell and DesktopHaruWindow'],
  fixtureModules: ['desktop/haru-ui-preview/native-main.cjs', 'desktop/haru-ui-preview/fixture-api.cjs'],
  productionMainStartupTested: false, installedExeTested: false, backendTested: false, backendTokenInjectionTested: false, realAiTested: false,
  nativeWindowBehaviorTested: false, requestedWebPreferencesSandbox: true, bypassCSP: false,
  osAlphaCompositionRequiresHumanReview: true, nativeOsCompositionCaptured: false,
  conversationData: 'Local synthetic read-only fixture; UI-only has_api_key flag enables unsent drafts, with no key/provider and all writes denied',
  workflowCommit: process.env.GITHUB_SHA || null, hostPlatform: process.platform, checks: [], screenshots: [], pageErrors: [], consoleErrors: [],
  completedStage: 'starting', failureStage: null };
let app, owner, haru, timer, stage = 'initialization', closing, expired = false, emergencyExit;
let outputQueue = Promise.resolve();
function output() {
  const contents = JSON.stringify(report, null, 2) + '\n';
  outputQueue = outputQueue.then(async () => {
    const temporary = path.join(evidence, 'native-result.json.tmp');
    await writeFile(temporary, contents); await rename(temporary, path.join(evidence, 'native-result.json'));
  });
  return outputQueue;
}
async function cleanup() {
  if (!closing) closing = (async () => {
    if (app) {
      let closeTimer;
      try { await Promise.race([app.close(), new Promise((_, reject) => { closeTimer = setTimeout(() => reject(new Error('Owned Electron close timed out')), 10000); })]); }
      catch { report.passed = false; report.cleanupFailed = true; app.process().kill(); process.exitCode = 1; }
      finally { clearTimeout(closeTimer); }
    }
    if (expired) report.passed = false;
  })();
  return closing;
}
const poll = async (fn, timeout = 10000) => { const until = Date.now() + timeout; while (Date.now() < until) { if (await fn()) return; await new Promise(resolve => setTimeout(resolve, 100)); } throw new Error(`Condition timeout at ${stage}`); };
async function windowState(page) {
  const win = await app.browserWindow(page);
  try { return await win.evaluate(win => ({ visible: win.isVisible(), minimized: win.isMinimized(), focused: win.isFocused(), bounds: win.getBounds(), backgroundColor: win.getBackgroundColor(),
    preferences: { sandbox: win.webContents.getLastWebPreferences().sandbox, contextIsolation: win.webContents.getLastWebPreferences().contextIsolation,
      nodeIntegration: win.webContents.getLastWebPreferences().nodeIntegration, webSecurity: win.webContents.getLastWebPreferences().webSecurity } })); }
  finally { await win.dispose(); }
}
async function windowAction(page, action) {
  const win = await app.browserWindow(page);
  try { await win.evaluate((win, action) => { if (action === 'minimize') win.minimize(); else if (action === 'close') win.close(); else if (action === 'focus') { win.show(); win.focus(); } }, action); }
  finally { await win.dispose(); }
}
async function capture(name, page = haru, expanded = true, theme) {
  stage = name;
  let visual;
  if (page === haru) { await windowAction(haru, 'focus'); await settleVisual(page, expanded); visual = await page.evaluate(observeLayout); }
  await page.screenshot({ path: path.join(evidence, `${name}.png`), omitBackground: page === haru && !expanded, timeout: 10000 });
  const state = await windowState(page);
  report.screenshots.push({ file: `${name}.png`, omitBackground: page === haru && !expanded, ...visual, nativeWindow: state });
  if (visual) assertLayout(visual, { expanded, theme });
  assert.equal(expired, false);
  await output();
}
async function check(name, run) { stage = name; await run(); report.checks.push(name); report.completedStage = name; await output(); }
const bridgeState = () => haru.evaluate(() => window.offerpilotDesktop.getState());
const draft = '【原生验收草稿】恢复窗口和导航后保留';
const haruDraft = '【原生Haru草稿】保留';
const historyText = '【本地合成数据】窗口恢复应保留这条会话。';
async function assertHaruHistory() {
  await haru.locator('.desktop-haru-messages').getByText(historyText, { exact: true }).waitFor({ state: 'visible' });
  const state = await bridgeState();
  assert.equal(state.snapshot?.conversationId, 101);
  assert.ok(state.snapshot.messages.some(message => message.role === 'assistant' && message.content === historyText));
}
async function assertRetainedHistory(label, checkDrafts = true) {
  await assertHaruHistory();
  await owner.locator('.op-pilot-page-host').getByText(historyText, { exact: true }).waitFor({ state: 'visible' });
  if (checkDrafts) {
    assert.equal(await owner.locator('.op-pilot-page-host textarea:visible').inputValue(), draft);
    assert.equal(await haru.getByLabel('给 Haru 发消息', { exact: true }).inputValue(), haruDraft);
  }
  report.historyChecks ??= [];
  report.historyChecks.push({ label, conversationId: 101, ownerHistoryVisible: true, haruHistoryVisible: true,
    historyText, bothDraftsVerified: checkDrafts });
}
await mkdir(evidence, { recursive: true }); await output();
try {
  assert.equal(process.platform, 'win32', 'Native evidence requires actual Windows');
  if (process.env.CI === 'true') assert.match(report.workflowCommit || '', /^[a-f0-9]{40}$/i);
  const sourceFiles = ['desktop/haru.cjs', 'desktop/haru-protocol.cjs', 'desktop/preload.cjs', 'desktop/capabilities.cjs', 'desktop/package-lock.json',
    'web/src/layout/AppShell.tsx', 'web/src/layout/viewRoute.ts', 'web/src/features/assistantSurface/AssistantSurfaceProvider.tsx',
    'web/src/features/assistantSurface/useDesktopHaruOwner.ts', 'web/src/features/assistantSurface/DesktopHaruWindow.tsx',
    'web/src/features/assistantSurface/DesktopHaruWindow.css', 'web/src/features/assistantSurface/desktopHaru.ts',
    'web/src/features/assistantSurface/assets/haru-window-light.png', 'web/src/features/assistantSurface/assets/haru-window-dark.png',
    'web/dist/index.html', 'desktop/haru-ui-preview/native-drag.ps1', 'desktop/haru-ui-preview/native-main.cjs', 'desktop/haru-ui-preview/fixture-api.cjs', 'desktop/haru-ui-preview/run-native.mjs'];
  report.sourceFiles = await Promise.all(sourceFiles.map(async file => ({ file, sha256: createHash('sha256').update(await readFile(path.join(repo, file))).digest('hex') })));
  const requireInstalled = createRequire(path.resolve(root, '../installed-ui/package.json'));
  const requireDesktop = createRequire(path.resolve(root, '../package.json'));
  const { _electron } = requireInstalled('playwright-core');
  const profile = await mkdtemp(path.join(os.tmpdir(), 'offerpilot-haru-native-'));
  stage = 'launch-real-electron-fixture-main';
  app = await _electron.launch({ executablePath: requireDesktop('electron'), args: [path.join(root, 'native-main.cjs')], cwd: repo,
    env: { ...process.env, HARU_NATIVE_PROFILE: profile }, bypassCSP: false, timeout: 30000 });
  timer = setTimeout(() => {
    expired = true; report.passed = false; report.failureStage = stage; report.error = 'Five-minute native runtime deadline expired'; process.exitCode = 1;
    // Closing the owned app interrupts page waits. The main catch/finally owns
    // the final flush. A stuck host wait gets a bounded atomic report then exit.
    void cleanup().finally(() => {
      emergencyExit = setTimeout(() => { void output().finally(() => process.exit(1)); }, 2000);
    });
  }, 5 * 60000);
  const observedPages = new Set();
  const observePage = page => {
    if (observedPages.has(page)) return; observedPages.add(page);
    page.on('pageerror', error => report.pageErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text().slice(0, 500)); });
  };
  app.on('window', observePage);
  for (const page of app.windows()) observePage(page);
  await poll(async () => {
    for (const page of app.windows()) {
      const role = await page.evaluate(() => window.offerpilotDesktop?.role).catch(() => null);
      if (role === 'owner') owner = page; if (role === 'haru') haru = page;
    }
    return owner && haru;
  }, 30000);
  for (const page of [owner, haru]) page.setDefaultTimeout(15000);
  report.runtime = await app.evaluate(({ app }) => ({ electron: process.versions.electron, chrome: process.versions.chrome, platform: process.platform,
    packaged: app.isPackaged, sandboxDisabled: app.commandLine.hasSwitch('no-sandbox') || app.commandLine.hasSwitch('disable-sandbox') }));
  assert.equal(report.runtime.sandboxDisabled, false); assert.equal(report.runtime.packaged, false);
  await owner.getByRole('navigation', { name: '主导航', exact: true }).waitFor();
  await poll(async () => (await bridgeState())?.connected);
  await check('production-preload-and-window-security', async () => {
    for (const page of [owner, haru]) assert.deepEqual((await windowState(page)).preferences,
      { sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true });
    const constructed = await app.evaluate(() => globalThis.haruNativeAudit.constructedWindows);
    assert.equal(constructed.length, 1); assert.equal(constructed[0].transparent, true); assert.equal(constructed[0].frame, false);
    report.haruConstructedOptions = constructed[0];
    // Electron 44 returns RGB only: this getter deliberately omits alpha.
    // https://www.electronjs.org/docs/latest/api/browser-window#wingetbackgroundcolor
    const backgroundColorRgb = (await windowState(haru)).backgroundColor;
    assert.equal(backgroundColorRgb, '#000000');
    report.backgroundColorObservation = { rgb: backgroundColorRgb, alphaReturnedByGetter: false, provesOsAlphaComposition: false };
    const roles = await Promise.all([owner, haru].map(page => page.evaluate(() => ({ role: window.offerpilotDesktop.role,
      require: typeof window.require, genericInvoke: typeof window.offerpilotDesktop.invoke, publish: typeof window.offerpilotDesktop.publish, request: typeof window.offerpilotDesktop.request }))));
    assert.deepEqual(roles, [{ role: 'owner', require: 'undefined', genericInvoke: 'undefined', publish: 'function', request: 'undefined' },
      { role: 'haru', require: 'undefined', genericInvoke: 'undefined', publish: 'undefined', request: 'function' }]);
  });
  await capture('native-collapsed-only-live2d', haru, false);
  await check('os-cursor-drag-does-not-expand', async () => {
    await windowAction(haru, 'focus');
    const before = (await windowState(haru)).bounds;
    const box = await haru.locator('.desktop-haru-avatar').boundingBox();
    const viewport = await haru.evaluate(() => ({ width: innerWidth, height: innerHeight }));
    const identity = await app.evaluate(({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows().find(win => win.webContents.getURL().includes('desktopSurface=haru'));
      const handle = win.getNativeWindowHandle();
      return { pid: process.pid, handle: (handle.length === 8 ? handle.readBigUInt64LE() : BigInt(handle.readUInt32LE())).toString() };
    });
    await execFileAsync('powershell.exe', ['-NoProfile', '-File', path.join(root, 'native-drag.ps1'), '-WindowHandle', identity.handle,
      '-OwnerProcessId', String(identity.pid), '-XRatio', String((box.x + box.width / 2) / viewport.width), '-YRatio', String((box.y + box.height / 2) / viewport.height)], { timeout: 15000 });
    await poll(async () => { const after = (await windowState(haru)).bounds; return Math.abs(after.x - before.x) + Math.abs(after.y - before.y) >= 10; });
    assert.equal((await bridgeState()).expanded, false);
    report.nativeDrag = { before, after: (await windowState(haru)).bounds };
    await capture('native-after-os-drag', haru, false);
  });
  await haru.getByRole('button', { name: '展开 Haru 对话', exact: true }).click();
  await capture('native-expanded-static');
  await check('actual-appshell-pilot-conversation-and-draft', async () => {
    await haru.getByRole('button', { name: '打开 OfferPilot 主窗口', exact: true }).click();
    await owner.waitForURL(url => url.searchParams.get('view') === 'pilot');
    await owner.locator('.op-pilot-page-host').waitFor({ state: 'visible' });
    await owner.getByRole('button', { name: '打开对话：原生窗口合成会话', exact: true })
      .getByText('原生窗口合成会话', { exact: true }).click();
    await poll(async () => (await bridgeState()).snapshot?.conversationId === 101);
    await owner.locator('.op-pilot-page-host textarea:visible').fill(draft);
    await assertRetainedHistory('selected-conversation', false);
    await capture('native-owner-pilot-selected', owner);
  });
  await check('real-theme-toggle-and-repeat-retain-drafts', async () => {
    await haru.getByLabel('给 Haru 发消息', { exact: true }).fill(haruDraft);
    await assertRetainedHistory('both-drafts-seeded');
    for (const theme of ['dark', 'light']) {
      // Immersive Pilot deliberately has no Sidebar. Use its real exit control
      // before changing the theme, then return through Haru's native handoff.
      await owner.getByRole('button', { name: '退出沉浸模式，返回原页面', exact: true }).click();
      await owner.waitForURL(url => url.searchParams.get('view') === 'dashboard');
      await owner.getByRole('navigation', { name: '主导航', exact: true }).waitFor({ state: 'visible' });
      if (await owner.locator('html').getAttribute('data-theme') !== theme) await owner.getByRole('button', { name: '切换明暗模式', exact: true }).click();
      await haru.waitForFunction(value => document.querySelector('.desktop-haru')?.getAttribute('data-theme') === value, theme);
      await assertHaruHistory();
      await capture(`native-expanded-${theme}`, haru, true, theme);
      await haru.getByRole('button', { name: '收起 Haru 对话', exact: true }).click();
      await capture(`native-collapsed-${theme}`, haru, false, theme);
      await haru.getByRole('button', { name: '展开 Haru 对话', exact: true }).click();
      await settleVisual(haru, true);
      await assertHaruHistory();
      assert.equal(await haru.getByLabel('给 Haru 发消息', { exact: true }).inputValue(), haruDraft);
      await haru.getByRole('button', { name: '打开 OfferPilot 主窗口', exact: true }).click();
      await owner.waitForURL(url => url.searchParams.get('view') === 'pilot');
      await owner.locator('.op-pilot-page-host').waitFor({ state: 'visible' });
      assert.equal((await bridgeState()).snapshot.conversationId, 101);
      assert.equal(await owner.locator('.op-pilot-page-host textarea:visible').inputValue(), draft);
      await assertRetainedHistory(`theme-${theme}-collapse-reopen`);
    }
  });
  for (const mode of ['minimized', 'hidden-after-close', 'background']) await check(`restore-${mode}-to-pilot-with-draft`, async () => {
    await owner.getByRole('button', { name: '退出沉浸模式，返回原页面', exact: true }).click();
    await owner.waitForURL(url => url.searchParams.get('view') === 'dashboard');
    if (mode === 'minimized') { await windowAction(owner, 'minimize'); await poll(async () => (await windowState(owner)).minimized); }
    else if (mode === 'hidden-after-close') { await windowAction(owner, 'close'); await poll(async () => !(await windowState(owner)).visible); }
    else { await windowAction(haru, 'focus'); await poll(async () => !(await windowState(owner)).focused); }
    const before = await windowState(owner); const generation = (await bridgeState()).generation;
    await haru.getByRole('button', { name: '打开 OfferPilot 主窗口', exact: true }).click();
    await owner.waitForURL(url => url.searchParams.get('view') === 'pilot');
    await owner.locator('.op-pilot-page-host').waitFor({ state: 'visible' });
    await poll(async () => { const state = await windowState(owner); return state.visible && !state.minimized && state.focused; });
    assert.equal((await bridgeState()).generation, generation);
    assert.equal((await bridgeState()).snapshot.conversationId, 101);
    assert.equal(await owner.locator('.op-pilot-page-host textarea:visible').inputValue(), draft);
    await assertRetainedHistory(`restore-${mode}`);
    report.checks.push({ mode, before, after: await windowState(owner), conversationId: 101, draftPreserved: true });
    await capture(`native-restored-${mode}`, owner);
  });
  await check('repeated-main-handoff-does-not-clear-conversation', async () => {
    for (let n = 0; n < 3; n++) await haru.getByRole('button', { name: '打开 OfferPilot 主窗口', exact: true }).click();
    assert.equal(new URL(owner.url()).searchParams.get('view'), 'pilot');
    assert.equal((await bridgeState()).snapshot.conversationId, 101);
    assert.equal(await owner.locator('.op-pilot-page-host textarea:visible').inputValue(), draft);
    await assertRetainedHistory('repeated-main-handoff');
  });
  await haru.getByRole('button', { name: '收起 Haru 对话', exact: true }).click();
  await settleVisual(haru, false);
  await check('native-os-composition-over-owned-appshell', async () => {
    const ownerWindow = await app.browserWindow(owner);
    const previous = await ownerWindow.evaluate(win => ({ bounds: win.getBounds(), maximized: win.isMaximized() }));
    try {
      await ownerWindow.evaluate(win => { win.show(); win.maximize(); });
      await poll(() => ownerWindow.evaluate(win => win.isMaximized()));
      await windowAction(haru, 'focus'); await settleVisual(haru, false);
      const identity = await app.evaluate(({ BrowserWindow }) => {
        const windows = BrowserWindow.getAllWindows();
        const mirror = windows.find(win => win.webContents.getURL().includes('desktopSurface=haru'));
        const owner = windows.find(win => win !== mirror);
        const nativeHandle = win => { const buffer = win.getNativeWindowHandle(); return (buffer.length === 8 ? buffer.readBigUInt64LE() : BigInt(buffer.readUInt32LE())).toString(); };
        return { pid: process.pid, haruHandle: nativeHandle(mirror), ownerHandle: nativeHandle(owner) };
      });
      const { stdout } = await execFileAsync('powershell.exe', ['-NoProfile', '-File', path.join(root, 'native-drag.ps1'),
        '-Mode', 'Capture', '-WindowHandle', identity.haruHandle, '-OwnerWindowHandle', identity.ownerHandle,
        '-OwnerProcessId', String(identity.pid)], { timeout: 15000 });
      const capture = JSON.parse(stdout.trim());
      assert.equal(capture.nativeScreenCapture, true); assert.equal(capture.imageEdited, false); assert.equal(capture.ownWindowSamples, 25); assert.ok(capture.haruWindowSamples > 0);
      const bytes = await readFile(path.join(evidence, capture.file));
      assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
      report.nativeOsCompositionCaptured = true;
      report.osComposition = { ...capture, sha256: createHash('sha256').update(bytes).digest('hex'),
        scope: 'Unedited Windows compositor capture limited to Haru bounds over the real owned AppShell; visual alpha review still required' };
    } finally {
      await ownerWindow.evaluate((win, previous) => { if (!previous.maximized) { win.unmaximize(); win.setBounds(previous.bounds); } }, previous);
      await poll(() => ownerWindow.evaluate((win, previous) => {
        if (win.isMaximized() !== previous.maximized) return false;
        const bounds = win.getBounds(); return Object.keys(previous.bounds).every(key => Math.abs(bounds[key] - previous.bounds[key]) <= 1);
      }, previous));
      await ownerWindow.dispose(); await windowAction(haru, 'focus');
    }
  });
  report.audit = await app.evaluate(() => globalThis.haruNativeAudit);
  assert.deepEqual(report.audit.denied, [], 'Unknown fixture API or disallowed network request');
  assert.deepEqual(report.pageErrors, [], 'Unhandled renderer errors');
  assert.deepEqual(report.consoleErrors, [], 'Renderer console errors');
  assert.equal(expired, false, 'Native deadline expired');
  report.nativeWindowBehaviorTested = true; report.passed = true; report.completedStage = 'complete';
} catch (error) {
  report.failureStage = stage; report.error = String(error); process.exitCode = 1;
  if (app) report.audit = await app.evaluate(() => globalThis.haruNativeAudit).catch(() => null);
  for (const [label, page] of [['owner', owner], ['haru', haru]]) if (page) await page.screenshot({ path: path.join(evidence, `native-failure-${label}.png`), omitBackground: label === 'haru', timeout: 5000 }).catch(() => {});
  console.error(`Native Haru validation failed at ${stage}:`, error);
} finally {
  await cleanup();
  if (expired || report.cleanupFailed) report.passed = false;
  await output(); clearTimeout(timer); clearTimeout(emergencyExit);
  if (expired || report.cleanupFailed) process.exit(1);
}
