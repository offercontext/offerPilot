import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { parse } from 'yaml';
import { PIN } from '../contract.mjs';
const original = parse(await fs.readFile(new URL('../../../.github/workflows/desktop-windows.yml', import.meta.url), 'utf8'));
const ai = parse(await fs.readFile(new URL('../../../.github/workflows/desktop-real-ai.yml', import.meta.url), 'utf8'));
const narrow = ['desktop/real-ai-validation/**', '.github/workflows/desktop-real-ai.yml', 'docs/architecture/desktop-real-ai-validation.md'];
const matches = (file, pattern) => pattern.endsWith('/**') ? file.startsWith(pattern.slice(0, -2)) : file === pattern;
const originalRuns = files => !files.every(file => original.on.push['paths-ignore'].some(pattern => matches(file, pattern)));
test('exact three AI helper exclusions preserve all product and original-workflow routes', () => {
  assert.deepEqual(original.on.push['paths-ignore'], ['desktop/installed-ui/**', 'desktop/layout-retry/**',
    '.github/workflows/desktop-layout-retry.yml', '.github/workflows/desktop-installed-ui.yml',
    'docs/architecture/desktop-installed-ui-validation.md', ...narrow,
    'desktop/browser-cleanup-diagnostic/**', '.github/workflows/desktop-browser-cleanup-diagnostic.yml']);
  for (const file of ['desktop/real-ai-validation/run.mjs', ...narrow.slice(1)]) assert.equal(originalRuns([file]), false);
  for (const file of ['desktop/main.cjs', 'desktop/package.json', 'desktop/package-lock.json', 'src/offerpilot/api.py',
    'web/src/App.tsx', 'uv.lock', 'scripts/release-gate.ps1', '.github/workflows/desktop-windows.yml']) {
    assert.equal(originalRuns([file]), true); assert.equal(originalRuns([file, 'desktop/real-ai-validation/run.mjs']), true);
  }
  assert.equal(originalRuns(['.github/workflows/desktop-windows.yml', ...narrow.slice(1)]), true,
    'the migration itself still runs existing Windows workflow');
});
test('default full gate, dispatch and existing explicit package-only expressions remain unchanged', () => {
  const activation = "github.event_name == 'push' && startsWith(github.event.head_commit.message, 'build: AI [windows-package-only] ')";
  assert.deepEqual(Object.keys(original.on).sort(), ['push', 'workflow_dispatch']);
  assert.deepEqual(original.on.push.branches, [PIN.branch]);
  assert.equal(original.on.workflow_dispatch, null);
  assert.equal(original.env.WINDOWS_PACKAGE_ONLY, `\${{ ${activation} }}`);
  for (const name of ['pytest-manifest', 'pytest-shards']) assert.equal(original.jobs[name].if, `\${{ !(${activation}) }}`);
  assert.equal(original.jobs['full-regression'].if, `\${{ always() && !(${activation}) }}`);
  assert.equal(original.jobs['validation-package'].if, undefined);
  assert.equal(original.jobs['validation-status'].if, '${{ always() }}');
  assert.deepEqual(original.jobs['validation-status'].needs, ['validation-package', 'full-regression']);
  assert.equal(original.concurrency, undefined);
});
test('package-only publication can run MOCK but cannot itself authorize paid AI', () => {
  assert.equal(ai.on.push.paths, undefined);
  assert.equal(ai.on.push['paths-ignore'], undefined);
  assert.ok(ai.jobs.offline.if.includes("needs.route.outputs.offline == 'true'"));
  assert.equal(ai.jobs.offline.environment, undefined);
  assert.equal(JSON.stringify(ai.jobs.offline).includes('secrets.'), false);
  assert.ok(ai.jobs.preflight.if.includes('github.sha == vars.OFFERPILOT_AI_REQUEST_SHA'));
  assert.ok(ai.jobs.preflight.if.includes("startsWith(github.event.head_commit.message, 'test: request OfferPilot real AI validation')"));
  assert.equal('build: AI [windows-package-only] Publish candidate'.startsWith('test: request OfferPilot real AI validation'), false);
  assert.equal(ai.jobs['real-ai'].environment.name, 'offerpilot-real-ai-validation');
});
