import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { captureFailureScreenshot, collectLayoutState, messageKind, observeLayoutFailure, resourceName } from '../../../web/tests/desktop-layout/failure-evidence.mjs';

const requireWeb = createRequire(new URL('../../../web/package.json', import.meta.url));
const { JSDOM } = requireWeb('jsdom');
const secret = 'DO_NOT_PERSIST_SYNTHETIC_SECRET_123';
const html = `<main id="root" class="op-app-shell ant-${secret}" data-private="${secret}"><output data-layout-result="pending">${secret}</output><input value="${secret}"><a href="https://example.test/?token=${secret}">${secret}</a><div class="ant-table-content"></div><script>window.privateValue='${secret}'</script><template><span data-secret="${secret}">${secret}</span></template></main>`;

async function timeoutProbe(output) {
  const requireHelper = createRequire(new URL('../package.json', import.meta.url));
  const { chromium } = requireHelper('playwright-core');
  const browser = await chromium.launch({ channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 900, height: 689 } });
  page.setDefaultTimeout(15000);
  const evidence = observeLayoutFailure(page, output, 'intentional-timeout');
  try {
    await page.setContent(html);
    await page.evaluate(value => { console.error(value); setTimeout(() => { throw new Error(value); }, 0); }, secret);
    // Same predicate and timeout as the real harness, on a controlled pending fixture.
    await page.waitForFunction(() => {
      const state = document.querySelector('[data-layout-result]')?.getAttribute('data-layout-result');
      return state && state !== 'pending';
    });
    throw new Error('Negative fixture unexpectedly became ready');
  } catch (error) {
    await evidence.capture('readiness-wait', error);
    await captureFailureScreenshot(page, output, 'intentional-timeout');
    throw error;
  } finally { evidence.dispose(); await browser.close(); }
}

