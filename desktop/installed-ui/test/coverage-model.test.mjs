import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { ROOTS, SUBVIEWS, WIDTHS, safeShotName, summarizeCoverage, classifyRuntimeMessage, publicRequestFailure, checkGeometry } from '../coverage-model.mjs';

test('coverage inventory matches all 13 actual root views and 31 major subviews', () => {
  assert.equal(ROOTS.length, 13); assert.equal(new Set(ROOTS.map((item) => item.view)).size, 13);
  assert.equal(SUBVIEWS.length, 31); assert.equal(new Set(SUBVIEWS.map((item) => item.id)).size, 31);
  assert.deepEqual(WIDTHS, [900, 1008, 1280, 1440]);
  const source = fs.readFileSync(new URL('../../../web/src/layout/navigation.ts', import.meta.url), 'utf8');
  for (const {view} of ROOTS) assert.ok(source.includes(`'${view}'`), view);
  assert.equal(ROOTS.find(({view}) => view === 'pilot').module, null, 'Pilot has no fabricated sidebar selector');
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
