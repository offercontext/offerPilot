import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { ROOTS, ROOT_CASES, ROOT_STATES, SUBVIEWS, WIDTHS, safeShotName, summarizeCoverage, classifyRuntimeMessage, publicRequestFailure, checkGeometry } from '../coverage-model.mjs';

// Cleanup cannot hide a failing assertion, and cleanup-only errors must still fail.
async function runWithCoverageCleanup(operation, close, remove) {
  let value;
  let primaryError;
  let failed = false;
  try { value = await operation(); }
  catch (error) { primaryError = error; failed = true; }
  const cleanupErrors = [];
  try {
    try { await close(); } catch (error) { cleanupErrors.push(error); }
  } finally {
    try { await remove(); } catch (error) { cleanupErrors.push(error); }
  }
  if (failed) {
    if (cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'Coverage test and cache cleanup failed', { cause: primaryError });
    throw primaryError;
  }
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Coverage cache cleanup failed');
  return value;
}

test('coverage inventory exactly matches production navigation routes, module labels and visible tabs', async () => {
  const navigationUrl = new URL('../../../web/src/layout/navigation.ts', import.meta.url);
  const source = fs.readFileSync(navigationUrl, 'utf8');
  const union = source.match(/export type ViewMode\s*=([\s\S]*?);/);
  assert.ok(union, 'production ViewMode union is required');
  const views = [...union[1].matchAll(/'([^']+)'/g)].map(([, view]) => view);
  // Use the application's locked Vite toolchain: navigation now depends on the
  // real i18n catalog's alias and import.meta.glob transformation. Do not stub
  // translations or duplicate the production navigation in a test fixture.
  const webRoot = fileURLToPath(new URL('../../../web/', import.meta.url));
  const requireWeb = createRequire(new URL('../../../web/package.json', import.meta.url));
  const loadWeb = name => import(pathToFileURL(requireWeb.resolve(name)).href);
  const [{ createServer }, { ViteNodeServer }, { ViteNodeRunner }] = await Promise.all([
    import(new URL('./dist/node/index.js', pathToFileURL(requireWeb.resolve('vite/package.json'))).href),
    loadWeb('vite-node/server'), loadWeb('vite-node/client'),
  ]);
  // This helper disables discovery; its partial optimized set must never seed browser QA.
  const cacheDir = fs.mkdtempSync(path.join(os.tmpdir(), 'offerpilot-coverage-vite-'));
  let server;
  await runWithCoverageCleanup(async () => {
    server = await createServer({ root: webRoot, cacheDir,
      server: { middlewareMode: true, hmr: false, watch: null },
      optimizeDeps: { noDiscovery: true, include: [] },
    });
    await server.pluginContainer.buildStart({});
    const node = new ViteNodeServer(server);
    const runner = new ViteNodeRunner({ root: server.config.root, base: server.config.base,
      fetchModule: id => node.fetchModule(id), resolveId: (id, importer) => node.resolveId(id, importer),
    });
    const i18n = await runner.executeFile(fileURLToPath(new URL('../../../web/src/i18n/index.ts', import.meta.url)));
    await i18n.setLocale('zh-CN'); // This installed-UI inventory targets the Chinese baseline.
    assert.equal(i18n.getLocale(), 'zh-CN');
    const { MODULE_NAV, MODULE_TABS, resolveModuleForView, defaultViewForModule, moduleTabsForView } =
      await runner.executeFile(fileURLToPath(navigationUrl));
    const production = { nav: MODULE_NAV, tabs: MODULE_TABS, routes: views.map(view => ({
      view, module: resolveModuleForView(view), defaultView: defaultViewForModule(resolveModuleForView(view)), tabs: moduleTabsForView(view),
    })) };
    assert.equal(ROOTS.length, 13);
    assert.equal(new Set(ROOTS.map(({ view }) => view)).size, ROOTS.length);
    assert.deepEqual(ROOTS.map(({ view }) => view).sort(), [...views].sort());
    assert.deepEqual(Object.values(production.tabs).flat().map(({ view }) => view).sort(), [...views].sort());
    for (const item of ROOTS) {
      const route = production.routes.find(({ view }) => view === item.view);
      const module = production.nav.find(({ key }) => key === route.module);
      assert.equal(item.module, module?.label ?? null, `${item.view} must use the production sidebar label`);
      assert.equal(item.tab, route.tabs.length > 1 ? route.tabs.find(({ view }) => view === item.view)?.label : null,
        `${item.view} must select an actual visible module tab`);
      if (module) assert.equal(module.defaultView, route.defaultView, `${item.view} module default must be consistent`);
      else assert.equal(item.view, 'pilot', 'only the command-palette Pilot root has no sidebar entry');
    }
    assert.equal(SUBVIEWS.length, 32); assert.equal(new Set(SUBVIEWS.map(({ id }) => id)).size, 32);
    assert.deepEqual(WIDTHS, [900, 1008, 1280, 1440]);
  }, () => server?.close(), () => fs.rmSync(cacheDir, { recursive: true, force: true }));
});

test('coverage cleanup preserves the primary failure and attempts removal even when close fails', async () => {
  const primary = new assert.AssertionError({ message: 'Original inventory assertion' });
  const closeError = new Error('Close failed');
  const removeError = new Error('Remove failed');
  const calls = [];
  await assert.rejects(runWithCoverageCleanup(
    async () => { throw primary; },
    async () => { calls.push('close'); throw closeError; },
    () => { calls.push('remove'); throw removeError; },
  ), error => {
    assert.ok(error instanceof AggregateError);
    assert.equal(error.cause, primary);
    assert.deepEqual(error.errors, [primary, closeError, removeError]);
    return true;
  });
  assert.deepEqual(calls, ['close', 'remove']);
  await assert.rejects(runWithCoverageCleanup(async () => { throw primary; }, () => {}, () => {}), error => error === primary);
});

