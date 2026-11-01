/** Real Chromium against production web/dist; every response is fulfilled locally.
 * This proves UI behavior against a strict synthetic transport, NOT backend state,
 * desktop packaging, model quality, billing, or production provider integration.
 */
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { readFile, realpath, mkdir, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createCompanionFixture, ORIGIN } from './fixture-api.mjs';
import { classifyRequest } from './network-guard.mjs';

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const repo = path.resolve(web, '..');
const dist = path.join(web, 'dist');
const output = path.resolve(process.argv[2] || path.join(repo, 'artifacts/companion-browser'));
await mkdir(output, { recursive: true });
const report = { kind: 'production-web Chromium / synthetic UI-only acceptance', backendPersistenceProof: false,
  startedAt: new Date().toISOString(), assertions: [], failures: [], scenarios: [], screenshots: [],
  console: [], pageErrors: [], networkViolations: [], requests: [], requestFailures: [], captures: [] };
let browser;
const names = {
  status: /^(查看 Haru 的虚拟日常|View Haru's virtual daily life)$/,
  details: /^(Haru 的虚拟日常|Haru's virtual daily life)$/,
  close: /^(关闭日常详情|Close daily life details)$/,
  pause: /^(暂停日常|Pause activity)$/,
  resume: /^(继续日常|Resume activity)$/,
  withdraw: /^(这步请 Haru 自己做|Let Haru do this step)$/,
  complete: /^(约好的一步完成了|The agreed step is complete)$/,
  view: /^(查看日常活动|View daily life activity)$/,
  hide: /^(隐藏日常状态|Hide daily life status)$/,
  enabled: /^(开启虚拟日常|Enable virtual daily life)$/,
};
const status = page => page.getByRole('button', { name: names.status });
const details = page => page.locator(`section[aria-label="Haru 的虚拟日常"], section[aria-label="Haru's virtual daily life"]`);
const settings = page => page.locator('section[aria-labelledby="companion-life-settings-title"]');
const button = (page, key) => details(page).getByRole('button', { name: names[key] });
const commands = (api, action) => api.requests.filter(request => request.path === '/api/companion/events' && request.body?.event === action);
const check = (name, condition, detail) => { assert.ok(condition, detail || name); report.assertions.push(name); };
const equal = (name, actual, expected) => { assert.deepEqual(actual, expected, name); report.assertions.push(name); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(name, predicate, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await predicate()) { report.assertions.push(name); return; } await sleep(40); }
  throw new Error(`Timed out: ${name}`);
}
async function go(page, view = 'dashboard') {
  await page.goto(`${ORIGIN}/?view=${view}`);
  if (view === 'pilot') await page.getByRole('button', { name: /^(退出沉浸模式，返回原页面|Exit immersive mode and return to the previous page)$/ }).waitFor();
  else await page.locator('#root').waitFor({ state: 'visible' });
}
async function openCompanionSettings(page) {
  await go(page, 'settings');
  await page.getByRole('navigation', { name: /^(设置分组|Settings groups)$/ })
    .getByRole('button', { name: /^(AI 与隐私|AI and privacy)$/ }).click();
  await settings(page).waitFor();
  check('companion settings remain within the grouped AI/privacy section', await settings(page).evaluate(node => {
    const group = document.getElementById('settings-ai-privacy');
    const nextGroup = document.getElementById('settings-connections');
    return !!group && !!nextGroup
      && !!(group.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)
      && !!(node.compareDocumentPosition(nextGroup) & Node.DOCUMENT_POSITION_FOLLOWING);
  }));
  await settings(page).scrollIntoViewIfNeeded();
}
async function openDetails(page) {
  await status(page).click(); await details(page).waitFor();
  await until('detail command controls settle', async () => await button(page, 'close').isVisible()
    && await details(page).getByRole('button', { name: /^(暂停日常|Pause activity|继续日常|Resume activity)$/ }).isEnabled());
}
async function capture(page, name) {
  const stem = name.replace(/[^a-zA-Z0-9_-]/g, '-');
  const png = `${stem}.png`, dom = `${stem}.html`, accessibility = `${stem}.aria.txt`;
  await writeFile(path.join(output, dom), await page.content());
  await writeFile(path.join(output, accessibility), await page.locator('body').ariaSnapshot());
  await page.screenshot({ path: path.join(output, png), fullPage: true, animations: 'disabled' });
  report.screenshots.push(png);
  report.captures.push({ name, screenshot: png, dom, accessibility });
}
async function lifeLayout(page, label) {
  const geometry = await details(page).evaluate(node => ({ width: innerWidth, height: innerHeight,
    header: node.querySelector('header').getBoundingClientRect().toJSON(),
    close: node.querySelector('header button').getBoundingClientRect().toJSON(), bounds: node.getBoundingClientRect().toJSON(), buttons: [...node.querySelectorAll('button')].map(button => button.getBoundingClientRect().toJSON()) }));
  check(`${label}: life details and controls fit the horizontal viewport`, geometry.bounds.left >= -1 && geometry.bounds.right <= geometry.width + 1
    && geometry.buttons.every(rect => rect.left >= -1 && rect.right <= geometry.width + 1), JSON.stringify(geometry));
  check(`${label}: detail, title and close fit the vertical viewport`, geometry.bounds.top >= -1
    && geometry.bounds.bottom <= geometry.height + 1 && geometry.header.top >= -1
    && geometry.close.top >= -1 && geometry.close.bottom <= geometry.height + 1, JSON.stringify(geometry));
}

async function runCase(name, { width = 1280, height = 900, locale = 'en-US' } = {}, test) {
  const api = createCompanionFixture(), scenario = { name, width, height, locale, status: 'running' };
  report.scenarios.push(scenario);
  const errorsBefore = report.pageErrors.length, networkBefore = report.networkViolations.length;
  let context; const held = [];
  const release = () => { while (held.length) held.shift()(); };
  try {
    context = await browser.newContext({ viewport: { width, height }, serviceWorkers: 'block', reducedMotion: 'reduce' });
    await context.exposeBinding('__recordFixtureCspViolation', (_, violation) => report.networkViolations.push({ scenario: name, ...violation }));
    await context.addInitScript(({ locale, origin }) => {
      if (location.origin !== origin) return;
      if (!localStorage.getItem('offerpilot.locale')) localStorage.setItem('offerpilot.locale', locale);
      localStorage.setItem('offerpilot:pilot-mascot-visible', 'true');
      localStorage.setItem('offerpilot:pilot-mascot-animation', 'off');
      document.addEventListener('securitypolicyviolation', event => {
        void window.__recordFixtureCspViolation({ type: 'CSP', blockedURI: event.blockedURI, directive: event.violatedDirective });
      });
    }, { locale, origin: ORIGIN });
    const pages = new WeakMap(); let nextPage = 0;
    context.on('page', page => {
      const pageId = `${name}/page-${++nextPage}`; pages.set(page, pageId); page.setDefaultTimeout(8000);
      page.on('pageerror', error => report.pageErrors.push({ scenario: name, page: pageId, error: error.message }));
      page.on('console', message => { if (['error', 'warning'].includes(message.type())) report.console.push({ scenario: name, type: message.type(), text: message.text() }); });
      page.on('requestfailed', request => report.requestFailures.push({ scenario: name, url: request.url(), method: request.method(), failure: request.failure()?.errorText }));
    });
    await context.route('**/*', async route => {
      const request = route.request(), url = request.url(), method = request.method();
      let pageId = `${name}/worker`;
      try { pageId = pages.get(request.frame().page()) || pageId; } catch { /* Worker requests still use the same strict router. */ }
      report.requests.push({ scenario: name, page: pageId, method, url });
      const target = classifyRequest(method, url, dist);
      if (target.blocked) { report.networkViolations.push({ scenario: name, method, url, reason: target.blocked }); return route.abort('blockedbyclient'); }
      if (target.api) {
        let body;
        try { body = request.postDataJSON() ?? undefined; } catch { report.networkViolations.push({ scenario: name, url, reason: 'invalid_json_body' }); return route.abort(); }
        const result = api.handle(method, url, body, request.headers());
        api.requests.at(-1).page = pageId;
        if (result.dropResponse) return route.abort('connectionreset');
        if (result.holdResponse) await new Promise(resolve => held.push(resolve));
        return route.fulfill({ status: result.status, json: result.data });
      }
      try {
        const resolved = await realpath(target.filename);
        if (!resolved.startsWith(await realpath(dist) + path.sep)) throw new Error('symlink_asset_escape');
        const body = await readFile(resolved);
        const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
          '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.woff2': 'font/woff2', '.wasm': 'application/wasm' }[path.extname(resolved)] || 'application/octet-stream';
        return route.fulfill({ status: 200, body, contentType, headers: {
          'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self'; worker-src 'self' blob:; form-action 'none'; object-src 'none'",
        } });
      } catch (error) {
        report.networkViolations.push({ scenario: name, url, reason: `local_asset:${error.message}` });
        return route.fulfill({ status: 404, body: 'Missing/unsafe synthetic local asset' });
      }
    });
    await context.routeWebSocket('**/*', socket => {
      report.networkViolations.push({ scenario: name, type: 'WebSocket', url: socket.url() }); socket.close();
    });
    const page = await context.newPage();
    await test({ page, context, api, release });
    check(`${name}: every API request matched the strict fixture`, api.violations.length === 0, JSON.stringify(api.violations));
    check(`${name}: no external or unsafe network`, report.networkViolations.length === networkBefore);
    check(`${name}: no page runtime error`, report.pageErrors.length === errorsBefore);
    scenario.status = 'passed';
    console.log(`PASS ${name}`);
  } catch (error) {
    scenario.status = 'failed'; report.failures.push({ scenario: name, error: error.stack || String(error) });
    console.error(`FAIL ${name}: ${error.message}`);
    for (const [index, page] of (context?.pages() || []).entries()) {
      await capture(page, `failure-${name}-${index + 1}`).catch(error => { report.failures.push({ scenario: name, captureError: error.message }); });
    }
  } finally {
    release();
    await context?.close().catch(error => { scenario.status = 'failed'; report.failures.push({ scenario: name, error: `Context close: ${error.message}` }); });
    scenario.apiRequests = api.requests; scenario.fixtureViolations = api.violations;
    scenario.finalSnapshot = api.snapshot(); scenario.receipts = api.receipts();
  }
}

