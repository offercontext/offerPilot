/** Production UI in real Chromium, with every network request locally fulfilled or rejected. */
import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createImageFixture, image, png } from './fixture-api.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dist = path.join(root, 'dist');
const output = path.resolve(process.env.IMAGE_EVIDENCE_DIR || path.join(root, '../artifacts/image-messages'));
const origin = 'http://127.0.0.1:4175';
const hash = data => createHash('sha256').update(data).digest('hex');
await mkdir(output, { recursive: true });
const report = { kind: 'Real Chromium; production dist; strictly synthetic network', commit: process.env.GITHUB_SHA || null,
  boundaries: ['Not backend persistence or real-model acceptance.', 'No API credentials, provider calls, deployment or publishing.'], dist: {}, cases: [], failures: [] };
for (const file of ['index.html', ...(await readdir(path.join(dist, 'assets'))).filter(name => /\.(js|css)$/.test(name)).map(name => `assets/${name}`)]) report.dist[file] = hash(await readFile(path.join(dist, file)));
let browser;
async function eventually(predicate, label, timeout = 12000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await predicate()) return; await new Promise(resolve => setTimeout(resolve, 50)); }
  assert.fail(label);
}
async function shot(page, record, name) {
  // A visible portal can still be at opacity 0 during its first animation frame.
  await page.evaluate(async () => {
    await document.fonts.ready;
    const paint = () => new Promise(resolve => requestAnimationFrame(resolve));
    const deadline = performance.now() + 5000;
    let stableFrames = 0;
    while (stableFrames < 2) {
      await paint();
      const moving = document.getAnimations().some(animation => animation.effect?.getTiming().iterations !== Infinity
        && (animation.playState === 'running' || animation.pending));
      stableFrames = moving ? 0 : stableFrames + 1;
      if (performance.now() > deadline) throw new Error('Screenshot UI animations did not settle');
    }
  });
  const filename = `${record.name}-${name}.png`;
  await page.screenshot({ path: path.join(output, filename), fullPage: true });
  record.screenshots.push({ file: filename, sha256: hash(await readFile(path.join(output, filename))) });
}
const submissions = api => api.requests.filter(req => req.method === 'POST' && req.path === '/api/pilot/runtime/v1/turns');
async function runCase(name, options, work) {
  const record = { name, status: 'running', checks: [], screenshots: [], blocked: [], missingAssets: [], pageErrors: [] };
  report.cases.push(record);
  const api = createImageFixture(options);
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', reducedMotion: 'reduce', serviceWorkers: 'block' });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
  await context.addInitScript(() => {
    localStorage.setItem('offerpilot.locale', 'zh-CN');
    localStorage.setItem('offerpilot:pilot-mascot-visible', 'true');
    localStorage.setItem('offerpilot:pilot-mascot-animation', 'off');
  });
  await context.routeWebSocket('**/*', socket => { record.blocked.push(socket.url()); socket.close(); });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) { record.blocked.push(url.href); return route.abort('blockedbyclient'); }
    if (url.pathname.startsWith('/api/')) {
      let body; try { body = request.postDataJSON(); } catch { /* Multipart stays synthetic; no content saved. */ }
      const response = await api.handle(request.method(), url.href, body);
      return route.fulfill(response.bytes ? { status: response.status, body: response.bytes, contentType: response.contentType } : { status: response.status, json: response.data });
    }
    if (request.method() !== 'GET') { record.blocked.push(`${request.method()} ${url.href}`); return route.abort(); }
    const filename = path.resolve(dist, url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).slice(1));
    if (!filename.startsWith(dist + path.sep)) { record.blocked.push(url.href); return route.abort(); }
    try {
      const bytes = await readFile(filename);
      return route.fulfill({ body: bytes, contentType: ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.woff2': 'font/woff2', '.wasm': 'application/wasm' })[path.extname(filename)] || 'application/octet-stream' });
    } catch { record.missingAssets.push(url.pathname); return route.fulfill({ status: 404, body: '' }); }
  });
  const page = await context.newPage();
  page.on('pageerror', error => record.pageErrors.push(String(error)));
  page.setDefaultTimeout(12000);
  try {
    await work(page, api, record);
    assert.deepEqual(record.blocked, [], 'No external network attempts');
    assert.deepEqual(record.missingAssets, [], 'All requested production assets exist');
    assert.deepEqual(api.unexpected, [], 'All API requests are allowlisted');
    assert.deepEqual(record.pageErrors, [], 'No uncaught browser errors');
    record.status = 'passed';
  } catch (error) {
    record.status = 'failed'; record.error = String(error.stack || error); report.failures.push({ name, error: record.error });
    await shot(page, record, 'failure').catch(() => {});
  } finally {
    record.requests = api.requests;
    await context.tracing.stop({ path: path.join(output, `${name}-trace.zip`) });
    await context.close();
    await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  }
}
async function openPilot(page) {
  await page.goto(`${origin}/?view=pilot`);
  await page.getByRole('button', { name: '添加图片', exact: true }).waitFor();
}
async function upload(scope) {
  await eventually(async () => await scope.getByRole('button', { name: '添加图片', exact: true }).isEnabled(), 'Image capability ready before file selection');
  await scope.locator('input[type=file][accept="image/png,image/jpeg,image/webp"]').setInputFiles({ name: 'synthetic.png', mimeType: 'image/png', buffer: png });
  const thumbnail = scope.locator('img[alt="图片 1，点击放大"]');
  await thumbnail.waitFor();
  await eventually(() => thumbnail.evaluate(node => node.complete && node.naturalWidth === 160 && node.naturalHeight === 96), 'Synthetic thumbnail decoded');
}
try {
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}), args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  report.browser = browser.version();
  await runCase('pilot-rejection-retry-history', {}, async (page, api, result) => {
    await openPilot(page);
    await upload(page);
    const input = page.locator('textarea:visible').last();
    await input.fill('Synthetic image question');
    const send = page.getByRole('button', { name: '发送', exact: true });
    await eventually(async () => await send.isEnabled(), 'Uploaded image ready');
    await send.click();
    await page.getByRole('button', { name: '重新发送', exact: true }).waitFor();
    assert.equal(await input.inputValue(), 'Synthetic image question');
    assert.equal(await page.getByLabel('待发送图片', { exact: true }).count(), 1);
    assert.deepEqual(submissions(api)[0].body.image_attachment_ids, [image.id]);
    result.checks.push('Rejected admission retains text and image');
    await shot(page, result, 'rejected');
    await page.getByRole('button', { name: '重新发送', exact: true }).click();
    await eventually(async () => (await input.inputValue()) === '' && await page.getByLabel('待发送图片', { exact: true }).count() === 0, 'Admission consumes submitted draft');
    assert.equal(submissions(api)[0].body.request_id, submissions(api)[1].body.request_id);
    assert.deepEqual(submissions(api)[1].body.image_attachment_ids, [image.id]);
    await page.locator('img[alt="图片 1，点击放大"]').waitFor();
    result.checks.push('Retry preserves request identity; accepted image displayed from canonical history');
    await shot(page, result, 'history');
  });
  await runCase('slash-does-not-consume-image', {}, async (page, api, result) => {
    await openPilot(page); await upload(page);
    await page.locator('textarea:visible').last().fill('/');
    await page.getByRole('option').filter({ hasText: '查看投递进度' }).click();
    await eventually(() => submissions(api).length === 1, 'Slash request submitted');
    assert.equal(submissions(api)[0].body.image_attachment_ids, undefined);
    assert.equal(await page.getByLabel('待发送图片', { exact: true }).count(), 1);
    result.checks.push('Real slash selection excludes and retains ready image draft');
    await shot(page, result, 'isolated');
  });
  await runCase('text-only-model-blocks-image', { vision: false }, async (page, api, result) => {
    await openPilot(page);
    assert.equal(await page.getByRole('button', { name: '添加图片', exact: true }).isDisabled(), true);
    await page.getByText('Synthetic model does not accept images', { exact: true }).waitFor();
    assert.equal(api.requests.filter(req => req.method === 'POST').length, 0);
    result.checks.push('Text-only model disables image picker without upload or model requests');
    await shot(page, result, 'disabled');
  });
  await runCase('haru-image-only-preview-escape', {}, async (page, api, result) => {
    await page.goto(`${origin}/?view=applications`);
    await page.getByRole('button', { name: /^打开 OfferPilot 领航员/ }).click();
    const haru = page.locator('#haru-chat-window'); await haru.waitFor();
    await eventually(async () => await haru.getByRole('button', { name: '添加图片', exact: true }).isEnabled(), 'Haru image capability ready');
    await upload(haru);
    await haru.locator('img[alt="图片 1，点击放大"]').click();
    await page.locator('.ant-image-preview-wrap').waitFor({ state: 'visible' });
    await eventually(() => page.locator('.ant-image-preview-img').evaluate(node => node.complete && node.naturalWidth === 160 && node.naturalHeight === 96), 'Portal image decoded');
    await shot(page, result, 'preview-open');
    // Real keyboard targets the preview portal, not the underlying Haru section.
    await page.keyboard.press('Escape');
    await page.locator('.ant-image-preview-wrap').waitFor({ state: 'hidden' });
    assert.equal(await haru.isVisible(), true);
    result.checks.push('Escape closes actual preview portal while keeping Haru open');
    api.acceptNext();
    const send = haru.getByRole('button', { name: '发送消息', exact: true });
    await eventually(async () => await send.isEnabled(), 'Image-only send enabled'); await send.click();
    await eventually(() => submissions(api).length === 1, 'Image-only request submitted');
    assert.equal(submissions(api)[0].body.message, '');
    assert.deepEqual(submissions(api)[0].body.image_attachment_ids, [image.id]);
    await eventually(async () => await haru.getByLabel('待发送图片', { exact: true }).count() === 0, 'Accepted image draft consumed');
    await haru.locator('img[alt="图片 1，点击放大"]').waitFor();
    await shot(page, result, 'accepted-history');
    await haru.locator('textarea').focus(); await page.keyboard.press('Escape');
    await haru.waitFor({ state: 'hidden' });
    result.checks.push('Image-only admission and history; subsequent Escape closes Haru');
  });
} catch (error) { report.failures.push({ name: 'browser-startup', error: String(error.stack || error) }); }
finally {
  await browser?.close();
  report.status = report.failures.length ? 'failed' : 'passed';
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
}
console.log(JSON.stringify({ status: report.status, cases: report.cases.map(({ name, status }) => ({ name, status })), failures: report.failures }, null, 2));
if (report.failures.length) process.exitCode = 1;
