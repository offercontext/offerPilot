import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { classifyPush } from '../real-ai-validation/classify-change.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = filename => fs.readFileSync(path.join(repo, filename), 'utf8').replace(/\r\n/g, '\n');
const diagnostic = read('.github/workflows/desktop-browser-cleanup-diagnostic.yml');
const full = read('.github/workflows/desktop-windows.yml');
const realAi = read('.github/workflows/desktop-real-ai.yml');
const request = 'desktop/browser-cleanup-diagnostic/request.json';
const branch = 'feat/20261005-windows-desktop-validation';
const ignoreBlock = full.split('    paths-ignore:\n')[1].split('  workflow_dispatch:')[0];
const ignores = [...ignoreBlock.matchAll(/^      - '([^']+)'$/gm)].map(match => match[1]);
const ignored = filename => ignores.some(pattern => pattern.endsWith('/**')
  ? filename.startsWith(pattern.slice(0, -2)) : filename === pattern);

function routes(files, message, { selectedBranch = branch, attempt = 1 } = {}) {
  const skipped = message.includes('[skip ci]');
  const before = '1'.repeat(40);
  const after = '2'.repeat(40);
  const push = { before, after, repository: { full_name: 'offercontext/offerPilot' },
    head_commit: { id: after, message }, forced: false, deleted: false };
  const comparison = { status: 'ahead', base_commit: { sha: before }, merge_base_commit: { sha: before },
    commits: [{ sha: after, parents: [{ sha: before }] }], ahead_by: 1, total_commits: 1,
    files: files.map(filename => ({ filename, status: 'modified' })) };
  const offline = classifyPush(push, {
    GITHUB_EVENT_NAME: 'push', GITHUB_REPOSITORY: 'offercontext/offerPilot', GITHUB_REF: `refs/heads/${branch}`,
    GITHUB_RUN_ATTEMPT: '1', GITHUB_SHA: after,
  }, comparison).offline;
  const allowedPush = !skipped && selectedBranch === branch;
  const paidPrefix = realAi.match(/startsWith\(github.event.head_commit.message, '([^']+)'\)/)[1];
  return {
    full: allowedPush && files.some(filename => !ignored(filename)),
    diagnostic: allowedPush && attempt === 1 && files.includes(request) && files.length === 1,
    offline: allowedPush && offline,
    paid: allowedPush && message.toLowerCase().startsWith(paidPrefix.toLowerCase()),
  };
}

test('diagnostic activation and bounds stay exact', () => {
  assert.ok(diagnostic.includes(`    branches: [${branch}]\n    paths: ['${request}']`));
  assert.ok(!diagnostic.includes('workflow_dispatch:'));
  assert.match(diagnostic, /github\.repository == 'offercontext\/offerPilot' && github\.ref == 'refs\/heads\/feat\/20261005-windows-desktop-validation' && github\.run_attempt == 1/);
  assert.match(diagnostic, /\$changed\.Count -ne 1 -or \$changed\[0\] -cne \$requestPath/);
  assert.match(diagnostic, /\$event\.before -cne \$parent -or \$event\.after -cne \$head/);
  assert.match(diagnostic, /git rev-list --parents -n 1 HEAD/);
  assert.match(diagnostic, /\$parents\.Count -ne 2 -or \$parents\[0\] -cne \$head -or \$parents\[1\] -cne \$parent/);
  assert.ok(diagnostic.includes('timeout-minutes: 12'));
  assert.ok(diagnostic.includes('permissions:\n  contents: read\n'));
  assert.ok(diagnostic.includes('if: ${{ always() }}'));
  for (const excluded of ['secrets.', 'environment:', 'release-gate.ps1', 'build:win', 'npm.cmd', 'cancel-in-progress:']) {
    assert.ok(!diagnostic.includes(excluded), excluded);
  }
});

for (const [label, files, message, options, expected] of [
  ['bootstrap', ['.github/workflows/desktop-windows.yml', '.github/workflows/desktop-browser-cleanup-diagnostic.yml', 'desktop/browser-cleanup-diagnostic/run.py'], 'test: AI cleanup diagnosis [skip ci]', {}, { full: false, diagnostic: false, offline: false, paid: false }],
  ['request only', [request], 'test: AI 请求离线清理诊断', {}, { full: false, diagnostic: true, offline: false, paid: false }],
  ['helper only', ['desktop/browser-cleanup-diagnostic/run.py'], 'test: AI helper', {}, { full: false, diagnostic: false, offline: false, paid: false }],
  ['normal product', ['src/offerpilot/api.py'], 'fix: AI ordinary product', {}, { full: true, diagnostic: false, offline: false, paid: false }],
  ['mixed product request rejected', [request, 'src/offerpilot/api.py'], 'fix: AI mixed', {}, { full: true, diagnostic: false, offline: false, paid: false }],
  ['other branch', [request], 'test: AI 请求离线清理诊断', { selectedBranch: 'other' }, { full: false, diagnostic: false, offline: false, paid: false }],
  ['rerun blocked', [request], 'test: AI 请求离线清理诊断', { attempt: 2 }, { full: false, diagnostic: false, offline: false, paid: false }],
]) {
  test(label, () => assert.deepEqual(routes(files, message, options), expected));
}
