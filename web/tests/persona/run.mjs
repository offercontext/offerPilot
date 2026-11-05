/** Real production-dist UI against the isolated HTTP/FakeModel app only.
 * No fixture API, real provider, user database, desktop packaging or quality claim.
 * This file is prepared for CI; do not launch Chromium in a denied environment.
 */
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { readFile, realpath, readdir, mkdir, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { classifyRequest, ORIGIN } from './network-guard.mjs';
const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const repo = path.resolve(web, '..'), dist = path.join(web, 'dist');
const output = path.resolve(process.argv[2] || path.join(repo, 'artifacts/persona-browser'));
await mkdir(output, { recursive: true });
const report = { kind: 'production-dist / isolated real HTTP backend / FakeModel persona UI', startedAt: new Date().toISOString(),
  assertions: [], failures: [], screenshots: [], captures: [], pageErrors: [], networkViolations: [], requests: [], mutations: [], scenarios: [],
  realProviderCallsAuthorized: false, installedDesktopProof: false };
let browser, context, activePage, dropNextMutationFrom = null;
const mutationBodies = [], pages = new WeakMap();
const sha = value => createHash('sha256').update(value).digest('hex');
const safeUrl = raw => { try { const url = new URL(raw); return `${url.origin}${url.pathname}`; } catch { return 'invalid-url'; } };
const check = (name, condition) => { assert.ok(condition, name); report.assertions.push(name); };
const equal = (name, actual, expected) => { assert.deepEqual(actual, expected, name); report.assertions.push(name); };
const section = page => page.locator('section[aria-labelledby="companion-persona-title"]');
const nameInput = page => section(page).getByRole('textbox', { name: /^(伙伴名字|Companion name)$/ });
const saveButton = page => section(page).getByRole('button', { name: /^(保存人设|Save persona)$/ });
const retryButton = page => section(page).getByRole('button', { name: /^(重试同一保存请求|Retry the same save)$/ });
const button = (page, name) => section(page).getByRole('button', { name });
async function until(name, predicate, timeout = 12000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await predicate()) { report.assertions.push(name); return; } await new Promise(resolve => setTimeout(resolve, 60)); }
  throw new Error(`Timed out: ${name}`);
}
async function api(page, pathname = '/api/companion/persona') {
  // Browser request interception enforces the same strict allowlist on these reads.
  return page.evaluate(async pathname => { const response = await fetch(pathname, { credentials: 'omit' }); if (!response.ok) throw new Error(`read failed: ${response.status}`); return response.json(); }, pathname);
}
async function go(page, view) { activePage = page; await page.goto(`${ORIGIN}/?view=${view}`); await page.locator('#root').waitFor({ state: 'visible' }); }
async function openSettings(page) {
  await go(page, 'settings');
  await page.getByRole('navigation', { name: /^(设置分组|Settings groups)$/ }).getByRole('button', { name: /^(AI 与隐私|AI and privacy)$/ }).click();
  await nameInput(page).waitFor(); await section(page).scrollIntoViewIfNeeded();
  await until('settings load settles', () => nameInput(page).isEnabled());
}
async function confirm(page) { await section(page).getByRole('checkbox', { name: /^(我已核对差异，确认保存人设|I have reviewed the changes and confirm this persona save)$/ }).check(); }
async function waitActiveUi(page, current) {
  await until(`UI shows active revision ${current.revision}`, async () => await nameInput(page).isEnabled()
    && await nameInput(page).inputValue() === current.spec.display_name
    && new RegExp(`(?:当前人设版本：|Current persona revision: )${current.revision}(?:\\D|$)`).test(await section(page).innerText()));
}
async function save(page, { doubleClick = false, status = 200 } = {}) {
  await confirm(page);
  const responsePromise = page.waitForResponse(response => response.url() === `${ORIGIN}/api/companion/persona/mutations` && response.request().method() === 'POST');
  if (doubleClick) await saveButton(page).evaluate(node => { node.click(); node.click(); }); else await saveButton(page).click();
  const response = await responsePromise; equal('persona mutation status', response.status(), status);
  if (status === 200) { const current = await api(page); await waitActiveUi(page, current); return current; }
  return null;
}
async function locale(page, value) {
  await page.locator('section[aria-labelledby="language-settings-title"] .ant-select-selector').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: value === 'en-US' ? /^English$/ : /^简体中文$/ }).click();
  await page.waitForFunction(expected => document.documentElement.lang === expected, value);
}
async function capture(page, label) {
  const stem = label.replace(/[^a-zA-Z0-9_-]/g, '-');
  const capture = { name: label, screenshot: `${stem}.png`, dom: `${stem}.html`, aria: `${stem}.aria.txt` };
  await writeFile(path.join(output, capture.dom), await page.content());
  await writeFile(path.join(output, capture.aria), await page.locator('body').ariaSnapshot());
  await page.screenshot({ path: path.join(output, capture.screenshot), fullPage: true, animations: 'disabled' });
  report.screenshots.push(capture.screenshot); report.captures.push(capture);
}
async function step(name, fn) {
  const scenario = { name, status: 'running' }; report.scenarios.push(scenario);
  try { await fn(); scenario.status = 'passed'; }
  catch (error) { scenario.status = 'failed'; report.failures.push({ scenario: name, error: String(error.message) }); throw error; }
}
async function distFingerprint(directory) {
  const entries = [];
  async function walk(dir) { for (const entry of (await readdir(dir, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const filename = path.join(dir, entry.name);
    if (entry.isDirectory()) await walk(filename);
    else if (entry.isFile()) entries.push([path.relative(directory, filename), sha(await readFile(filename))]);
    else throw new Error('Non-regular build artifact');
  } }
  await walk(directory); return { files: entries.length, sha256: sha(JSON.stringify(entries)) };
}
try {
  report.commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim();
  report.dirty = Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: repo, encoding: 'utf8' }).trim());
  report.build = await distFingerprint(dist);
  const healthResponse = await fetch(`${ORIGIN}/__persona_browser__/health`, { redirect: 'error', signal: AbortSignal.timeout(5000) });
  check('isolated HTTP app health status', healthResponse.ok);
  const health = await healthResponse.json();
  for (const flag of ['synthetic', 'fake_model', 'isolated_data', 'outbound_blocked']) check(`server attests ${flag}`, health[flag] === true);
  const indexResponse = await fetch(`${ORIGIN}/`, { redirect: 'error', signal: AbortSignal.timeout(5000) });
  equal('HTTP app serves this production build', sha(Buffer.from(await indexResponse.arrayBuffer())), sha(await readFile(path.join(dist, 'index.html'))));
  report.server = Object.fromEntries(['synthetic', 'fake_model', 'isolated_data', 'outbound_blocked'].map(key => [key, health[key]]));
  const executablePath = process.env.CHROMIUM_PATH;
  browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true, args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run', '--no-default-browser-check'] });
  report.browser = { engine: 'Chromium', version: browser.version(), source: executablePath ? 'CHROMIUM_PATH' : 'Playwright managed Chromium' };
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block', reducedMotion: 'reduce' });
  await context.addInitScript(origin => {
    if (location.origin !== origin) return;
    if (!localStorage.getItem('offerpilot.locale')) localStorage.setItem('offerpilot.locale', 'zh-CN');
    localStorage.setItem('offerpilot:pilot-mascot-visible', 'true');
    localStorage.setItem('offerpilot:pilot-mascot-animation', 'off');
  }, ORIGIN);
  context.on('page', page => {
    pages.set(page, `page-${report.requests.length}-${Date.now()}`); page.setDefaultTimeout(12000);
    page.on('pageerror', error => report.pageErrors.push({ page: pages.get(page), error: error.message }));
  });
  await context.routeWebSocket('**/*', socket => { report.networkViolations.push({ kind: 'websocket', url: safeUrl(socket.url()) }); socket.close(); });
  await context.route('**/*', async route => {
    try {
    const request = route.request(); let page;
    try { page = request.frame().page(); } catch { /* Worker requests still face the same allowlist. */ }
    let body = null;
    try { if (request.postData()) body = request.postDataJSON(); } catch { /* Invalid payload fails the guard. */ }
    const entry = { method: request.method(), url: safeUrl(request.url()), page: pages.get(page) || 'worker' };
    report.requests.push(entry);
    const classification = classifyRequest(request.method(), request.url(), body, dist);
    // A fresh synthetic context must never acquire a real auth credential.
    const headers = await request.allHeaders();
    if (headers.authorization || headers['proxy-authorization']) classification.blocked = 'unexpected_auth_header';
    if (classification.blocked) { report.networkViolations.push({ ...entry, reason: classification.blocked }); return route.abort('blockedbyclient'); }
    if (classification.filename) {
      try { const resolved = await realpath(classification.filename); check('static asset stays inside production dist', resolved.startsWith(await realpath(dist) + path.sep)); }
      catch { report.networkViolations.push({ ...entry, reason: 'unknown_or_escaped_static_asset' }); return route.abort('blockedbyclient'); }
    }
    if (classification.mutation) {
      mutationBodies.push(structuredClone(body));
      report.mutations.push({ command_id: body.command_id, expected_revision: body.expected_revision, operation: body.operation, body_sha256: sha(JSON.stringify(body)) });
      if (page === dropNextMutationFrom) {
        dropNextMutationFrom = null;
        const response = await route.fetch({ maxRedirects: 0 });
        equal('lost response was committed by the real HTTP backend', response.status(), 200);
        const receipt = await response.json(); report.droppedReceipt = { revision: receipt.revision, content_hash: receipt.content_hash };
        return route.abort('connectionreset');
      }
    }
    return await route.continue();
    } catch (error) {
      report.failures.push({ routeError: String(error.message) });
      await route.abort('blockedbyclient').catch(() => {});
    }
  });
  const a = await context.newPage(), b = await context.newPage(); activePage = a;
  let initial;
  await step('presets-are-drafts-cancel-navigation-refresh', async () => {
    await openSettings(a); initial = await api(a); equal('isolated initial persona revision', initial.revision, 0);
    const before = report.mutations.length;
    for (const name of [/^(官方默认|Official default)$/, /^(温和简洁|Gentle and brief)$/, /^(冷静直接|Calm and direct)$/]) {
      await button(a, name).click(); equal('preset makes no mutation', report.mutations.length, before);
    }
    await button(a, /^(取消编辑|Cancel editing)$/).click(); equal('cancel restores persisted draft', await nameInput(a).inputValue(), initial.spec.display_name);
    await nameInput(a).fill('Unsaved QA'); await go(a, 'dashboard'); await openSettings(a);
    equal('leaving settings cancels unsaved name', await nameInput(a).inputValue(), initial.spec.display_name);
    await a.reload(); await nameInput(a).waitFor(); equal('refresh preserves backend revision', (await api(a)).revision, initial.revision);
    equal('draft controls never posted', report.mutations.length, before); await capture(a, 'persona-default-zh');
  });
  await step('explicit-save-empty-activities-repeat-click', async () => {
    await button(a, /^(温和简洁|Gentle and brief)$/).click(); await nameInput(a).fill('Nova QA');
    for (const checkbox of await section(a).locator('fieldset input[type="checkbox"]').all()) await checkbox.uncheck();
    check('save cannot bypass confirmation', await saveButton(a).isDisabled());
    const before = report.mutations.length; const saved = await save(a, { doubleClick: true });
    equal('rapid repeated save creates one mutation', report.mutations.length, before + 1);
    equal('first save appends exactly one revision', saved.revision, initial.revision + 1); equal('empty activity whitelist persists', saved.spec.enabled_activity_templates, []);
    await capture(a, 'persona-saved-empty-activities');
  });
  await step('locale-and-both-entry-names', async () => {
    const before = report.mutations.length;
    await locale(a, 'en-US'); await section(a).getByRole('heading', { name: 'Companion persona' }).waitFor();
    await section(a).getByText('Illustrative example, not actual model output', { exact: true }).waitFor();
    equal('locale leaves configured name unchanged', await nameInput(a).inputValue(), 'Nova QA'); await capture(a, 'persona-en');
    await go(a, 'dashboard'); const mascot = a.locator('aside[aria-label="Nova QA assistant"], aside[aria-label="Nova QA 助手"]');
    await mascot.waitFor(); await mascot.locator('button[aria-expanded]').first().click();
    const dialog = a.locator('#haru-chat-window'); await dialog.waitFor(); equal('Haru window displays shared name', await dialog.locator('header strong').innerText(), 'Nova QA');
    await capture(a, 'persona-haru-entry'); await dialog.getByRole('button', { name: /^Close Nova QA chat$/ }).click(); await dialog.waitFor({ state: 'hidden' });
    await go(a, 'pilot'); await a.getByText('Nova QA · OfferPilot assistant', { exact: true }).waitFor(); await capture(a, 'persona-pilot-entry');
    await openSettings(a); await locale(a, 'zh-CN'); await section(a).getByRole('heading', { name: '伙伴人设' }).waitFor();
    equal('locale and entry navigation perform no persona write', report.mutations.length, before);
  });
  await step('stale-tab-cas-preserves-draft', async () => {
    await openSettings(a); await openSettings(b);
    await nameInput(a).fill('Alpha QA'); await nameInput(b).fill('Beta draft'); await save(a); await save(b, { status: 409 });
    await button(b, /^(读取最新版并保留草稿|Load latest and keep draft)$/).waitFor(); equal('CAS rejection preserves draft', await nameInput(b).inputValue(), 'Beta draft');
    const before = report.mutations.length; await button(b, /^(读取最新版并保留草稿|Load latest and keep draft)$/).click();
    await until('CAS refresh unlocks draft', () => nameInput(b).isEnabled()); equal('CAS refresh retains draft', await nameInput(b).inputValue(), 'Beta draft');
    check('CAS refresh requires new confirmation', await saveButton(b).isDisabled()); equal('CAS refresh does not retry mutation', report.mutations.length, before);
    await capture(b, 'persona-cas-preserved-draft'); await button(b, /^(取消编辑|Cancel editing)$/).click(); equal('cancel uses newest current version', await nameInput(b).inputValue(), 'Alpha QA');
  });
  await step('lost-committed-response-refresh-same-command-new-active', async () => {
    await openSettings(a); await nameInput(a).fill('Lost response QA'); await confirm(a); dropNextMutationFrom = a;
    const index = mutationBodies.length; await saveButton(a).click(); await retryButton(a).waitFor();
    const original = mutationBodies[index]; check('unknown result locks edits', await nameInput(a).isDisabled());
    await openSettings(b); equal('lost response really committed before retry', (await api(b)).spec.display_name, 'Lost response QA');
    await nameInput(b).fill('Newer winner QA'); const newer = await save(b);
    await a.reload(); await retryButton(a).waitFor(); check('unknown save survives refresh without auto-resend', report.mutations.length === index + 2);
    const responsePromise = a.waitForResponse(response => response.url() === `${ORIGIN}/api/companion/persona/mutations` && response.request().method() === 'POST');
    await retryButton(a).click(); equal('receipt replay status', (await responsePromise).status(), 200); await waitActiveUi(a, newer);
    equal('lost-response retry reuses exact command and body', mutationBodies[index + 2], original);
    equal('old receipt cannot regress current backend', (await api(a)).revision, newer.revision);
    equal('old receipt UI reads newer active name', await nameInput(a).inputValue(), 'Newer winner QA'); await capture(a, 'persona-replayed-receipt-new-active');
  });
  await step('restore-appends-revision-does-not-delete-history', async () => {
    const current = await api(a), before = await api(a, '/api/companion/persona/revisions');
    await button(a, /^(查看人设历史|View persona history)$/).click();
    const item = section(a).locator('li').filter({ hasText: /(?:人设版本 0|Persona revision 0)\s*·/ }); await item.getByRole('button').click();
    equal('restore only loads a draft', (await api(a)).revision, current.revision); equal('restored draft has official name', await nameInput(a).inputValue(), initial.spec.display_name);
    const restored = await save(a); equal('restore appends a fresh revision', restored.revision, current.revision + 1); equal('restore uses original content', restored.spec, initial.spec);
    const after = await api(a, '/api/companion/persona/revisions'); equal('restore retains every old revision', after.revisions.length, before.revisions.length + 1);
    equal('old revisions remain unchanged', after.revisions.filter(item => item.revision <= current.revision), before.revisions);
    await a.reload(); await nameInput(a).waitFor(); await waitActiveUi(a, restored); await capture(a, 'persona-restored-after-reload');
  });
  equal('no browser network guard violations', report.networkViolations, []); equal('no browser page errors', report.pageErrors, []);
  equal('production build remained unchanged', await distFingerprint(dist), report.build);
} catch (error) {
  report.failures.push({ error: String(error.message) });
  if (activePage && !activePage.isClosed()) await capture(activePage, 'failure').catch(cause => report.failures.push({ captureError: String(cause.message) }));
} finally {
  await context?.close().catch(() => {}); await browser?.close().catch(() => {});
  report.finishedAt = new Date().toISOString(); report.status = report.failures.length || report.networkViolations.length || report.pageErrors.length ? 'failed' : 'passed';
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(`Persona browser gate ${report.status}; ${report.assertions.length} assertions; evidence ${output}`);
  if (report.status !== 'passed') process.exitCode = 1;
}
