import assert from 'node:assert/strict';
import { mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { observeLayout, assertLayout, settleVisual } from './visual.mjs';

const root = path.dirname(fileURLToPath(import.meta.url));
const evidence = path.join(root, 'evidence');
const origin = 'http://127.0.0.1:4177';
const report = {
  schemaVersion: 1, passed: false, scope: 'WINDOWS_BROWSER_UI_PREVIEW_SYNTHETIC_BRIDGE_ONLY',
  conversationData: 'Local synthetic fixtures; no model or backend calls',
  runtime: 'Unmodified product DesktopHaruWindow, Live2D runtime and repository model assets',
  installedExeTested: false, nativeWindowBehaviorTested: false, realAiTested: false,
  hostPlatform: process.platform, browserChannel: 'msedge', chromiumSandbox: true,
  workflowCommit: /^[a-f0-9]{40}$/i.test(process.env.GITHUB_SHA || '') ? process.env.GITHUB_SHA.toLowerCase() : null,
  screenshots: [], checks: [], consoleErrors: 0, pageErrors: 0, blockedRequests: 0, blockedWebSockets: 0,
  completedStage: 'starting', failureStage: null,
};
let browser;
let server;
let page;
let stage = 'initialization';
let deadline;
let cleanupPromise;
let finalizePromise;
let expired = false;
let outputQueue = Promise.resolve();
function output() {
  const contents = JSON.stringify(report, null, 2) + '\n';
  outputQueue = outputQueue.then(async () => {
    const temporary = path.join(evidence, 'result.json.tmp');
    await writeFile(temporary, contents);
    await rename(temporary, path.join(evidence, 'result.json'));
  });
  return outputQueue;
}

const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
async function sourceIdentity() {
  const sources = [
    'web/src/features/assistantSurface/DesktopHaruWindow.tsx',
    'web/src/features/assistantSurface/DesktopHaruWindow.css',
    'web/src/features/pilotMascot/live2dRuntime.ts',
    'web/public/live2d/live2dcubismcore.min.js',
    'web/package-lock.json',
    'desktop/installed-ui/package-lock.json',
    'web/src/features/assistantSurface/assets/haru-window-light.png',
    'web/src/features/assistantSurface/assets/haru-window-dark.png',
  ];
  const modelRoot = 'web/public/live2d/haru-receptionist';
  async function collect(directory) {
    for (const entry of await readdir(path.resolve(root, '../..', directory), { withFileTypes: true })) {
      const relative = `${directory}/${entry.name}`;
      if (entry.isDirectory()) await collect(relative);
      else if (entry.isFile()) sources.push(relative);
      else throw new Error('Preview model assets must be ordinary repository files');
    }
  }
  await collect(modelRoot);
  return Promise.all(sources.sort().map(async file => {
    const bytes = await readFile(path.resolve(root, '../..', file));
    const value = { file, sha256Raw: sha256(bytes) };
    if (/\.(tsx?|css|js|json)$/.test(file)) value.sha256CanonicalLf = sha256(bytes.toString('utf8').replace(/\r\n/g, '\n'));
    return value;
  }));
}
async function boundedClose(operation) {
  let timer;
  try {
    await Promise.race([operation(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Owned preview cleanup timed out')), 5000); })]);
  } finally { clearTimeout(timer); }
}
function cleanupOwned() {
  if (!cleanupPromise) cleanupPromise = (async () => {
    try { if (browser) await boundedClose(() => browser.close()); }
    catch { report.passed = false; report.cleanupFailed = true; process.exitCode = 1; }
    try {
      if (server) await boundedClose(() => new Promise((resolve, reject) => {
        server.httpServer.close(error => error ? reject(error) : resolve());
        server.httpServer.closeIdleConnections?.();
      }));
    } catch {
      // Only the HTTP server created below, never another process or port.
      server?.httpServer.closeAllConnections?.();
      report.passed = false; report.cleanupFailed = true; process.exitCode = 1;
    }
  })();
  return cleanupPromise;
}
function finalize() {
  // The deadline and normal finally share one writer/terminator. Neither can
  // truncate the other's final report or skip owned-resource cleanup.
  if (!finalizePromise) finalizePromise = (async () => {
    await cleanupOwned();
    if (expired) report.passed = false;
    if (report.cleanupFailed) report.failureStage ||= 'owned-cleanup';
    try { await output(); }
    finally {
      clearTimeout(deadline);
      if (expired || report.cleanupFailed) process.exit(1);
    }
  })();
  return finalizePromise;
}

async function checkpoint(name, { expanded = true, theme } = {}) {
  stage = name;
  await settleVisual(page, expanded);
  const value = await page.evaluate(observeLayout);
  const filename = `${name}.png`;
  await page.screenshot({ path: path.join(evidence, filename), fullPage: false, omitBackground: !expanded, timeout: 10_000 });
  assert.equal(expired, false, 'Browser preview deadline expired');
  report.screenshots.push({ file: filename, omitBackground: !expanded, ...value });
  assertLayout(value, { expanded, theme });
  report.completedStage = name;
  await output();
  return value;
}

async function scenario(name) {
  await page.evaluate(value => window.haruUiPreview.setScenario(value), name);
  await page.waitForFunction(value => {
    const expanded = value !== 'collapsed';
    const running = value === 'running' || value === 'offline-running';
    const canSend = !['running', 'pending', 'offline', 'offline-running'].includes(value);
    const root = document.querySelector('.desktop-haru');
    const input = document.querySelector('#desktop-haru-input');
    return root?.classList.contains('expanded') === expanded
      && (!expanded || input?.disabled === !canSend)
      && Boolean(document.querySelector('.desktop-haru-pending')) === (value === 'pending')
      && Boolean(document.querySelector('.desktop-haru-stop')) === (expanded && running);
  }, name, { timeout: 5000 });
}
async function observeBridge() { return page.evaluate(() => window.haruUiPreview.observe()); }
async function waitBridge(predicate) { await page.waitForFunction(predicate, undefined, { timeout: 5000 }); }
async function check(name, operation) {
  stage = name;
  await operation();
  report.checks.push(name);
  report.completedStage = name;
}

await mkdir(evidence, { recursive: true });
await output();
try {
  assert.equal(process.platform, 'win32', 'This runner requires Windows and preinstalled Microsoft Edge');
  if (process.env.CI === 'true' || process.env.GITHUB_ACTIONS === 'true') assert.ok(report.workflowCommit, 'CI requires an exact 40-hex workflow source commit');
  report.sourceFiles = await sourceIdentity();
  const { build, preview } = await import('../../web/node_modules/vite/dist/node/index.js');
  const requireInstalled = createRequire(path.resolve(root, '../installed-ui/package.json'));
  const { chromium } = requireInstalled('playwright-core');
  stage = 'build-isolated-fixture';
  const configFile = path.join(root, 'vite.config.mjs');
  await build({ configFile });
  stage = 'start-loopback-preview';
  server = await preview({ configFile });
  stage = 'launch-preinstalled-edge';
  browser = await chromium.launch({ channel: 'msedge', headless: true, chromiumSandbox: true, timeout: 30_000 });
  // The workflow bounds setup/build separately. Start this six-minute UI
  // deadline only after acquiring the browser so timeout cleanup always owns
  // both resources. No retry, WebGL downgrade or sandbox bypass is allowed.
  deadline = setTimeout(() => {
    expired = true;
    process.exitCode = 1;
    report.passed = false;
    report.failureStage = stage;
    void finalize();
  }, 6 * 60_000);
  report.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 420, height: 740 }, deviceScaleFactor: 1, locale: 'zh-CN', serviceWorkers: 'block' });
  // Default deny. Even other loopback ports and same-origin /api are blocked.
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    const pathAllowed = url.pathname === '/' || url.pathname === '/index.html' || url.pathname.startsWith('/assets/') || url.pathname.startsWith('/live2d/');
    if (url.origin === origin && pathAllowed && ['GET', 'HEAD'].includes(route.request().method())) return route.continue();
    report.blockedRequests++;
    return route.abort('blockedbyclient');
  });
  await context.routeWebSocket('**/*', socket => { report.blockedWebSockets++; socket.close(); });
  page = await context.newPage();
  page.setDefaultTimeout(10_000);
  page.on('console', message => { if (message.type() === 'error') report.consoleErrors++; });
  page.on('pageerror', () => report.pageErrors++);
  await page.goto(`${origin}/?surface=1`, { waitUntil: 'networkidle', timeout: 30_000 });
  await checkpoint('expanded-420x740');

  await check('context-open-main-and-escape', async () => {
    const toggle = page.locator('.desktop-haru-context-toggle');
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
    const before = (await observeBridge()).mainWindowRequests;
    await page.getByRole('button', { name: '在主窗查看', exact: true }).click();
    await waitBridge(() => window.haruUiPreview.observe().mainWindowRequests > 0);
    assert.equal((await observeBridge()).mainWindowRequests, before + 1);
    await checkpoint('context-expanded');
    await page.keyboard.press('Escape');
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(await toggle.evaluate(node => node === document.activeElement), true);
    assert.equal((await observeBridge()).expanded, true);
  });

  await check('send-and-stop-synthetic-generation', async () => {
    await page.getByLabel('给 Haru 发消息', { exact: true }).fill('【预览测试】一起梳理这道面试题。');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await waitBridge(() => window.haruUiPreview.observe().requests.includes('send'));
    await page.waitForFunction(() => document.querySelector('#desktop-haru-input')?.value === '' && document.querySelector('#desktop-haru-input')?.disabled);
    assert.equal(await page.getByLabel('给 Haru 发消息', { exact: true }).inputValue(), '');
    assert.equal(await page.getByLabel('给 Haru 发消息', { exact: true }).isDisabled(), true);
    await checkpoint('synthetic-generating');
    await page.getByRole('button', { name: '停止', exact: true }).click();
    await waitBridge(() => window.haruUiPreview.observe().requests.includes('stop'));
    await page.getByText('已停止本次生成。', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('给 Haru 发消息', { exact: true }).isEnabled(), true);
    await checkpoint('synthetic-stopped');
  });

  await check('pending-handoff-without-approval', async () => {
    await scenario('pending');
    const before = (await observeBridge()).mainWindowRequests;
    assert.equal(await page.getByLabel('给 Haru 发消息', { exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: '到 Pilot 查看并确认', exact: true }).click();
    await waitBridge(() => window.haruUiPreview.observe().requests.includes('open-pending'));
    assert.equal((await observeBridge()).mainWindowRequests, before + 1);
    // The pending panel remains; the preview bridge cannot approve any write.
    assert.equal(await page.getByRole('button', { name: '到 Pilot 查看并确认', exact: true }).isVisible(), true);
    await checkpoint('synthetic-pending');
  });

  await check('offline-send-and-stop-disabled', async () => {
    await scenario('offline');
    assert.equal(await page.getByLabel('给 Haru 发消息', { exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: '发送', exact: true }).isDisabled(), true);
    await checkpoint('synthetic-disconnected');
    await scenario('offline-running');
    assert.equal(await page.getByRole('button', { name: '停止', exact: true }).isDisabled(), true);
    await checkpoint('synthetic-disconnected-running');
  });

  await check('collapse-expand-switches-static-and-live2d', async () => {
    await scenario('idle');
    await page.getByRole('button', { name: '收起 Haru 对话', exact: true }).click();
    await waitBridge(() => !window.haruUiPreview.observe().expanded);
    await page.setViewportSize({ width: 260, height: 340 });
    await checkpoint('collapsed-260x340', { expanded: false });
    await page.getByRole('button', { name: '展开 Haru 对话', exact: true }).click();
    await waitBridge(() => window.haruUiPreview.observe().expanded);
    await page.setViewportSize({ width: 420, height: 740 });
    await checkpoint('expanded-after-collapse');
    assert.equal(await page.getByLabel('给 Haru 发消息', { exact: true }).evaluate(node => node === document.activeElement), true);
    // Escape closes the conversation and keeps the character visible.
    // Explicit hiding disposes its model; resuming mounts it again.
    await page.keyboard.press('Escape');
    await waitBridge(() => !window.haruUiPreview.observe().expanded);
    await page.setViewportSize({ width: 260, height: 340 });
    await page.getByRole('button', { name: '展开 Haru 对话', exact: true }).focus();
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => window.haruUiPreview.observe().visible), true);
    await page.evaluate(() => window.offerpilotDesktop.windowAction('hide-haru'));
    await waitBridge(() => !window.haruUiPreview.observe().visible);
    await page.waitForFunction(() => !document.querySelector('.desktop-haru-portrait canvas'));
    assert.equal(await page.locator('.desktop-haru-portrait canvas').count(), 0);
    await scenario('collapsed');
    await checkpoint('collapsed-after-remount', { expanded: false });
    await page.getByRole('button', { name: '展开 Haru 对话', exact: true }).click();
    await waitBridge(() => window.haruUiPreview.observe().expanded);
    await page.setViewportSize({ width: 420, height: 740 });
    await checkpoint('expanded-after-remount');
  });

  await check('short-viewport-and-long-message-overflow', async () => {
    await scenario('idle');
    await page.setViewportSize({ width: 420, height: 620 });
    await checkpoint('short-420x620');
    await scenario('long');
    const value = await checkpoint('short-long-message');
    assert.equal(value.messagesScroll, true);
    await page.locator('.desktop-haru-context-toggle').click();
    await page.getByRole('button', { name: '在主窗查看', exact: true }).click();
    await checkpoint('short-context-expanded');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.desktop-haru-context-toggle').getAttribute('aria-expanded'), 'false');
  });

  await check('reduced-motion-static-runtime', async () => {
    await page.setViewportSize({ width: 260, height: 340 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    // Full same-origin navigation creates a fresh runtime with reduced motion.
    // Collapsed idle retains the real model under this accessibility preference.
    // This does not claim running/minimal animation-path coverage.
    await page.goto(`${origin}/?surface=1&scenario=collapsed`, { waitUntil: 'networkidle', timeout: 30_000 });
    assert.equal(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), true);
    assert.equal((await observeBridge()).scenario, 'collapsed');
    await checkpoint('reduced-motion-collapsed', { expanded: false });
  });

  await check('avatar-hover-preserves-layout-in-both-themes', async () => {
    await scenario('idle');
    report.hoverMeasurements = [];
    const geometry = () => Object.fromEntries(['.desktop-haru-avatar', '.desktop-haru-context', '.desktop-haru-chat'].map(selector => {
      const box = document.querySelector(selector).getBoundingClientRect();
      return [selector, { x: box.x, y: box.y, width: box.width, height: box.height }];
    }));
    const unchanged = (before, after, label) => {
      for (const selector of Object.keys(before)) for (const key of ['x', 'y', 'width', 'height']) {
        assert.ok(Math.abs(before[selector][key] - after[selector][key]) <= 0.5, `${label}: ${selector} ${key} moved`);
      }
    };
    for (const height of [740, 620]) for (const theme of ['light', 'dark']) {
      await page.setViewportSize({ width: 420, height });
      await page.evaluate(value => window.haruUiPreview.setTheme(value), theme);
      await page.mouse.move(1, 1); await settleVisual(page, true);
      const before = await page.evaluate(geometry);
      await page.locator('.desktop-haru-avatar').hover(); await settleVisual(page, true);
      const hovered = await page.evaluate(geometry);
      unchanged(before, hovered, `hover ${theme} ${height}`);
      await checkpoint(`hover-${theme}-420x${height}`, { theme });
      await page.mouse.move(1, 1); await settleVisual(page, true);
      const after = await page.evaluate(geometry);
      unchanged(before, after, `leave ${theme} ${height}`);
      report.hoverMeasurements.push({ theme, width: 420, height, before, hovered, after });
    }
  });

  await check('theme-repeated-toggle-and-draft', async () => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await scenario('idle');
    await page.setViewportSize({ width: 420, height: 740 });
    const draft = '【预览草稿】反复展开和切换主题后仍保留';
    await page.getByLabel('给 Haru 发消息', { exact: true }).fill(draft);
    for (const theme of ['light', 'dark', 'light']) {
      await page.evaluate(value => window.haruUiPreview.setTheme(value), theme);
      await checkpoint(`theme-${theme}-${report.screenshots.length}`, { theme });
      await page.getByRole('button', { name: '收起 Haru 对话', exact: true }).click();
      await waitBridge(() => !window.haruUiPreview.observe().expanded);
      await page.setViewportSize({ width: 260, height: 340 });
      await checkpoint(`only-live2d-${theme}-${report.screenshots.length}`, { expanded: false, theme });
      // Browser pointer events prove click suppression only; native Windows
      // cursor/window displacement is independently required by run-native.mjs.
      const avatar = page.locator('.desktop-haru-avatar'); const box = await avatar.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 30, box.y + box.height / 2 + 10, { steps: 5 }); await page.mouse.up();
      assert.equal((await observeBridge()).expanded, false);
      assert.ok((await observeBridge()).dragPhases.includes('move'));
      await avatar.click(); await waitBridge(() => window.haruUiPreview.observe().expanded);
      await page.setViewportSize({ width: 420, height: 740 });
      await settleVisual(page, true);
      assert.equal(await page.getByLabel('给 Haru 发消息', { exact: true }).inputValue(), draft);
    }
  });

  stage = 'console-and-network-clean';
  assert.equal(report.consoleErrors, 0, 'Browser console errors are validation failures');
  assert.equal(report.pageErrors, 0, 'Unhandled browser errors are validation failures');
  assert.equal(report.blockedRequests, 0, 'A disallowed request was attempted');
  assert.equal(report.blockedWebSockets, 0, 'A disallowed WebSocket was attempted');
  report.checks.push(stage);
  assert.equal(expired, false, 'Browser preview deadline expired');
  report.passed = true;
  report.completedStage = 'complete';
} catch (error) {
  report.failureStage = stage;
  if (page) {
    try {
      const value = await page.evaluate(observeLayout);
      await page.screenshot({ path: path.join(evidence, 'failure.png'), fullPage: false, omitBackground: !value.expanded, timeout: 5000 });
      report.failureVisual = value;
    } catch { /* Preserve original failure; never substitute an illustration. */ }
  }
  console.error(`Haru synthetic UI preview failed at ${stage}:`, error);
  process.exitCode = 1;
} finally {
  await finalize();
}