try {
  const index = await readFile(path.join(dist, 'index.html'));
  report.source = { commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim(),
    tree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { cwd: repo, encoding: 'utf8' }).trim(),
    dirty: execFileSync('git', ['status', '--porcelain'], { cwd: repo, encoding: 'utf8' }).trim(),
    lockSha256: createHash('sha256').update(await readFile(path.join(web, 'package-lock.json'))).digest('hex'),
    distIndexSha256: createHash('sha256').update(index).digest('hex') };
  const executablePath = process.env.CHROMIUM_PATH;
  browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true });
  report.browser = { engine: 'Chromium', version: browser.version(), headless: true,
    executablePath: executablePath || chromium.executablePath(),
    source: executablePath ? 'CHROMIUM_PATH' : 'Playwright managed Chromium' };

  for (const [locale, width] of [['zh-CN', 1280], ['en-US', 900]]) {
    await runCase(`read-close-refresh-${locale}-${width}`, { locale, width }, async ({ page, api }) => {
      const before = api.snapshot(); await go(page); await openDetails(page);
      await lifeLayout(page, locale); await capture(page, `waiting-${locale}-${width}`);
      check(`${locale}: authored life text is localized`, locale === 'en-US'
        ? !(await details(page).innerText()).match(/\p{Script=Han}/u)
        : (await details(page).innerText()).includes('不倒计时，也不会催促'));
      await button(page, 'close').click(); await details(page).waitFor({ state: 'hidden' });
      await openDetails(page); await button(page, 'close').focus(); await page.keyboard.press('Escape');
      await details(page).waitFor({ state: 'hidden' });
      equal(`${locale}: Escape closes real detail`, await status(page).getAttribute('aria-expanded'), 'false');
      await page.reload(); await openDetails(page);
      equal(`${locale}: open/close/Escape/refresh preserve activity`, api.snapshot().state, before.state);
      equal(`${locale}: no participation is manufactured`, api.snapshot().shared_participation, before.shared_participation);
      equal(`${locale}: no completion command`, commands(api, 'complete_shared').length, 0);
      await capture(page, `refreshed-${locale}-${width}`);
    });
  }

  for (const [width, height] of [[390, 480], [900, 360]]) {
    await runCase(`short-viewport-scroll-${width}-${height}`, { width, height }, async ({ page, api }) => {
      await go(page); await openDetails(page); const before = api.snapshot();
      await lifeLayout(page, `${width}x${height}`);
      const box = await details(page).boundingBox();
      check('short viewport requires internal scrolling', await details(page).evaluate(node => node.scrollHeight > node.clientHeight));
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.wheel(0, 1000);
      await until('wheel scrolls the detail internally', () => details(page).evaluate(node => node.scrollTop > 0));
      await lifeLayout(page, `${width}x${height} scrolled`);
      await until('last controls remain reachable inside viewport and scrolling content', async () => {
        const footer = await details(page).locator('footer').boundingBox();
        const content = await details(page).boundingBox();
        return footer.y >= Math.max(0, content.y) - 1
          && footer.y + footer.height <= Math.min(height, content.y + content.height) + 1;
      });
      await capture(page, `short-scrolled-${width}-${height}`);
      await button(page, 'close').click(); await details(page).waitFor({ state: 'hidden' });
      equal('small viewport scrolling and closing never changes life', api.snapshot().state, before.state);
    });
  }

  await runCase('controls-repeat-loss-withdraw', {}, async ({ page, api, release }) => {
    await go(page); await openDetails(page); const before = api.snapshot();
    api.permit('pause'); api.holdNextResponse('pause');
    await button(page, 'pause').click({ clickCount: 3, delay: 25 });
    await until('rapid repeated pointer clicks send exactly one pause', () => commands(api, 'pause').length === 1);
    check('in-flight shared controls are disabled', await button(page, 'pause').isDisabled());
    release(); await button(page, 'resume').waitFor();
    await until('pause receipt applied', () => button(page, 'resume').isEnabled());
    equal('paused shared source retained', api.snapshot().state.activity.paused_from, 'waiting_shared');
    api.permit('resume'); api.dropNextResponse('resume');
    await button(page, 'resume').click();
    await details(page).getByRole('alert').filter({ hasText: /result is not confirmed/ }).waitFor();
    check('unknown result prevents a new control action', await button(page, 'resume').isDisabled());
    const lost = commands(api, 'resume')[0]; check('response was deliberately lost after commit', lost.dropResponse);
    await capture(page, 'lost-response-retry');
    await details(page).getByRole('button', { name: 'Retry', exact: true }).click();
    await button(page, 'pause').waitFor(); await until('retry receipt settles UI', () => button(page, 'pause').isEnabled());
    const resumes = commands(api, 'resume'); equal('one resume plus one exact receipt retry', resumes.length, 2);
    equal('retry preserves the entire command envelope', resumes[1].body, resumes[0].body);
    equal('retry preserves committed receipt identity', resumes[1].receipt, resumes[0].receipt);
    check('retry is replay, not a second mutation', resumes[1].replayed);
    equal('pause and resume changed only two revisions', api.snapshot().revision, before.revision + 2);
    api.permit('pause'); await button(page, 'pause').click(); await button(page, 'resume').waitFor();
    await until('pause before withdrawal settled', () => button(page, 'withdraw').isEnabled());
    api.permit('withdraw_shared'); await button(page, 'withdraw').click();
    await button(page, 'withdraw').waitFor({ state: 'hidden' });
    const after = api.snapshot();
    equal('withdrawal preserves paused state and exact step', [after.state.activity.id, after.state.activity.status, after.state.activity.step_index, after.state.activity.paused_from],
      [before.state.activity.id, 'paused', before.state.activity.step_index, 'active']);
    equal('withdrawal only abandons current promise', after.shared_participation[1].status, 'abandoned');
    equal('withdrawal retains prior actual record', after.shared_participation[0], before.shared_participation[0]);
    equal('withdrawal never sends completion', commands(api, 'complete_shared').length, 0);
    equal('repeat clicks still only one first pause plus the explicit second pause', commands(api, 'pause').length, 2);
    check('paused withdrawal is entirely localized', !(await details(page).innerText()).match(/\p{Script=Han}/u));
    await capture(page, 'paused-withdrawal');
  });

  await runCase('chat-typing-closes-life', { width: 900 }, async ({ page, api }) => {
    await go(page); await status(page).waitFor();
    await page.getByRole('button', { name: /Open OfferPilot/ }).click();
    const composer = page.locator('#haru-composer'); await composer.fill('Synthetic unsent draft');
    await status(page).waitFor({ state: 'hidden' });
    await composer.fill(''); await page.locator('#haru-chat-window header strong').click();
    await status(page).waitFor();
    await until('real focus/blur reports typing false and claims one eligible bid', () => api.requests.some(request => request.path === '/api/companion/bid' && request.status === 200));
    await page.locator('[data-companion-life] [role="status"]').waitFor();
    await composer.fill('Still unsent');
    await page.locator('[data-companion-life]').waitFor({ state: 'hidden' });
    await composer.fill(''); await page.locator('#haru-chat-window header strong').click();
    await status(page).waitFor(); await openDetails(page);
    await composer.fill('Typing closes an already-open life detail');
    await details(page).waitFor({ state: 'hidden' }); await status(page).waitFor({ state: 'hidden' });
    await capture(page, 'typing-no-life-overlay');
    await composer.fill(''); await page.locator('#haru-chat-window header strong').click(); await status(page).waitFor();
    equal('clearing typing never automatically reopens life details', await status(page).getAttribute('aria-expanded'), 'false');
    equal('bid is not replayed after typing', await page.locator('[data-companion-life] [role="status"]').count(), 0);
    check('real controller emitted typing presence', api.requests.some(request => request.path.endsWith('/presence') && request.body.typing));
    check('typing never submits chat/model commands', !api.requests.some(request => request.method !== 'GET' && !request.path.startsWith('/api/companion/')));
  });

  await runCase('work-and-approval-close-life', {}, async ({ page, api }) => {
    await go(page, 'pilot'); await page.getByText('Synthetic retained conversation', { exact: true }).first().click();
    await until('retained conversation execution is read', () => api.requests.some(request => request.path === '/api/chat/conversations/401/execution'));
    await page.getByRole('button', { name: 'Exit immersive mode and return to the previous page', exact: true }).click();
    await status(page).waitFor(); await openDetails(page);
    await until('conversation-selection loading has settled before work injection', () => {
      const last = api.requests.filter(request => request.path === '/api/companion/presence').at(-1);
      return last && !last.body.work_running && !last.body.awaiting_approval;
    });
    const beforeWork = api.requests.at(-1)?.index ?? 0;
    api.setWork('running');
    await until('a new execution GET observes the injected running state', () => api.requests.some(request => request.index > beforeWork
      && request.method === 'GET' && request.path === '/api/chat/conversations/401/execution' && request.execution?.state === 'running'));
    const observedWork = api.requests.find(request => request.index > beforeWork
      && request.method === 'GET' && request.path === '/api/chat/conversations/401/execution' && request.execution?.state === 'running');
    await until('presence after that execution GET reports work running', () => api.requests.some(request => request.index > observedWork.index
      && request.path === '/api/companion/presence' && request.body.work_running));
    await details(page).waitFor({ state: 'hidden' }); await status(page).waitFor({ state: 'hidden' });
    await capture(page, 'work-suppresses-life');
    api.setWork('idle'); await status(page).waitFor();
    equal('work ending does not reopen details', await status(page).getAttribute('aria-expanded'), 'false');
    await openDetails(page); api.setWork('approval');
    await until('real recovered approval suppresses life via presence', () => api.requests.some(request => request.path.endsWith('/presence') && request.body.awaiting_approval));
    await details(page).waitFor({ state: 'hidden' }); await status(page).waitFor({ state: 'hidden' });
    await capture(page, 'approval-suppresses-life');
    // A later synthetic terminal GET clears pending; no confirm/execute POST exists.
    api.setWork('cleared');
    await until('approval clears through read-only recovery', () => {
      const last = api.requests.filter(request => request.path.endsWith('/presence')).at(-1);
      return last && !last.body.awaiting_approval && !last.body.work_running;
    });
    await details(page).waitFor({ state: 'hidden' });
    check('cleared approval never reopens details', await status(page).count() === 0 || await status(page).getAttribute('aria-expanded') === 'false');
    equal('work and approval never complete shared promise', commands(api, 'complete_shared').length, 0);
    check('no approval, execution or model write was attempted', !api.requests.some(request => request.method !== 'GET' && !request.path.startsWith('/api/companion/')));
  });

  await runCase('hidden-settings-language-off-job', { locale: 'zh-CN' }, async ({ page, api }) => {
    await openCompanionSettings(page);
    await until('settings ready', () => settings(page).getByRole('switch', { name: names.enabled }).isEnabled());
    const before = api.snapshot();
    api.permit('settings'); await settings(page).getByRole('switch', { name: names.hide }).click();
    await status(page).waitFor({ state: 'hidden' });
    await settings(page).getByRole('button', { name: names.view }).click(); await details(page).waitFor();
    await button(page, 'withdraw').waitFor(); await capture(page, 'hidden-settings-zh');
    api.permit('settings'); await settings(page).locator('.ant-select-selector').click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: /^只在我打开时$/ }).click();
    await until('proactivity off receipt applied', () => api.snapshot().settings.proactivity === 'off');
    equal('proactivity off preserves interests and activity', api.snapshot().state, before.state);
    equal('proactivity off preserves participation', api.snapshot().shared_participation, before.shared_participation);
    await page.locator('section[aria-labelledby="language-settings-title"] .ant-select-selector').click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: /^English$/ }).click();
    await page.waitForFunction(() => document.documentElement.lang === 'en-US');
    await button(page, 'withdraw').waitFor();
    check('open Settings detail switches to readable English', !(await details(page).innerText()).match(/\p{Script=Han}/u));
    await details(page).scrollIntoViewIfNeeded(); await capture(page, 'hidden-settings-en');
    await until('settings controls settled before disable', () => settings(page).getByRole('switch', { name: names.enabled }).isEnabled());
    api.permit('settings'); await settings(page).getByRole('switch', { name: names.enabled }).click();
    await details(page).waitFor({ state: 'hidden' });
    equal('disable received and keeps all saved interests/state', [api.snapshot().settings.enabled, api.snapshot().state], [false, before.state]);
    await go(page, 'board'); await page.getByText('Example Labs', { exact: true }).first().waitFor();
    check('synthetic job board remains usable with life off', await page.getByRole('button', { name: 'Add application', exact: true }).first().isEnabled());
    await page.getByRole('button', { name: 'Add application', exact: true }).first().click(); await page.getByRole('dialog').waitFor();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    equal('companion remains off after job view navigation', await status(page).count(), 0);
    await capture(page, 'life-off-job-board');
  });

  await runCase('two-window-stale-cas-refresh', { width: 900 }, async ({ page, context, api }) => {
    await openCompanionSettings(page); const second = await context.newPage(); await openCompanionSettings(second);
    for (const current of [page, second]) await until('both settings windows ready', () => settings(current).getByRole('switch', { name: names.enabled }).isEnabled());
    const initial = api.snapshot(); api.permit('settings');
    await settings(page).getByRole('switch', { name: names.hide }).click();
    await until('first window saves hide status', () => api.snapshot().settings.hide_status);
    const saved = api.snapshot();
    // Second page has the old revision. Only this exact attempted control is expected.
    api.permit('settings');
    await settings(second).getByRole('switch', { name: names.enabled }).click();
    await settings(second).getByRole('alert').filter({ hasText: /Daily life has changed/ }).waitFor();
    await until('stale settings are refreshed, not overwritten', async () => await settings(second).getByRole('switch', { name: names.enabled }).getAttribute('aria-checked') === 'true'
      && await settings(second).getByRole('switch', { name: names.hide }).getAttribute('aria-checked') === 'true');
    const writes = api.requests.filter(request => request.method === 'PUT');
    equal('exactly one accepted save and one stale CAS', writes.map(request => request.status), [200, 409]);
    equal('both windows originally targeted same revision', writes.map(request => request.body.expected_revision), [initial.revision, initial.revision]);
    equal('stale save leaves newer settings/state untouched', [api.snapshot().revision, api.snapshot().settings, api.snapshot().state], [saved.revision, saved.settings, saved.state]);
    check('409 is followed by same-window GET refresh', api.requests.some(request => request.index > writes[1].index && request.page === writes[1].page && request.method === 'GET' && request.path === '/api/companion'));
    await settings(second).scrollIntoViewIfNeeded(); await capture(second, 'stale-window-refreshed');
  });
} catch (error) {
  report.failures.push({ scenario: 'setup', error: error.stack || String(error) }); console.error(error.message);
} finally {
  await browser?.close().catch(error => report.failures.push({ scenario: 'teardown', error: error.message }));
  const allApi = report.scenarios.flatMap(scenario => scenario.apiRequests || []);
  report.requestCounts = { total: report.requests.length, api: allApi.length, byMethodPath: {},
    commands: allApi.filter(request => ['/api/companion/events', '/api/companion/settings'].includes(request.path)).length,
    uniqueCommandIds: new Set(allApi.map(request => request.body?.command_id).filter(Boolean)).size,
    deliberateLostResponses: allApi.filter(request => request.dropResponse).length,
    networkViolations: report.networkViolations.length, fixtureViolations: report.scenarios.reduce((sum, scenario) => sum + (scenario.fixtureViolations?.length || 0), 0) };
  for (const request of allApi) { const key = `${request.method} ${request.path}`; report.requestCounts.byMethodPath[key] = (report.requestCounts.byMethodPath[key] || 0) + 1; }
  report.status = report.failures.length || report.pageErrors.length || report.requestCounts.networkViolations || report.requestCounts.fixtureViolations ? 'failed' : 'passed';
  report.finishedAt = new Date().toISOString();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await writeFile(path.join(output, 'requests.json'), JSON.stringify({ requests: report.requests, api: allApi, failures: report.requestFailures }, null, 2));
  await writeFile(path.join(output, 'report.txt'), [
    `Companion browser gate: ${report.status.toUpperCase()}`, `Commit: ${report.source?.commit || 'unavailable'}`, `Tree: ${report.source?.tree || 'unavailable'}`,
    `Browser: ${report.browser?.engine || 'unavailable'} ${report.browser?.version || ''}; executable: ${report.browser?.executablePath || 'unavailable'}`,
    'Scope: actual production UI + Chromium + local synthetic API only. Not backend persistence or model-quality proof.',
    ...report.scenarios.map(scenario => `${scenario.status.toUpperCase()} ${scenario.name} (${scenario.width}x${scenario.height}, ${scenario.locale})`),
    `Assertions: ${report.assertions.length}; API requests: ${allApi.length}; page errors: ${report.pageErrors.length}; network violations: ${report.networkViolations.length}`,
    `Command attempts: ${report.requestCounts.commands}; unique command IDs: ${report.requestCounts.uniqueCommandIds}; deliberate lost responses: ${report.requestCounts.deliberateLostResponses}`,
    ...report.failures.map(failure => `FAIL ${failure.scenario}: ${failure.error || failure.captureError}`),
    ...report.pageErrors.map(error => `PAGE ERROR ${error.page}: ${error.error}`),
    'See report.json, requests.json, PNG screenshots, DOM HTML and .aria.txt snapshots for complete evidence.',
  ].join('\n') + '\n');
  console.log(`REPORT ${path.join(output, 'report.txt')}`);
  if (report.status !== 'passed') process.exitCode = 1;
}