if (process.argv[2] === '--intentional-timeout') {
  await timeoutProbe(process.argv[3]);
} else {
  test('error kinds never retain arbitrary messages or credentials', () => {
    assert.equal(messageKind(`TimeoutError: Timeout 15000ms exceeded ${secret}`), 'timeout');
    assert.equal(messageKind(`Invalid hook call ${secret}`), 'react-hook-error');
    assert.equal(messageKind(secret), 'unclassified');
    assert.equal(resourceName(`http://127.0.0.1:5174/src/main.tsx?token=${secret}#${secret}`), '/src/main.tsx');
    assert.equal(resourceName(`http://127.0.0.1:5174/api/tokens/${secret}`), '[other-loopback]');
    assert.equal(resourceName(`https://example.test/${secret}`), '[non-loopback]');
    for (const category of ['assets', 'src', 'vendor', 'node_modules', 'tests/desktop-layout']) {
      const resource = resourceName(`http://127.0.0.1:5174/${category}/${secret}?token=${secret}`);
      assert.ok(!resource.includes(secret));
      assert.match(resource, /:\[[a-f0-9]{12}\]$/);
    }
  });

  test('DOM evidence keeps readiness structure while removing text, values and attributes', () => {
    const dom = new JSDOM(html);
    const savedDocument = globalThis.document;
    const savedFilter = globalThis.NodeFilter;
    try {
      globalThis.document = dom.window.document;
      globalThis.NodeFilter = dom.window.NodeFilter;
      const state = collectLayoutState();
      assert.equal(state.rootPresent, true);
      assert.equal(state.layoutResult, 'pending');
      assert.equal(state.tablePresent, true);
      assert.ok(state.dom.includes('id="root"'));
      assert.ok(state.dom.includes('data-layout-result'));
      assert.ok(!JSON.stringify(state).includes(secret));
      assert.ok(!state.dom.includes('<script'));
      assert.ok(!state.dom.includes('href='));
      assert.ok(!state.dom.includes('value='));
    } finally {
      globalThis.document = savedDocument;
      globalThis.NodeFilter = savedFilter;
      dom.window.close();
    }
  });

  test('bounded failure evidence records error events and pending resources without changing the original error', async () => {
    const output = await mkdtemp(path.join(os.tmpdir(), 'layout-evidence-test-'));
    const page = new EventEmitter();
    page.evaluate = async () => ({ layoutResult: 'pending', rootPresent: true, dom: '<body><div id="root"></div></body>' });
    const observer = observeLayoutFailure(page, output, 'negative', { maxEvents: 2 });
    const original = new Error(`TimeoutError: Timeout 15000ms exceeded`);
    try {
      for (let i = 0; i < 4; i++) page.emit('pageerror', new Error(secret));
      page.emit('console', { type: () => 'error', text: () => secret, location: () => ({ url: `http://127.0.0.1:5174/src/main.tsx?${secret}` }) });
      page.emit('request', { url: () => `http://127.0.0.1:5174/api/${secret}`, resourceType: () => 'fetch' });
      const files = await observer.capture('readiness-wait', original);
      const raw = await readFile(path.join(output, files.diagnostic), 'utf8');
      const report = JSON.parse(raw);
      assert.equal(report.errorKind, 'timeout');
      assert.equal(report.events.length, 2);
      assert.equal(report.droppedEvents, 3);
      assert.equal(report.state.layoutResult, 'pending');
      assert.ok(!raw.includes(secret));
      assert.equal(original.message, 'TimeoutError: Timeout 15000ms exceeded');
    } finally { observer.dispose(); await rm(output, { recursive: true, force: true }); }
    assert.equal(page.listenerCount('pageerror'), 0);
  });

  test('disposing evidence removes both page and WebSocket child listeners', () => {
    const page = new EventEmitter();
    const sockets = [new EventEmitter(), new EventEmitter()];
    const observer = observeLayoutFailure(page, '.', 'negative');
    for (const socket of sockets) {
      page.emit('websocket', socket);
      assert.equal(socket.listenerCount('framereceived'), 1);
      assert.equal(socket.listenerCount('socketerror'), 1);
    }
    observer.dispose();
    observer.dispose(); // Disposal remains safe after the original failure path.
    assert.deepEqual(page.eventNames(), []);
    for (const socket of sockets) assert.deepEqual(socket.eventNames(), []);
  });

  test('unavailable DOM or unwritable evidence cannot replace the original failure', async () => {
    const output = await mkdtemp(path.join(os.tmpdir(), 'layout-evidence-unavailable-'));
    const page = new EventEmitter();
    page.evaluate = async () => { throw new Error(secret); };
    const blocked = path.join(output, 'file');
    await writeFile(blocked, 'occupied');
    const observer = observeLayoutFailure(page, blocked, 'negative');
    try {
      const result = await observer.capture('readiness-wait', new Error('TimeoutError'));
      assert.deepEqual(result.diagnosticCaptureErrors, ['dom-unavailable', 'evidence-write-failed']);
    } finally { observer.dispose(); await rm(output, { recursive: true, force: true }); }
  });

  test('a rejected screenshot cannot replace the original timeout', async () => {
    const original = new Error('TimeoutError: Timeout 15000ms exceeded');
    const failure = { status: 'failed', error: String(original) };
    const page = { screenshot: async () => { throw new Error(secret); } };
    Object.assign(failure, await captureFailureScreenshot(page, '.', 'negative'));
    assert.equal(failure.status, 'failed');
    assert.equal(failure.error, String(original));
    assert.equal(failure.screenshotUnavailable, true);
  });

  test('a wedged renderer cannot leave failure capture pending indefinitely', async () => {
    const output = await mkdtemp(path.join(os.tmpdir(), 'layout-evidence-stalled-'));
    const page = new EventEmitter();
    page.evaluate = () => new Promise(() => {});
    const observer = observeLayoutFailure(page, output, 'negative', { captureTimeoutMs: 25 });
    try {
      const result = await observer.capture('readiness-wait', new Error('TimeoutError'));
      assert.deepEqual(result.diagnosticCaptureErrors, ['dom-capture-timeout']);
      const report = JSON.parse(await readFile(path.join(output, result.diagnostic), 'utf8'));
      assert.equal(report.errorKind, 'timeout');
      assert.equal(report.state, null);
    } finally { observer.dispose(); await rm(output, { recursive: true, force: true }); }
  });

  test('real browser timeout preserves artifacts and exits unsuccessfully at the unchanged 15-second limit', { skip: process.env.LAYOUT_BROWSER_NEGATIVE !== '1', timeout: 45000 }, async t => {
    const output = process.env.LAYOUT_NEGATIVE_EVIDENCE_DIR || await mkdtemp(path.join(os.tmpdir(), 'layout-timeout-negative-'));
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--intentional-timeout', output], { stdio: ['ignore', 'pipe', 'pipe'] });
    t.after(async () => {
      if (child.exitCode === null && child.signalCode === null) child.kill();
      if (!process.env.LAYOUT_NEGATIVE_EVIDENCE_DIR) await rm(output, { recursive: true, force: true });
    });
    let log = '';
    child.stdout.on('data', data => { log += data; });
    child.stderr.on('data', data => { log += data; });
    const result = await new Promise((resolve, reject) => { child.once('error', reject); child.once('close', (code, signal) => resolve({ code, signal })); });
    assert.equal(result.code, 1, `Expected original timeout failure; got ${JSON.stringify(result)}`);
    assert.equal(result.signal, null);
    assert.match(log, /Timeout 15000ms exceeded/);
    const reportText = await readFile(path.join(output, 'intentional-timeout-failed.json'), 'utf8');
    const report = JSON.parse(reportText);
    assert.equal(report.errorKind, 'timeout');
    assert.equal(report.stage, 'readiness-wait');
    assert.equal(report.state.layoutResult, 'pending');
    assert.equal(report.state.rootPresent, true);
    assert.ok(report.events.some(event => event.type === 'console'));
    assert.ok(report.events.some(event => event.type === 'pageerror'));
    assert.ok(!reportText.includes(secret));
    assert.ok(!(await readFile(path.join(output, 'intentional-timeout-failed.dom.html'), 'utf8')).includes(secret));
    assert.ok((await readFile(path.join(output, 'intentional-timeout-failed.png'))).length > 0);
    // The cleanup hook retains only synthetic evidence requested by the workflow.
  });
}
