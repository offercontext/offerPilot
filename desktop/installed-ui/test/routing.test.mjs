import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';
import { PIN } from '../contract.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const original = parse(fs.readFileSync(path.join(root, '.github/workflows/desktop-windows.yml'), 'utf8'));
const ui = parse(fs.readFileSync(path.join(root, '.github/workflows/desktop-installed-ui.yml'), 'utf8'));
const allowed = ['desktop/installed-ui/**', '.github/workflows/desktop-installed-ui.yml', 'docs/architecture/desktop-installed-ui-validation.md'];
const matches = (file, pattern) => pattern.endsWith('/**') ? file.startsWith(pattern.slice(0, -2)) : file === pattern;
function routes(branch, files, message = '') {
  if (/\[skip ci\]/i.test(message)) return { original: false, ui: false };
  function triggered(workflow) {
    const push = workflow.on.push;
    if (!push.branches.includes(branch)) return false;
    if (push.paths) return files.some((file) => push.paths.some((pattern) => matches(file, pattern)));
    return !files.every((file) => push['paths-ignore'].some((pattern) => matches(file, pattern)));
  }
  return { original: triggered(original), ui: triggered(ui) };
}
test('exact branch and exact narrow paths, without alternate triggers or auto-cancel', () => {
  for (const workflow of [original, ui]) {
    assert.deepEqual(Object.keys(workflow.on).sort(), ['push', 'workflow_dispatch']);
    assert.deepEqual(workflow.on.push.branches, [PIN.branch]);
    assert.equal(workflow.on.workflow_dispatch, null, 'dispatch cannot accept arbitrary input');
    assert.equal(workflow.concurrency, undefined);
    for (const job of Object.values(workflow.jobs)) assert.equal(job.concurrency, undefined);
  }
  assert.deepEqual(original.on.push['paths-ignore'], allowed);
  assert.deepEqual(ui.on.push.paths, allowed);
  assert.equal(original.on.push.paths, undefined);
  assert.equal(ui.on.push['paths-ignore'], undefined);
});
test('push routing matrix isolates UI-only activation and retains all product/build paths', () => {
  for (const file of ['desktop/installed-ui/request.json', 'desktop/installed-ui/smoke.mjs', allowed[1], allowed[2]]) {
    assert.deepEqual(routes(PIN.branch, [file]), { original: false, ui: true });
  }
  for (const file of ['desktop/main.cjs', 'desktop/lifecycle.cjs', 'desktop/package.json', 'desktop/package-lock.json',
    'desktop/build-backend.py', 'desktop/backend.spec', 'desktop/validate-windows.ps1', 'desktop/smoke-backend.py',
    '.github/workflows/desktop-windows.yml', 'scripts/release-gate.ps1', 'src/offerpilot/api.py', 'web/src/App.tsx', 'uv.lock']) {
    assert.deepEqual(routes(PIN.branch, [file]), { original: true, ui: false }, file);
    assert.deepEqual(routes(PIN.branch, [file, 'desktop/installed-ui/request.json']), { original: true, ui: true }, file);
  }
  const bootstrap = ['desktop/installed-ui/smoke.mjs', allowed[1], allowed[2], '.github/workflows/desktop-windows.yml'];
  assert.deepEqual(routes(PIN.branch, bootstrap, 'ci: AI 安装 UI 辅助路由 [skip ci]'), { original: false, ui: false });
  for (const branch of ['master', 'main', 'feat/other', `${PIN.branch}-other`]) {
    assert.deepEqual(routes(branch, ['desktop/main.cjs', 'desktop/installed-ui/request.json']), { original: false, ui: false });
  }
});
test('dedicated job is read-only, branch-guarded and downloads only the hard pin', () => {
  assert.deepEqual(ui.permissions, {});
  assert.deepEqual(Object.keys(ui.jobs), ['installed-ui']);
  const job = ui.jobs['installed-ui'];
  assert.deepEqual(job.permissions, { contents: 'read', actions: 'read' });
  assert.equal(job.if, "${{ github.repository == 'offercontext/offerPilot' && github.ref == 'refs/heads/feat/20261005-windows-desktop-validation' }}");
  const download = job.steps.find((step) => step.uses?.startsWith('actions/download-artifact@'));
  assert.equal(download.uses, 'actions/download-artifact@v4');
  assert.equal(download.with.repository, PIN.repository);
  assert.equal(download.with['run-id'], PIN.runId);
  assert.equal(download.with.name, PIN.artifactName);
  const source = job.steps.find((step) => step.uses === 'actions/checkout@v4' && step.with.ref);
  assert.equal(source.with.ref, PIN.commit);
  for (const step of job.steps.filter((item) => item.uses === 'actions/checkout@v4')) assert.equal(step.with['persist-credentials'], false);
  const upload = job.steps.find((step) => step.uses === 'actions/upload-artifact@v4');
  assert.equal(upload.if, '${{ always() }}');
  assert.equal(upload.with['if-no-files-found'], 'error');
  assert.deepEqual(upload.with.path.trim().split('\n'), ['scope.txt', 'source.json', 'result.json',
    '01-first-launch.png', '02-saved-detail.png', '03-saved-list.png', '04-restarted-list.png', '05-restarted-detail.png', 'failure.png']
    .map((name) => `desktop/installed-ui/evidence/${name}`));
  const script = job.steps.map((step) => step.run || '').join('\n');
  assert.doesNotMatch(script, /release-gate|build:win|uv sync|npm\.cmd (?:ci|test) --prefix (?:web|desktop)\s*(?:\n|$)/);
});
test('automation does not request a browser install, mutation of fuses or weaken Electron protection', () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'desktop/installed-ui/package.json'), 'utf8'));
  assert.equal(packageJson.dependencies['playwright-core'], '1.63.0');
  assert.equal(packageJson.dependencies['@electron/fuses'], '2.1.3');
  const smoke = fs.readFileSync(path.join(root, 'desktop/installed-ui/smoke.mjs'), 'utf8');
  assert.match(smoke, /chromiumSandbox: true, bypassCSP: false/);
  assert.doesNotMatch(smoke, /flipFuses\s*\(|webSecurity:\s*false|contextIsolation:\s*false|sandbox:\s*false|nodeIntegration:\s*true|devTools:\s*true|storageState\s*\(|\.tracing\./);
});