test('coverage cleanup-only failures cannot turn into successful tests', async () => {
  for (const failedStep of ['close', 'remove']) {
    const cleanupError = new Error(`${failedStep} failed`);
    const calls = [];
    await assert.rejects(runWithCoverageCleanup(
      () => 'passed',
      () => { calls.push('close'); if (failedStep === 'close') throw cleanupError; },
      () => { calls.push('remove'); if (failedStep === 'remove') throw cleanupError; },
    ), error => {
      assert.ok(error instanceof AggregateError);
      assert.deepEqual(error.errors, [cleanupError]);
      return true;
    });
    assert.deepEqual(calls, ['close', 'remove']);
  }
});

test('the complete 130-target root matrix covers both populations, dark widths and light 1280 exactly once', () => {
  assert.equal(ROOT_CASES.length, 130);
  assert.equal(new Set(ROOT_CASES.map(({ caseId }) => caseId)).size, 130);
  for (const root of ROOTS) for (const state of ROOT_STATES) {
    const targets = ROOT_CASES.filter(item => item.surfaceId === root.id && item.state === state);
    assert.deepEqual(targets.map(({ theme, width }) => [theme, width]),
      [...WIDTHS.map(width => ['dark', width]), ['light', 1280]]);
    for (const target of targets) {
      assert.equal(target.view, root.view);
      assert.equal(target.caseId, `${state}-${target.theme}-${target.width}-${root.view}`);
    }
  }
});

test('screenshots alone cannot become functional PASS; incomplete and failures remain visible', () => {
  assert.throws(() => summarizeCoverage([{ outcome:'PASS', kind:'visual', assertions:[], screenshots:['screen.png'] }]));
  assert.throws(() => summarizeCoverage([{ outcome:'PASS', kind:'interaction', assertions:['saved'], screenshots:[] }]));
  const visual = { outcome:'PASS', kind:'visual', assertions:['root selected'], screenshots:['root.png'] };
  assert.equal(summarizeCoverage([visual]).functionalPasses, 0);
  const partial = summarizeCoverage([visual, {outcome:'BLOCKED'}, {outcome:'NOT RUN'}, {outcome:'N/A'}]);
  assert.equal(partial.status, 'incomplete');
  assert.equal(partial.humanVisualReview, 'required-not-automated');
  assert.equal(summarizeCoverage([visual, {outcome:'FAIL'}]).status, 'failed');
});

test('artifact screenshot names cannot escape the dedicated whitelist directory', () => {
  assert.equal(safeShotName('001-R01-empty-dark-1280'), '001-R01-empty-dark-1280.png');
  for (const value of ['../userData', 'a/b', 'token?secret', '', 'raw.log', 'a'.repeat(152)]) assert.throws(() => safeShotName(value));
});

test('runtime diagnostic classification never retains raw text, tokens, URLs or error stacks', () => {
  const sensitive = ' sk-sensitive ws://127.0.0.1:9333/private-debug-id C:\\Users\\real-user\\secret';
  assert.equal(classifyRuntimeMessage(`Refused to evaluate because of Content Security Policy${sensitive}`), 'csp-runtime-block');
  assert.equal(classifyRuntimeMessage(`Haru Live2D error${sensitive}`), 'haru-runtime-error');
  assert.equal(classifyRuntimeMessage(`WebGL context lost${sensitive}`), 'graphics-runtime-error');
  assert.equal(classifyRuntimeMessage(sensitive, 'pageerror'), 'unexpected-page-error');
  assert.equal(classifyRuntimeMessage(sensitive), 'unclassified-console-error');
  const diagnostic = publicRequestFailure('http://127.0.0.1:8000/api/private?token=secret', 'http://127.0.0.1:8000', 500);
  assert.deepEqual(diagnostic, {category:'own-api',status:500,expectedAbsent:false});
  assert.equal(publicRequestFailure('https://external.invalid/?token=secret', 'http://127.0.0.1:8000', 500), null);
  assert.equal(publicRequestFailure('not-a-url', 'http://127.0.0.1:8000', 500), null);
});

test('native viewport and global overflow checks reject simulation mismatch or clipping', () => {
  checkGeometry({width:900,height:900,documentWidth:900},900);
  assert.throws(() => checkGeometry({width:884,height:900,documentWidth:884},900));
  assert.throws(() => checkGeometry({width:900,height:900,documentWidth:1200},900));
  assert.throws(() => checkGeometry({width:900,height:599,documentWidth:900},900));
});

test('installed flows use public UI and native sizing without fixtures or external/model invocations', () => {
  const flow = fs.readFileSync(new URL('../screen-coverage.mjs', import.meta.url), 'utf8');
  const recorder = fs.readFileSync(new URL('../coverage-recorder.mjs', import.meta.url), 'utf8');
  assert.match(recorder, /win\.setContentSize\(value\.width, value\.height\)/);
  assert.match(recorder, /window\.innerWidth === value\.width && window\.innerHeight === value\.height/);
  for (const value of [flow,recorder]) assert.doesNotMatch(value, /setViewportSize|page\.route\(|route\.fulfill|localStorage|sessionStorage|setQueryData|sqlite|page\.request\.|fetch\(/);
  assert.doesNotMatch(flow, /btn\([^\n]*'(?:发送|根据所选内容整理故事|开始录音|下载模型)'\)\.click/);
  assert.match(flow, /responseFromUI/);
  assert.match(flow, /api\.py constructs ExtractionWorker/);
});
