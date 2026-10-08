import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { PIN, WORKFLOW, ENVIRONMENT, REVIEWER, PAID_JOB, OFFLINE_JOB, marker, validateOfflineRun } from '../contract.mjs';
import { classifyPush } from '../classify-change.mjs';
import { preflight } from '../preflight.mjs';
const before = 'a'.repeat(40), after = 'b'.repeat(40), tree = 'c'.repeat(40);
function input() {
  return { event: { before, after, repository: { full_name: PIN.repository }, head_commit: { id: after } },
    env: { GITHUB_EVENT_NAME: 'push', GITHUB_REPOSITORY: PIN.repository, GITHUB_REF: `refs/heads/${PIN.branch}`,
      GITHUB_RUN_ATTEMPT: '1', GITHUB_SHA: after }, comparison: {
      status: 'ahead', base_commit: { sha: before }, merge_base_commit: { sha: before }, ahead_by: 1, total_commits: 1,
      commits: [{ sha: after, parents: [{ sha: before }] }], files: [{ filename: 'desktop/real-ai-validation/run.mjs', status: 'modified' }],
    } };
}
const classify = f => classifyPush(f.event, f.env, f.comparison);
test('tool changes run Windows MOCK, unrelated and empty activation do not', () => {
  const f = input(); assert.equal(classify(f).offline, true);
  f.comparison.files = [{ filename: 'desktop/installed-ui/haru.mjs', status: 'modified' }]; assert.equal(classify(f).offline, false);
  f.comparison.files = []; assert.equal(classify(f).offline, false);
  f.comparison.files = [{ filename: 'elsewhere.mjs', previous_filename: 'desktop/real-ai-validation/run.mjs', status: 'renamed' }];
  assert.equal(classify(f).offline, true);
});
for (const [label, change] of [
  ['merge', f => f.comparison.commits[0].parents.push({ sha: tree })],
  ['missing parent', f => { f.comparison.commits[0].parents = []; }],
  ['incomplete history', f => { f.comparison.total_commits = 2; }],
  ['truncated files', f => { f.comparison.files = Array(300).fill(f.comparison.files[0]); }],
  ['missing files', f => { delete f.comparison.files; }],
  ['nonancestor', f => { f.comparison.merge_base_commit.sha = tree; }],
  ['force push', f => { f.event.forced = true; }],
  ['new branch', f => { f.event.before = '0'.repeat(40); }],
  ['other branch', f => { f.env.GITHUB_REF = 'refs/heads/master'; }],
]) test(`route fails closed for ${label}`, () => { const f = input(); change(f); assert.throws(() => classify(f)); });
function offlineRun() {
  return { id: 1, run_number: 1, path: WORKFLOW, head_sha: before, event: 'push', head_branch: PIN.branch,
    run_attempt: 1, status: 'completed', conclusion: 'success', repository: { full_name: PIN.repository }, head_repository: { full_name: PIN.repository } };
}
function offlineJobs() { return { total_count: 2, jobs: [
  { name: OFFLINE_JOB, head_sha: before, run_id: 1, status: 'completed', conclusion: 'success' },
  { name: PAID_JOB, status: 'completed', conclusion: 'skipped' },
] }; }
test('paid gate requires actual completed successful Windows MOCK job at same reviewed helper', () => {
  validateOfflineRun(offlineRun(), offlineJobs(), before);
  for (const [run, jobs] of [[{ ...offlineRun(), head_sha: after }, offlineJobs()],
    [{ ...offlineRun(), path: '.github/workflows/another.yml' }, offlineJobs()],
    [{ ...offlineRun(), status: 'in_progress' }, offlineJobs()],
    [{ ...offlineRun(), run_attempt: 2 }, offlineJobs()],
    [offlineRun(), { total_count: 2, jobs: [] }],
    [offlineRun(), { total_count: 1, jobs: [{ ...offlineJobs().jobs[0], conclusion: 'skipped' }] }],
    [offlineRun(), { total_count: 1, jobs: [{ ...offlineJobs().jobs[0], head_sha: after }] }],
  ]) assert.throws(() => validateOfflineRun(run, jobs, before));
});
test('full preflight retains same-helper MOCK gate and denies package-only LIVE', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-preflight-'));
  const eventPath = path.join(dir, 'event.json');
  const event = { before, after, repository: { full_name: PIN.repository }, head_commit: { id: after, message: marker(before) } };
  await fs.writeFile(eventPath, JSON.stringify(event));
  const env = { ...input().env, GITHUB_EVENT_PATH: eventPath, GITHUB_RUN_ID: '2', AI_APPROVED_HELPER_SHA: before, AI_REQUEST_SHA: after };
  const head = { sha: after, message: marker(before), parents: [{ sha: before }], tree: { sha: tree } };
  const helper = { sha: before, tree: { sha: tree } };
  const environment = { id: 1, name: ENVIRONMENT, can_admins_bypass: false,
    protection_rules: [{ type: 'required_reviewers', prevent_self_review: false, reviewers: [{ type: 'User', reviewer: { type: 'User', ...REVIEWER } }] }],
    deployment_branch_policy: { protected_branches: false, custom_branch_policies: true } };
  let verified = true;
  const read = async suffix => {
    if (suffix === `git/commits/${after}`) return head;
    if (suffix === `git/commits/${before}`) return helper;
    if (suffix.startsWith('git/ref/')) return { object: { sha: after } };
    if (suffix === `environments/${ENVIRONMENT}`) return environment;
    if (suffix.includes('deployment-branch-policies')) return { total_count: 1, branch_policies: [{ name: PIN.branch, type: 'branch' }] };
    if (suffix.includes('/runs?')) return { total_count: 2, workflow_runs: [
      { id: 2, run_number: 2, path: WORKFLOW, head_sha: after }, { ...offlineRun(), head_sha: verified ? before : tree },
    ] };
    if (suffix === 'actions/runs/1/jobs?filter=all&per_page=100') return offlineJobs();
    if (suffix.endsWith('/approvals')) return [{ state: 'approved', environments: [{ name: ENVIRONMENT, id: 1 }], user: { type: 'User', id: REVIEWER.id } }];
    assert.fail(`unexpected read route: ${suffix}`);
  };
  try {
    if (PIN.fullRegressionRunId === null) {
      await assert.rejects(preflight({ env, read, approval: true }), { safeCode: 'FULL_GATE_PROVENANCE_REQUIRED' });
    } else {
      assert.equal((await preflight({ env, read, approval: true })).sameHelperOfflineVerified, true);
    }
    verified = false;
    await assert.rejects(preflight({ env, read }), error => error.safeCode === 'SAME_HELPER_MOCK_RUN_REQUIRED');
  } finally { await fs.rm(dir, { recursive: true, force: true }); }
});

test('package-only live preparation and execution each block before host, profile or broker access', async () => {
  if (PIN.fullRegressionRunId !== null) return;
  const { prepare } = await import('../prepare.mjs');
  const { executeValidation } = await import('../validation-runner.mjs');
  await assert.rejects(prepare({ mode: 'live' }), { safeCode: 'FULL_GATE_PROVENANCE_REQUIRED' });
  let brokerCalled = false;
  await assert.rejects(executeValidation({ mode: 'live', brokerFactory: () => { brokerCalled = true; } }),
    { safeCode: 'FULL_GATE_PROVENANCE_REQUIRED' });
  assert.equal(brokerCalled, false);
});
