import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { ROOTS, ROOT_CASES, ROOT_STATES, SUBVIEWS, WIDTHS, safeShotName, summarizeCoverage, classifyRuntimeMessage, publicRequestFailure, checkGeometry } from '../coverage-model.mjs';

test('coverage inventory exactly matches production navigation routes, module labels and visible tabs', () => {
  const navigationUrl = new URL('../../../web/src/layout/navigation.ts', import.meta.url);
  const source = fs.readFileSync(navigationUrl, 'utf8');
  const union = source.match(/export type ViewMode\s*=([\s\S]*?);/);
  assert.ok(union, 'production ViewMode union is required');
  const views = [...union[1].matchAll(/'([^']+)'/g)].map(([, view]) => view);
  // Run the actual typed module in Node's supported strip-only mode. Looking for
  // view-name substrings cannot catch missing routes or wrong module/tab labels.
  const script = `import { MODULE_NAV, MODULE_TABS, resolveModuleForView, defaultViewForModule, moduleTabsForView } from ${JSON.stringify(navigationUrl.href)};
    const views = ${JSON.stringify(views)};
    console.log(JSON.stringify({ nav: MODULE_NAV, tabs: MODULE_TABS, routes: views.map(view => ({
      view, module: resolveModuleForView(view), defaultView: defaultViewForModule(resolveModuleForView(view)), tabs: moduleTabsForView(view),
    })) }));`;
  const production = JSON.parse(execFileSync(process.execPath,
    ['--experimental-strip-types', '--input-type=module', '--eval', script], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
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
