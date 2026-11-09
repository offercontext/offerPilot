/** Actual production UI in Chromium with strictly intercepted synthetic responses.
 * No backend, model, provider, credentials or real user data are used.
 */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFile, realpath, readdir, mkdir, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright-core';
import { createTrainingFixture, EVIDENCE_PATH, ORIGINAL_ANSWER, ORIGIN } from './fixture-api.mjs';
import { classifyRequest } from './network-guard.mjs';

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const repo = path.resolve(web, '..'), dist = path.join(web, 'dist');
const output = path.resolve(process.argv[2] || path.join(repo, 'artifacts/training-evidence-browser'));
await mkdir(output, { recursive: true });
const hash = data => createHash('sha256').update(data).digest('hex');
const report = { kind: 'production-web Chromium synthetic training-evidence acceptance', backendPersistenceProof: false,
  realModelQualityProof: false, startedAt: new Date().toISOString(), assertions: [], cases: [], failures: [], requests: [], networkViolations: [], pageErrors: [], console: [], screenshots: [] };
let browser;
const labels = {
  practice: /^(复盘重点练习|Review-focused practice)$/,
  panel: /^(训练证据|Training evidence)$/,
  open: /^(选择这个技能并查看记录|Select this skill and view records)$/,
  save: /^(确认保存训练证据|Confirm and save training evidence)$/,
  correct: /^(更正记录|Correct record)$/,
  saveCorrection: /^(确认保存更正|Confirm and save correction)$/,
  scenario: /^(练习情境|Practice scenario)$/,
  assistance: /^(这次如何完成回答|How you answered this time)$/,
  answer: /^(本次回答|This answer)$/,
  next: /^(换个场景试一次|Try another scenario)$/,
  retry: /^(使用原操作重试|Retry the original action)$/,
  withdraw: /^(撤回训练记录|Withdraw training record)$/,
  confirmWithdrawal: /^(确认撤回训练记录|Confirm withdrawal)$/,
  reload: /^(重新读取|Reload)$/,
};
const button = (scope, key) => scope.getByRole('button', { name: labels[key], exact: true });
const check = (name, condition) => { assert.ok(condition, name); report.assertions.push(name); };
async function until(name, predicate) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (await predicate()) { report.assertions.push(name); return; }
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  throw new Error(`Timed out: ${name}`);
}
async function capture(page, panel, stem) {
  await panel.scrollIntoViewIfNeeded();
  await page.evaluate(() => document.fonts.ready);
  await writeFile(path.join(output, `${stem}.html`), await page.content());
  await writeFile(path.join(output, `${stem}.aria.txt`), await page.locator('body').ariaSnapshot());
  await page.screenshot({ path: path.join(output, `${stem}.png`), fullPage: true, animations: 'disabled' });
  report.screenshots.push({ file: `${stem}.png`, sha256: hash(await readFile(path.join(output, `${stem}.png`))) });
  // Element capture includes the tall panel, not just the shell's internally scrolled viewport.
  await panel.screenshot({ path: path.join(output, `${stem}-panel.png`), animations: 'disabled' });
  report.screenshots.push({ file: `${stem}-panel.png`, sha256: hash(await readFile(path.join(output, `${stem}-panel.png`))) });
}
async function exercise(locale, width) {
  const api = createTrainingFixture(), result = { locale, width, status: 'running', requests: [], fixtureViolations: [] };
  report.cases.push(result);
  const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
  let page, panel;
  try {
    await context.addInitScript(({ origin, locale }) => {
      if (location.origin !== origin) return;
      localStorage.setItem('offerpilot.locale', locale);
      localStorage.setItem('offerpilot:pilot-mascot-visible', 'false');
      localStorage.setItem('offerpilot:pilot-mascot-animation', 'off');
    }, { origin: ORIGIN, locale });
    context.on('page', target => {
      target.setDefaultTimeout(10000);
      target.on('pageerror', error => report.pageErrors.push({ locale, message: error.message }));
      target.on('console', message => { if (['error', 'warning'].includes(message.type())) report.console.push({ locale, type: message.type(), text: message.text() }); });
    });
    await context.route('**/*', async route => {
      const request = route.request(), target = classifyRequest(request.method(), request.url(), dist);
      report.requests.push({ locale, method: request.method(), url: request.url() });
      if (target.blocked) { report.networkViolations.push({ locale, url: request.url(), reason: target.blocked }); return route.abort('blockedbyclient'); }
      if (target.api) {
        let body;
        try { body = request.postDataJSON() ?? undefined; } catch { report.networkViolations.push({ locale, reason: 'invalid_json' }); return route.abort(); }
        const response = api.handle(request.method(), request.url(), body, request.headers());
        if (response.dropResponse) return route.abort('connectionreset');
        return route.fulfill({ status: response.status, json: response.data });
      }
      try {
        const filename = await realpath(target.filename);
        if (!filename.startsWith(await realpath(dist) + path.sep)) throw new Error('asset_symlink_escape');
        const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.wasm': 'application/wasm' }[path.extname(filename)] || 'application/octet-stream';
        return route.fulfill({ status: 200, body: await readFile(filename), contentType,
          headers: { 'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self'; worker-src 'self' blob:; form-action 'none'; object-src 'none'" } });
      } catch (error) {
        report.networkViolations.push({ locale, url: request.url(), reason: `local_asset:${error.message}` });
        return route.fulfill({ status: 404, body: 'Missing or unsafe synthetic asset' });
      }
    });
    await context.routeWebSocket('**/*', socket => { report.networkViolations.push({ locale, type: 'WebSocket', url: socket.url() }); socket.close(); });
    page = await context.newPage();
    await page.goto(`${ORIGIN}/?view=interview`);
    await button(page.getByTestId('free-practice-workspace'), 'practice').click();
    const workspace = page.getByTestId('review-focus-practice');
    await workspace.locator('details summary').filter({ hasText: 'SYNTHETIC · Idempotency practice' }).click();
    panel = workspace.getByRole('region', { name: labels.panel });
    await panel.waitFor();
    check(`${locale}: opening completed history does not read or write training evidence`, !api.requests.some(request => request.path === EVIDENCE_PATH));
    await button(panel, 'open').click();
    await panel.getByRole('combobox', { name: labels.assistance }).waitFor();
    check(`${locale}: history assistance remains unknown`, await panel.getByRole('combobox', { name: labels.assistance }).inputValue() === 'unknown');
    check(`${locale}: original answer is read-only`, await panel.getByRole('textbox', { name: labels.answer }).evaluate(element => element.readOnly));
    check(`${locale}: explicit selection is read-only`, api.requests.filter(request => request.path === EVIDENCE_PATH && request.method === 'POST').length === 0);
    await capture(page, panel, `${locale}-01-explicit-selection`);
    await button(panel, 'save').click();
    await until(`${locale}: first evidence is saved once`, () => api.state().revision === 1 && api.state().records.length === 1);
    await panel.locator('#training-808-1').waitFor();
    check(`${locale}: unknown is not inferred into an ability claim`, api.state().records[0].assistance === 'unknown');
    await button(panel.locator('#training-808-1'), 'correct').click();
    await panel.getByRole('combobox', { name: labels.assistance }).selectOption('hinted');
    await button(panel, 'saveCorrection').click();
    await until(`${locale}: correction preserves original answer`, () => api.state().revision === 2 && api.state().records[0].assistance === 'hinted' && api.state().records[0].answer === ORIGINAL_ANSWER);
    await until(`${locale}: correction UI settles`, () => panel.getAttribute('aria-busy').then(value => value === 'false'));
    await button(panel, 'next').click();
    check(`${locale}: next exercise changes the scenario`, await panel.getByRole('combobox', { name: labels.scenario }).inputValue() === 'retry_timeout');
    await panel.getByRole('textbox', { name: labels.answer }).fill('SYNTHETIC retest: read the committed receipt and replay the same request key without writing again.');
    await panel.getByRole('combobox', { name: labels.assistance }).selectOption('independent');
    api.dropNextResponse();
    await button(panel, 'save').click();
    await button(panel, 'retry').waitFor();
    check(`${locale}: uncertain result freezes editing`, await panel.locator('fieldset').evaluate(element => element.disabled));
    const firstCommand = api.requests.filter(request => request.path === EVIDENCE_PATH && request.method === 'POST').at(-1).body;
    await capture(page, panel, `${locale}-02-unknown-result`);
    await button(panel, 'retry').click();
    await panel.locator('#training-808-2').waitFor();
    await until(`${locale}: retry UI settles`, () => panel.getAttribute('aria-busy').then(value => value === 'false'));
    const replayCommand = api.requests.filter(request => request.path === EVIDENCE_PATH && request.method === 'POST').at(-1).body;
    check(`${locale}: retry preserves exact original command`, JSON.stringify(firstCommand) === JSON.stringify(replayCommand));
    check(`${locale}: no duplicate evidence on unknown-result retry`, api.state().revision === 3 && api.state().records.length === 2);
    check(`${locale}: comparison links both evidence records`, await panel.locator('a[href="#training-808-1"]').count() === 1 && await panel.locator('a[href="#training-808-2"]').count() === 1);
    await capture(page, panel, `${locale}-03-comparison`);
    api.pauseSource();
    await button(panel, 'reload').click();
    await until(`${locale}: source pause removes recording and advice`, async () => await panel.locator('fieldset').count() === 0 && await button(panel, 'next').count() === 0);
    check(`${locale}: correction is disabled when source is stale`, await button(panel.locator('#training-808-2'), 'correct').isDisabled());
    await button(panel.locator('#training-808-2'), 'withdraw').click();
    await button(panel.locator('#training-808-2'), 'confirmWithdrawal').click();
    await until(`${locale}: withdrawal remains available after source pause`, () => api.state().records[1].withdrawn && api.state().records[1].answer === '');
    await until(`${locale}: withdrawn record UI no longer contains the answer`, async () => !(await panel.locator('#training-808-2').innerText()).includes('SYNTHETIC retest:'));
    check(`${locale}: original completed plan answer is unchanged`, ORIGINAL_ANSWER === api.handle('GET', `${ORIGIN}/api/interview-practice/plans`).data[0].response_text);
    await capture(page, panel, `${locale}-04-source-paused-withdrawn`);
    if (locale === 'en-US') check('English authored panel text is localized', !/\p{Script=Han}/u.test(await panel.innerText()));
    const bounds = await panel.evaluate(node => ({ left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right, width: innerWidth }));
    check(`${locale}: panel fits desktop viewport horizontally`, bounds.left >= -1 && bounds.right <= bounds.width + 1);
    check(`${locale}: fixture has no unknown requests`, api.violations.length === 0);
    result.status = 'passed';
  } catch (error) {
    result.status = 'failed'; report.failures.push({ locale, error: error.stack || String(error) });
    if (page && panel) await capture(page, panel, `${locale}-failure`).catch(captureError => report.failures.push({ locale, captureError: String(captureError) }));
  } finally {
    result.requests = api.requests; result.fixtureViolations = api.violations; result.finalState = api.state();
    await context.close();
  }
}
try {
  report.source = { commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim(), tree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { cwd: repo, encoding: 'utf8' }).trim(), worktree: execFileSync('git', ['status', '--porcelain'], { cwd: repo, encoding: 'utf8' }).trim(), lockSha256: hash(await readFile(path.join(web, 'package-lock.json'))), dist: {} };
  for (const file of ['index.html', ...(await readdir(path.join(dist, 'assets'))).filter(file => /\.(js|css)$/.test(file)).map(file => `assets/${file}`)]) report.source.dist[file] = hash(await readFile(path.join(dist, file)));
  browser = await chromium.launch({ headless: true, args: ['--disable-background-networking'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  report.browser = { engine: 'Chromium', version: browser.version(), executablePath: process.env.CHROMIUM_PATH || chromium.executablePath() };
  await exercise('zh-CN', 1280);
  await exercise('en-US', 1000);
} catch (error) { report.failures.push({ error: error.stack || String(error) }); }
finally {
  await browser?.close();
  report.finishedAt = new Date().toISOString();
  report.status = report.failures.length || report.pageErrors.length || report.networkViolations.length || report.cases.some(item => item.status !== 'passed' || item.fixtureViolations.length) ? 'failed' : 'passed';
  if (report.cases.length !== 2) report.status = 'failed';
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await writeFile(path.join(output, 'report.txt'), [`Training evidence Chromium: ${report.status}`, `Commit: ${report.source?.commit}`, 'Scope: actual production UI with synthetic local transport. Not backend persistence, model quality, or installed desktop acceptance.', ...report.assertions, ...report.failures.map(failure => JSON.stringify(failure))].join('\n') + '\n');
  console.log(`REPORT ${path.join(output, 'report.json')}`);
  if (report.status !== 'passed') process.exitCode = 1;
}
