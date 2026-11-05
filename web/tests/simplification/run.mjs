/** Real Chromium + production dist; every HTTP request is fulfilled from files or
 * an allowlisted synthetic API. No backend, model, mailbox or credential is used.
 * Run only in an authorized browser-capable executor (e.g. the CI browser job).
 */
import { chromium } from 'playwright-core';
import { readFile, mkdir, writeFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { loadHaruAssetExpectations, observeHaruAssets } from '../../../desktop/live2d-assets.mjs';
import { createSimplificationFixture, knowledgeTitle, referenceTitle, frozenEvidence, allowedWrites, isDomainWriteAttempt, isContentFreePresenceRequest } from './fixture-api.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dist = path.join(root, 'dist');
const output = path.resolve(process.env.SIMPLIFICATION_EVIDENCE_DIR || path.join(root, '../artifacts/simplification-browser'));
const origin = 'http://127.0.0.1:4175';
await mkdir(output, { recursive: true });
const report = {
  kind: 'Real Chromium rendering production dist with strict synthetic API',
  startedAt: new Date().toISOString(), browser: null, source: {}, cases: [], failures: [],
  boundaries: [
    'Synthetic client acceptance, not backend persistence, real-model, real-mail or installed Electron acceptance.',
    'Desktop safety checks use only a test preload contract adapter; no update is installed or download attempted.',
    'No claim of complete coverage of every retained-owner domain or provider recovery protocol.',
  ],
};
try {
  report.source.commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
  report.source.tree = execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { cwd: root, encoding: 'utf8' }).trim();
  report.source.worktree = execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8' }).trim();
} catch (error) { report.source.error = String(error); }
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
report.source.dist = {};
for (const file of ['index.html', ...(await readdir(path.join(dist, 'assets'))).filter(file => /\.(js|css)$/.test(file)).map(file => `assets/${file}`)]) {
  report.source.dist[file] = hash(await readFile(path.join(dist, file)));
}
let haruAssetExpectations;
let browser;
const counts = (api, method, pathname) => api.requests.filter(request => request.method === method && request.path === pathname);
const writes = api => api.requests.filter(isDomainWriteAttempt);
async function eventually(predicate, message, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await predicate()) return; await new Promise(resolve => setTimeout(resolve, 50)); }
  assert.fail(message);
}
async function screenshot(page, result, name) {
  await page.evaluate(() => document.fonts.ready);
  const filename = `${result.name}-${name}.png`;
  await page.screenshot({ path: path.join(output, filename), fullPage: true });
  result.screenshots.push({ file: filename, sha256: hash(await readFile(path.join(output, filename))), kind: 'actual Chromium pixels' });
}
async function go(page, view) {
  await page.goto(`${origin}/?view=${view}`);
  await page.getByRole('navigation', { name: '主导航' }).waitFor();
}
async function navigate(page, name) {
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name, exact: true }).click();
}
// Ant Design icons contribute accessible names (e.g. "setting 配置 AI").
// Match the complete visible button label, not an incomplete accessible name.
function textButton(scope, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return scope.getByRole('button').filter({ hasText: new RegExp(`^${escaped}$`) });
}
async function inputIs(locator, expected, description) {
  await locator.waitFor({ state: 'visible' });
  await eventually(async () => await locator.inputValue() === expected, description);
}
async function afterPaint(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function settledModalState(dialog) {
  return dialog.evaluate(async node => {
    const paint = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const moving = () => {
      // Only the modal and its ancestors; never the infinite loading spinner.
      for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
        if (ancestor.getAnimations().some(animation => animation.effect?.getTiming().iterations !== Infinity
          && (animation.playState === 'running' || animation.pending))) return true;
      }
      return false;
    };
    await paint();
    const deadline = performance.now() + 3000;
    while (moving() && performance.now() < deadline) await paint();
    return { connected: node.isConnected, visible: node.getClientRects().length > 0
      && getComputedStyle(node).visibility === 'visible', moving: moving(),
      leaving: /ant-zoom-leave/.test(node.className), focused: node.contains(document.activeElement),
      inputDisabled: node.querySelector('#knowledge-note-title')?.disabled === true };
  });
}
async function openAI(page) {
  await textButton(page, '配置 AI').click();
  await page.getByRole('region', { name: 'AI 设置', exact: true }).waitFor();
  await eventually(async () => (await page.locator('#model').inputValue()).length > 0, 'AI provider fields initialize');
}
async function closeStory(page) {
  await page.getByRole('dialog').getByRole('button', { name: /^(Close|关闭)$/ }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
}
async function safety(page) {
  return page.evaluate(async () => {
    const id = `qa-${Date.now()}-${Math.random()}`;
    const listener = window.__simplificationSafety.listener;
    if (!listener) throw new Error('Production safety listener was not registered');
    listener({ id });
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
      if (window.__simplificationSafety.replies[id]) return window.__simplificationSafety.replies[id];
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw new Error('Production safety listener did not reply');
  });
}
async function runCase(name, options, work) {
  const result = { name, status: 'running', checks: [], screenshots: [], network: [], forbiddenNetwork: [], console: [], pageErrors: [] };
  report.cases.push(result);
  const api = createSimplificationFixture(options);
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', timezoneId: 'UTC', serviceWorkers: 'block', reducedMotion: 'reduce' });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
  await context.addInitScript(() => {
    try { localStorage.setItem('offerpilot.locale', 'zh-CN'); } catch { /* Initial opaque blank document. */ }
    // Only the public preload interface is doubled. Actual AppShell owns reads.
    // There is no desktop process, filesystem authority, download, or installer.
    window.__simplificationSafety = { listener: null, replies: {} };
    window.offerpilotUpdates = {
      getState: async () => ({ status: 'unavailable', currentVersion: 'synthetic', reason: 'browser fixture' }),
      check: async () => { throw new Error('Update action forbidden in browser fixture'); },
      download: async () => { throw new Error('Update action forbidden in browser fixture'); },
      install: async () => { throw new Error('Update action forbidden in browser fixture'); },
      onState: () => () => {},
      onPrepareInstall: listener => { window.__simplificationSafety.listener = listener; return () => { if (window.__simplificationSafety.listener === listener) window.__simplificationSafety.listener = null; }; },
      replyPrepareInstall: (id, value) => { window.__simplificationSafety.replies[id] = value; },
    };
  });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    result.network.push({ method: request.method(), url: url.href });
    if (url.origin !== origin) { result.forbiddenNetwork.push(url.href); return route.abort('blockedbyclient'); }
    if (url.pathname.startsWith('/api/')) {
      let body; try { body = request.postDataJSON(); } catch { /* GET */ }
      const response = await api.handle(request.method(), url.href, body);
      try { return await route.fulfill({ status: response.status, json: response.data }); }
      catch (error) { if (!page.isClosed()) throw error; }
      return;
    }
    if (request.method() !== 'GET') { result.forbiddenNetwork.push(`${request.method()} ${url.href}`); return route.abort(); }
    const relative = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).replace(/^\//, '');
    const filename = path.resolve(dist, relative);
    if (!filename.startsWith(dist + path.sep)) { result.forbiddenNetwork.push(url.href); return route.abort(); }
    try {
      const bytes = await readFile(filename);
      const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.woff2': 'font/woff2', '.wasm': 'application/wasm' }[path.extname(filename)] || 'application/octet-stream';
      return route.fulfill({ status: 200, body: bytes, contentType,
        headers: { 'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self'; worker-src 'self' blob:" } });
    } catch {
      result.forbiddenNetwork.push(`Missing local asset ${url.href}`);
      return route.fulfill({ status: 404, body: 'Missing fixture asset' });
    }
  });
  await context.routeWebSocket('**/*', socket => { result.forbiddenNetwork.push(`WebSocket ${socket.url()}`); socket.close(); });
  const page = await context.newPage();
  const haruAssets = observeHaruAssets(page, { origin, expected: haruAssetExpectations });
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => result.pageErrors.push(error.message));
  page.on('console', message => { if (['error', 'warning'].includes(message.type())) result.console.push({ level: message.type(), text: message.text() }); });
  const check = (label, condition, details) => { assert.ok(condition, details || label); result.checks.push(label); };
  console.log(`START ${name}`);
  try {
    await work({ page, api, check, result, snap: label => screenshot(page, result, label) });
    result.live2dAssets = await haruAssets.finish();
    check('No unmocked API endpoint', api.unexpected.length === 0, JSON.stringify(api.unexpected));
    check('No external network or websocket attempt', result.forbiddenNetwork.length === 0, JSON.stringify(result.forbiddenNetwork));
    check('No uncaught browser runtime error', result.pageErrors.length === 0, JSON.stringify(result.pageErrors));
    check('Every write is explicitly synthetic or validated content-free presence', api.requests.filter(request => request.method !== 'GET')
      .every(request => allowedWrites.has(`${request.method} ${request.path}`) || isContentFreePresenceRequest(request)));
    assert.equal(result.live2dAssets.status, 'passed', 'All four Haru responses must complete with source/dist bytes');
    result.status = 'passed'; console.log(`PASS ${name}`);
  } catch (error) {
    result.live2dAssets ||= await haruAssets.finish();
    result.status = 'failed'; result.error = error.stack || String(error);
    report.failures.push({ name, error: result.error }); console.error(`FAIL ${name}: ${error.message}`);
    await screenshot(page, result, 'failure').catch(error => { result.screenshotError = String(error); });
    await writeFile(path.join(output, `${name}-failure-dom.txt`), await page.locator('body').innerText().catch(() => 'DOM unavailable'));
  } finally {
    haruAssets.dispose();
    api.releaseAll();
    result.apiRequests = api.requests; result.unmockedApi = api.unexpected; result.finishedAt = new Date().toISOString();
    await context.tracing.stop({ path: path.join(output, `${name}-trace.zip`) });
    await writeFile(path.join(output, `${name}-requests.json`), JSON.stringify({ network: result.network, api: api.requests, forbidden: result.forbiddenNetwork, unmocked: api.unexpected }, null, 2));
    await context.close();
    await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  }
}

try {
  haruAssetExpectations = await loadHaruAssetExpectations({ sourceRoot: path.join(root, 'public'), servedRoot: dist });
  const executablePath = process.env.CHROMIUM_PATH || (existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
  browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true, args: ['--no-sandbox'] });
  report.browser = { engine: 'Chromium', version: browser.version(), headless: true, executablePath: executablePath || 'Playwright managed Chromium' };

  await runCase('onboarding-first-application', { firstUse: true }, async ({ page, api, check, snap }) => {
    await go(page, 'dashboard');
    const onboarding = page.getByRole('region', { name: '新手引导' });
    await onboarding.waitFor();
    check('First onboarding action is application, AI optional', await onboarding.locator('[data-onboarding-action]').first().getAttribute('data-onboarding-action') === 'create_first_application');
    await onboarding.getByRole('button', { name: '添加第一条投递', exact: true }).click();
    await page.getByLabel('公司', { exact: true }).fill('合成无 AI 首次投递');
    await page.getByLabel('岗位', { exact: true }).fill('合成工程师');
    await page.getByRole('button', { name: '核对并检查重复', exact: true }).click();
    await page.getByRole('button', { name: '确认保存', exact: true }).waitFor();
    await page.getByRole('button', { name: '返回修改', exact: true }).click();
    check('Application Back preserves draft', await page.getByLabel('公司', { exact: true }).inputValue() === '合成无 AI 首次投递');
    await page.getByRole('button', { name: /^取\s*消$/ }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    check('Cancelled application did not write', writes(api).length === 0);
    await onboarding.getByRole('button', { name: '稍后再说', exact: true }).click();
    await onboarding.waitFor({ state: 'hidden' });
    const reloadedOnboarding = page.waitForResponse(response => response.request().method() === 'GET'
      && new URL(response.url()).pathname === '/api/onboarding');
    await page.reload();
    await (await reloadedOnboarding).finished();
    await page.getByRole('navigation', { name: '主导航' }).waitFor();
    await eventually(() => counts(api, 'GET', '/api/onboarding').length >= 2, 'Onboarding reload read completed');
    await page.getByRole('button', { name: '添加第一个投递', exact: true }).waitFor();
    await afterPaint(page);
    check('Dismissal persists across reload', !(await onboarding.isVisible()) && api.snapshot().onboarding.dismissed);
    await navigate(page, '设置'); await openAI(page);
    const refreshedOnboarding = page.waitForResponse(response => response.request().method() === 'GET'
      && new URL(response.url()).pathname === '/api/onboarding');
    await page.getByRole('button', { name: /^保\s*存$/ }).click();
    await page.getByRole('region', { name: 'AI 设置', exact: true }).waitFor({ state: 'hidden' });
    await navigate(page, '今日');
    await (await refreshedOnboarding).finished();
    await page.getByRole('button', { name: '添加第一个投递', exact: true }).waitFor();
    await afterPaint(page);
    check('Unrelated settings save does not reopen onboarding', !(await onboarding.isVisible()) && api.snapshot().onboarding.dismissed);
    await navigate(page, '设置');
    await page.getByRole('button', { name: '重新打开新手引导', exact: true }).click();
    await eventually(() => api.snapshot().onboarding.force_open, 'Explicit reopen reached fixture');
    await navigate(page, '今日'); await onboarding.waitFor();
    await onboarding.getByRole('button', { name: '添加第一条投递', exact: true }).click();
    await page.getByLabel('公司', { exact: true }).fill('合成无 AI 首次投递');
    await page.getByLabel('岗位', { exact: true }).fill('合成工程师');
    await page.getByRole('button', { name: '核对并检查重复', exact: true }).click();
    await page.getByRole('button', { name: '确认保存', exact: true }).click();
    await eventually(() => api.snapshot().applications.length === 1, 'First application created');
    await page.getByRole('dialog', { name: '添加投递', exact: true }).waitFor({ state: 'hidden' });
    check('First application creates exactly once without configured AI', counts(api, 'POST', '/api/applications').length === 1 && api.snapshot().settings.has_api_key === false);
    check('Onboarding never calls a model or provider test', counts(api, 'POST', '/api/settings/providers/test').length === 0);
    await snap('saved-without-ai');
  });

  await runCase('ai-provider-drafts', {}, async ({ page, api, check, snap }) => {
    await go(page, 'settings'); await openAI(page);
    check('Advanced provider list starts collapsed', !(await page.getByTestId('ai-provider-list').isVisible()));
    await page.locator('#model').fill('draft-one');
    await page.getByText('多供应商管理（高级）', { exact: true }).click();
    await textButton(page.getByTestId('ai-provider-list'), '编辑供应商').nth(1).click();
    await inputIs(page.locator('#model'), 'model-two', 'Second provider form is committed before editing');
    await page.locator('#model').fill('draft-two');
    await textButton(page.getByTestId('ai-provider-list'), '编辑供应商').nth(0).click();
    await inputIs(page.locator('#model'), 'draft-one', 'First provider draft restored after switching');
    check('Switching providers retains both drafts', await page.locator('#model').inputValue() === 'draft-one');
    const release = api.holdNext('POST', '/api/settings/providers/test');
    await textButton(page.getByRole('region', { name: 'AI 设置', exact: true }), '测试连接').click();
    await eventually(() => counts(api, 'POST', '/api/settings/providers/test').length === 1, 'Draft test dispatched');
    check('Test sends unsaved provider draft without saving settings', counts(api, 'POST', '/api/settings/providers/test')[0].body.provider.model === 'draft-one' && counts(api, 'PUT', '/api/settings').length === 0);
    await textButton(page.getByRole('region', { name: 'AI 设置', exact: true }), '返回设置').click();
    const testedResponse = page.waitForResponse(response => response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/settings/providers/test');
    release({ status: 200, data: { ok: true, message: 'STALE CONNECTION RESULT MUST NOT REAPPEAR' } });
    const response = await testedResponse;
    check('Released connection test response completes in the browser', await response.finished() === null);
    await openAI(page);
    check('Cancel/reopen restores saved provider, not abandoned draft', await page.locator('#model').inputValue() === 'model-one');
    await afterPaint(page);
    check('Late test result cannot resurrect after cancel', !(await page.getByText('STALE CONNECTION RESULT MUST NOT REAPPEAR', { exact: true }).isVisible()));
    await page.locator('#model').fill('final-one');
    await page.getByText('多供应商管理（高级）', { exact: true }).click();
    await textButton(page.getByTestId('ai-provider-list'), '编辑供应商').nth(1).click();
    await inputIs(page.locator('#model'), 'model-two', 'Cancelled second-provider draft was discarded');
    await page.locator('#model').fill('final-two');
    await snap('two-provider-drafts');
    await page.getByRole('button', { name: /^保\s*存$/ }).click();
    await page.getByRole('region', { name: 'AI 设置', exact: true }).waitFor({ state: 'hidden' });
    const saved = counts(api, 'PUT', '/api/settings');
    check('One save commits all provider drafts', saved.length === 1 && JSON.stringify(saved[0].body.providers.map(provider => provider.model)) === JSON.stringify(['final-one', 'final-two']));
    check('Fallback and write confirmation policy preserved', saved[0].body.chat_auto_approve_writes === false && JSON.stringify(saved[0].body.fallback_provider_ids) === '["two"]');
    await openAI(page); check('Saved provider survives reopen', await page.locator('#model').inputValue() === 'final-one');
    await snap('saved-provider');
  });

  await runCase('commands-and-history', {}, async ({ page, api, check, snap }) => {
    await go(page, 'dashboard');
    for (const [query, destination] of [['设置', '打开设置'], ['Offer', '打开 Offer 中心']]) {
      await page.getByRole('button', { name: '快速打开', exact: true }).click();
      const input = page.getByRole('combobox'); await input.fill(query);
      await page.getByRole('option').filter({ has: page.getByText(destination, { exact: true }) }).waitFor();
      check(`${query}: destination appears once`, await page.getByRole('option').count() === 1);
      await input.press('Escape'); await page.getByRole('dialog').waitFor({ state: 'hidden' });
    }
    await page.getByRole('button', { name: '快速打开', exact: true }).click();
    await page.getByRole('combobox').fill('设置');
    await page.getByRole('option').filter({ has: page.getByText('打开设置', { exact: true }) }).waitFor();
    await page.getByRole('combobox').press('Enter');
    await page.getByRole('combobox', { name: '界面语言', exact: true }).waitFor();
    check('Command Enter navigates once', new URL(page.url()).searchParams.get('view') === 'settings');
    await page.goBack(); await page.getByRole('tab', { name: '概览', exact: true }).waitFor();
    check('Browser Back restores prior route', new URL(page.url()).searchParams.get('view') === 'dashboard');
    await page.goForward(); await page.getByRole('combobox', { name: '界面语言', exact: true }).waitFor();
    check('Browser Forward restores settings without command ghost', !(await page.getByRole('dialog').isVisible()));
    check('Commands and history cause no writes', writes(api).length === 0);
    await snap('after-back-forward');
  });

  await runCase('today-inbox-and-history', { today: true }, async ({ page, api, check, snap }) => {
    await go(page, 'reminders');
    await page.getByText('Interview invitation', { exact: true }).waitFor();
    const proactive = page.getByRole('region', { name: '主动任务', exact: true });
    const resultCard = text => proactive.getByRole('article', { includeHidden: true }).filter({ hasText: text });
    const draft = resultCard('合成待查看准备草稿');
    await draft.waitFor({ state: 'visible' });
    check('Ready draft renders its exact body with the unsent boundary',
      (await draft.innerText()).split('\n').map(line => line.trim()).includes('合成待查看准备草稿')
      && await draft.getByText('自动准备草稿 · 未发送', { exact: true }).isVisible());
    await page.getByText('需要检查', { exact: true }).waitFor();
    await page.getByText(/合成笔试公司/).first().waitFor();
    check('Mail, candidate and actionable proactive results are co-located', true);
    check('Same successful reminder is not duplicated', await resultCard('合成重复提醒不应再显示').count() === 0);
    const historical = resultCard('合成历史提醒');
    check('Published non-current reminder exists but starts hidden in history', await historical.count() === 1 && !(await historical.isVisible()));
    await page.getByText('已发布提醒历史（1）', { exact: true }).click();
    await historical.waitFor({ state: 'visible' });
    check('Expanded history renders the exact published body', (await historical.innerText()).split('\n').map(line => line.trim()).includes('合成历史提醒'));
    await snap('combined-inbox');
    await page.getByText('Interview invitation', { exact: true }).click();
    const mailReview = page.getByRole('dialog', { name: '核对邮件建议', exact: true });
    await mailReview.getByRole('button', { name: '预览最终变更', exact: true }).waitFor();
    // rc-dialog focuses its content after the opening motion. A visible child
    // is not proof that an immediate page-level Escape reaches the dialog.
    // Observe natural focus; do not force focus or disable production motion.
    await eventually(() => mailReview.evaluate(dialog =>
      dialog.contains(document.activeElement)
      && !/ant-zoom-(appear|enter)/.test(dialog.className)),
    'Mail review finishes opening and acquires keyboard focus');
    await snap('mail-review-no-write');
    await page.keyboard.press('Escape'); await mailReview.waitFor({ state: 'hidden' });
    check('Escape closes the focused mail review without writing', writes(api).length === 0);
    await page.getByText('Interview invitation', { exact: true }).click();
    await mailReview.getByRole('button', { name: '暂不处理，保留待核对', exact: true }).click();
    await mailReview.waitFor({ state: 'hidden' });
    await page.getByText('Interview invitation', { exact: true }).waitFor();
    check('Explicit cancellation preserves the pending suggestion without writing', writes(api).length === 0);
    await navigate(page, '设置');
    check('Settings no longer owns mail review or proactive results', !(await page.getByText('Interview invitation', { exact: true }).isVisible()) && !(await resultCard('合成待查看准备草稿').isVisible()));
    check('Inbox navigation/review opening never writes', writes(api).length === 0);
  });

  await runCase('practice-sources-and-return', {}, async ({ page, api, check, snap }) => {
    await go(page, 'interview');
    const home = page.getByTestId('free-practice-workspace'); await home.waitFor();
    await home.getByRole('button', { name: '快速模拟', exact: true }).click();
    await page.getByTestId('quick-interview-practice').waitFor({ state: 'visible' });
    check('Quick entry uses its own source mode', await page.locator('[data-readiness-mode="quick"]').count() === 1);
    await snap('quick-sources');
    await navigate(page, '面试'); await home.waitFor();
    await home.getByRole('button', { name: '复盘重点练习', exact: true }).click();
    await page.getByTestId('review-focus-practice').waitFor({ state: 'visible' });
    check('Review entry does not display quick source form', !(await page.getByTestId('quick-interview-practice').isVisible()));
    await navigate(page, '面试'); await home.waitFor();
    await home.getByRole('button', { name: '复习到期题', exact: true }).click();
    await eventually(() => new URL(page.url()).searchParams.get('view') === 'questions', 'Due questions route');
    await page.goBack(); await home.waitFor();
    check('Back returns from due questions to interview', new URL(page.url()).searchParams.get('view') === 'interview');
    check('Practice entry and return do not invoke models or create fake records', writes(api).length === 0);
    await page.setViewportSize({ width: 430, height: 932 });
    await snap('narrow-entry');
    check('Practice entry has no document horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  });

  await runCase('knowledge-evidence-cancel-and-unknown', { materials: true }, async ({ page, api, check, snap }) => {
    await go(page, 'knowledge');
    const sources = page.locator('.knowledge-source-list');
    await sources.getByText(referenceTitle, { exact: true }).waitFor();
    check('Reference sources render separately from confirmed interview captures',
      await sources.getByRole('listitem').count() === 1 && await sources.getByText(knowledgeTitle, { exact: true }).count() === 0);
    await snap('reference-source-boundary');
    await page.getByRole('tab', { name: '已确认的知识笔记', exact: true }).click();
    const notes = page.getByTestId('confirmed-knowledge-notes'); await notes.getByText(knowledgeTitle, { exact: false }).waitFor();
    check('Reference source is not promoted into a confirmed knowledge note', await notes.getByText(referenceTitle, { exact: true }).count() === 0);
    await notes.getByRole('button', { name: '查看来源与依据', exact: true }).click();
    await page.getByText('资料来源 #31', { exact: true }).waitFor();
    check('Confirmed knowledge preserves frozen source evidence', await page.getByText(frozenEvidence, { exact: true }).isVisible());
    await snap('frozen-source');
    await page.getByRole('button', { name: '返回知识笔记', exact: true }).click();
    await notes.getByRole('button', { name: /^编\s*辑$/ }).click();
    await page.locator('#knowledge-note-title').fill('合成未保存标题');
    await page.getByRole('dialog').getByRole('button', { name: /^取\s*消$/ }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await notes.getByRole('button', { name: /^编\s*辑$/ }).click();
    await page.getByRole('dialog', { name: '编辑并确认新版本', exact: true }).waitFor({ state: 'visible' });
    await inputIs(page.locator('#knowledge-note-title'), knowledgeTitle, 'Reopened knowledge form restores the confirmed title');
    check('Cancel discards only the knowledge edit and does not save', await page.locator('#knowledge-note-title').inputValue() === knowledgeTitle && writes(api).length === 0);
    await page.locator('#knowledge-note-title').fill('合成确认修订');
    const release = api.holdNext('POST', '/api/knowledge/notes/7/lifecycle');
    const noteEditor = page.getByRole('dialog', { name: '编辑并确认新版本', exact: true });
    const saveNote = textButton(noteEditor, '确认保存新版本');
    await saveNote.dblclick();
    await eventually(() => counts(api, 'POST', '/api/knowledge/notes/7/lifecycle').length === 1, 'Knowledge single flight started');
    await eventually(async () => await page.locator('#knowledge-note-title').isDisabled()
      && await noteEditor.evaluate(node => node.contains(document.activeElement)),
    'Pending knowledge editor is disabled and owns keyboard focus');
    await page.keyboard.press('Escape');
    const pendingModal = await settledModalState(noteEditor);
    check('Pending knowledge save blocks dismissal and repeated submission',
      pendingModal.connected && pendingModal.visible && !pendingModal.moving && !pendingModal.leaving
      && pendingModal.focused && pendingModal.inputDisabled
      && counts(api, 'POST', '/api/knowledge/notes/7/lifecycle').length === 1,
      JSON.stringify(pendingModal));
    release({ status: 503, data: { error_code: 'synthetic_uncertain' } });
    await page.getByRole('dialog').getByText(/操作未确认/).waitFor(); await snap('unknown-save');
    const first = counts(api, 'POST', '/api/knowledge/notes/7/lifecycle')[0].body;
    // Ant Design can retain a transparent leaving loading icon after busy=false.
    // Its accessible name is still "loading", so use the complete visible label
    // and independently require the production busy state to have settled.
    await eventually(async () => await saveNote.isEnabled()
      && !(await saveNote.getAttribute('class') || '').split(/\s+/).includes('ant-btn-loading')
      && await page.locator('#knowledge-note-title').isEnabled(),
    'Unknown result releases the pending UI before the explicit retry');
    await saveNote.click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    const attempts = counts(api, 'POST', '/api/knowledge/notes/7/lifecycle');
    check('Unknown retry preserves exact mutation identity and body', attempts.length === 2 && JSON.stringify(attempts[1].body) === JSON.stringify(first));
    check('Retry advances exactly one confirmed version', api.snapshot().note.version_number === 3);
    await notes.getByText('合成确认修订', { exact: false }).waitFor();
  });

  await runCase('experience-owner-and-safety', { materials: true }, async ({ page, api, check, result, snap }) => {
    await go(page, 'reviews');
    await page.getByTestId('experience-materials-view').waitFor();
    const captures = page.getByTestId('confirmed-capture-list');
    const captureRow = captures.getByRole('listitem').filter({ has: page.getByText(knowledgeTitle, { exact: true }) });
    await captureRow.waitFor({ state: 'visible' });
    check('Confirmed interview capture renders a nonempty successful list',
      await captureRow.count() === 1 && await captures.getByRole('listitem').count() === 1
      && await captureRow.getByText('来源已更新，已确认内容仍保留', { exact: true }).isVisible());
    check('Successful captures are not an unavailable or empty placeholder',
      await captures.getByRole('alert').count() === 0 && await captures.getByRole('status').count() === 0);
    check('Personal experience does not contain the external reference source',
      await page.getByTestId('experience-materials-view').getByText(referenceTitle, { exact: true }).count() === 0);
    const stories = page.getByTestId('experience-story-library');
    await stories.getByText('还没有已确认的面试故事', { exact: true }).waitFor();
    await snap('confirmed-capture-list');
    await page.getByRole('button', { name: '新建故事', exact: true }).click();
    const assertion = '合成经历：通过测量和回归验证改善接口性能。';
    await page.getByRole('textbox', { name: '用户明确原始陈述', exact: true }).fill(assertion);
    await page.getByRole('button', { name: /^加\s*入$/ }).click();
    result.safetyDirty = await safety(page);
    check('Retained experience dirty state reaches production safety reader', result.safetyDirty.hasDraft === true);
    await closeStory(page); await navigate(page, '面试'); await navigate(page, '素材库');
    await page.getByRole('tab', { name: '我的经历', exact: true }).click();
    await page.getByRole('button', { name: '新建故事', exact: true }).click();
    await page.getByText(assertion, { exact: false }).waitFor();
    check('Canonical experience owner retains draft across navigation', writes(api).length === 0);
    await page.getByRole('button', { name: '手动编写并保存', exact: true }).click();
    await page.getByRole('textbox', { name: '手动故事标题', exact: true }).fill('合成性能优化故事');
    await page.getByRole('textbox', { name: '手动故事行动', exact: true }).fill('测量热点并验证最小变更。');
    for (const select of await page.locator('select[data-testid^="manual-evidence-"]').all()) await select.selectOption('assertion:1');
    const release = api.holdNext('POST', '/api/interview-stories');
    await page.getByRole('button', { name: '确认手动保存故事版本', exact: true }).click();
    await eventually(() => counts(api, 'POST', '/api/interview-stories').length === 1, 'Manual story save started');
    result.safetyPending = await safety(page);
    check('Pending story cannot look safe to install', result.safetyPending.hasDraft || result.safetyPending.activeRun || result.safetyPending.pendingApproval || !result.safetyPending.ready);
    release({ status: 503, data: { error_code: 'operation_result_unknown' } });
    await page.getByRole('button', { name: '使用原尝试重试', exact: true }).waitFor();
    result.safetyUnknown = await safety(page);
    check('Unknown story remains active in retained-owner aggregate', result.safetyUnknown.activeRun === true);
    await snap('retained-unknown-story');
    const first = counts(api, 'POST', '/api/interview-stories')[0].body;
    await closeStory(page); await navigate(page, '今日'); await navigate(page, '素材库');
    await page.getByRole('tab', { name: '我的经历', exact: true }).click();
    await page.getByRole('button', { name: '新建故事', exact: true }).click();
    await page.getByRole('button', { name: '使用原尝试重试', exact: true }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    const attempts = counts(api, 'POST', '/api/interview-stories');
    check('Story retry after navigation preserves owner request identity', attempts.length === 2 && JSON.stringify(attempts[1].body) === JSON.stringify(first));
    check('Manual story never calls proposal/model APIs', writes(api).every(request => request.path === '/api/interview-stories'));
    check('Saved story request retains the entered content and explicit assertion evidence',
      first.content.title === '合成性能优化故事'
      && first.content.blocks.some(block => block.kind === 'action' && block.text === '测量热点并验证最小变更。' && block.fact_mode === 'evidence_backed')
      && JSON.stringify(first.assertions) === JSON.stringify([assertion]) && first.evidence_links.length > 0
      && first.evidence_links.every(link => link.source_kind === 'user_assertion' && link.source_id === 'assertion_001'
        && link.source_path === '/statement' && link.excerpt === assertion));
    const storyRow = stories.getByRole('listitem').filter({ hasText: '合成性能优化故事' });
    await storyRow.getByText('合成性能优化故事', { exact: true }).waitFor({ state: 'visible' });
    check('Successful retry renders exactly one saved story row with its title and version',
      await storyRow.count() === 1 && api.snapshot().stories.length === 1
      && await storyRow.getByText('版本 1', { exact: true }).isVisible()
      && await storyRow.getByText('使用中', { exact: true }).isVisible());
    check('Saved story and confirmed capture stay in separate experience lists',
      await captures.getByText('合成性能优化故事', { exact: true }).count() === 0
      && await stories.getByText(knowledgeTitle, { exact: true }).count() === 0
      && await captureRow.isVisible());
    await afterPaint(page);
    await snap('saved-manual-story');
    await page.getByRole('tab', { name: '参考知识', exact: true }).click();
    const sources = page.locator('.knowledge-source-list');
    await sources.getByText(referenceTitle, { exact: true }).waitFor();
    check('Manual story save never adds a personal story or capture to reference sources',
      await sources.getByRole('listitem').count() === 1
      && await sources.getByText('合成性能优化故事', { exact: true }).count() === 0
      && await sources.getByText(knowledgeTitle, { exact: true }).count() === 0);
    await page.getByRole('tab', { name: '已确认的知识笔记', exact: true }).click();
    const notes = page.getByTestId('confirmed-knowledge-notes');
    await notes.getByText(knowledgeTitle, { exact: false }).waitFor();
    check('Manual story save does not promote the story to confirmed knowledge',
      await notes.getByText('合成性能优化故事', { exact: true }).count() === 0
      && writes(api).every(request => request.path === '/api/interview-stories'));
  });

  await runCase('diagnostics-demand-only', {}, async ({ page, api, check, snap }) => {
    await go(page, 'settings');
    await page.getByText('查看运行日志与诊断', { exact: true }).waitFor();
    await page.waitForTimeout(16000);
    check('Hidden diagnostics do not query or poll logs', counts(api, 'GET', '/api/logs').length === 0);
    await page.getByText('查看运行日志与诊断', { exact: true }).click();
    await eventually(() => counts(api, 'GET', '/api/logs').length >= 1, 'Visible diagnostics query logs');
    await snap('visible-diagnostics');
    await page.getByText('查看运行日志与诊断', { exact: true }).click();
    const closedCount = counts(api, 'GET', '/api/logs').length;
    await page.waitForTimeout(16000);
    check('Closing diagnostics stops hidden polling', counts(api, 'GET', '/api/logs').length === closedCount);
    await page.getByText('查看运行日志与诊断', { exact: true }).click();
    await openAI(page);
    const aiCount = counts(api, 'GET', '/api/logs').length;
    await page.waitForTimeout(16000);
    check('AI subview also stops hidden diagnostics polling', counts(api, 'GET', '/api/logs').length === aiCount);
    check('Viewing diagnostics never writes', writes(api).length === 0);
  });
} catch (error) {
  report.failures.push({ name: 'browser/setup', error: error.stack || String(error) });
  console.error(error);
} finally {
  report.finishedAt = new Date().toISOString();
  report.status = report.failures.length ? 'failed' : 'passed';
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser?.close();
  console.log(`REPORT ${path.join(output, 'report.json')}`);
  if (report.failures.length) process.exitCode = 1;
}
