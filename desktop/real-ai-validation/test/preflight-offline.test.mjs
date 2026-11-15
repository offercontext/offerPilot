import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { PIN, WORKFLOW, ENVIRONMENT, REVIEWER, PAID_JOB, OFFLINE_JOB, marker, validateOfflineRun, validateCurrentFullGate } from '../contract.mjs';
import { classifyPush } from '../classify-change.mjs';
import { preflight } from '../preflight.mjs';
import { RECOVERY } from '../carry-forward-policy.cjs';
import { sourceMetadata } from './fixtures/recovery-metadata.mjs';
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
function fullGate() {
  const run = { id: PIN.fullRegressionRunId, head_sha: PIN.commit, path: '.github/workflows/desktop-windows.yml',
    event: 'push', head_branch: PIN.branch, run_attempt: 1, status: 'completed', conclusion: 'success',
    repository: { full_name: PIN.repository, id: 7 }, head_repository: { full_name: PIN.repository, id: 7 } };
  return { run, jobs: { total_count: 2, jobs: ['Full release regression (required for release)', 'Validation results (both jobs must pass)']
    .map((name, index) => ({ id: index + 1, name, run_id: run.id, head_sha: PIN.commit, status: 'completed', conclusion: 'success' })) } };
}
test('current full gate must actually finish successfully with both exact required jobs', () => {
  const good = fullGate(); validateCurrentFullGate(good.run, good.jobs);
  for (const change of [f => { f.run.status = 'in_progress'; }, f => { f.run.conclusion = 'failure'; },
    f => { f.run.head_sha = RECOVERY.source.productCommit; }, f => f.run.run_attempt++,
    f => { f.jobs.jobs[0].conclusion = 'skipped'; }, f => { f.jobs.jobs[1].conclusion = 'failure'; },
    f => { f.jobs.jobs[1].name = 'Validation results'; }, f => f.jobs.total_count++,
    f => f.jobs.jobs[0].run_id++, f => { f.jobs.jobs[0].head_sha = before; }]) {
    const f = fullGate(); change(f); assert.throws(() => validateCurrentFullGate(f.run, f.jobs), { safeCode: 'FULL_GATE_SUCCESS_REQUIRED' });
  }
});
async function recoveryPreflightFixture() {
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
  const source = sourceMetadata(), full = fullGate(); source.run.run_number = 1;
  const history = { total_count: 3, workflow_runs: [{ id: 2, run_number: 3, path: WORKFLOW, head_sha: after, run_attempt: 1 },
    { ...offlineRun(), run_number: 2 }, structuredClone(source.run)] };
  const jobs = offlineJobs();
  const reviews = [{ state: 'approved', environments: [{ name: ENVIRONMENT, id: 1 }], user: { type: 'User', id: REVIEWER.id } }];
  const routes = new Map([
    [`git/commits/${after}`, head], [`git/commits/${before}`, helper], [`git/ref/heads/${PIN.branch}`, { object: { sha: after } }],
    [`environments/${ENVIRONMENT}`, environment], [`environments/${ENVIRONMENT}/deployment-branch-policies?per_page=100`,
      { total_count: 1, branch_policies: [{ name: PIN.branch, type: 'branch' }] }],
    [`actions/workflows/${WORKFLOW.split('/').at(-1)}/runs?per_page=100`, history], ['actions/runs/1/jobs?filter=all&per_page=100', jobs],
    ['actions/runs/2/approvals', reviews], [`actions/runs/${full.run.id}`, full.run], [`actions/runs/${full.run.id}/jobs?filter=all&per_page=100`, full.jobs],
    [`actions/runs/${source.run.id}`, source.run], [`actions/runs/${source.run.id}/jobs?filter=all&per_page=100`, source.jobs],
    [`actions/artifacts/${source.artifact.id}`, source.artifact], [`actions/runs/${source.run.id}/artifacts?per_page=100`, source.artifacts],
  ]);
  let downloads = 0;
  return { dir, env, source, full, history, jobs, reviews, routes,
    read: async route => { assert.ok(routes.has(route), `unexpected read route: ${route}`); return routes.get(route); },
    download: async () => { downloads++; return fs.readFile(new URL('./fixtures/prior-paid-38694575612.zip', import.meta.url)); },
    downloads: () => downloads };
}
test('full preflight carries only exact paid history and retains current helper MOCK, full gate and human approval', async () => {
  const f = await recoveryPreflightFixture();
  try {
    const result = await preflight({ env: f.env, read: f.read, download: f.download, approval: true });
    assert.equal(result.sameHelperOfflineVerified, true); assert.equal(result.currentFullGateVerified, true);
    assert.equal(result.manualReviewVerified, true); assert.equal(result.carryForward.settledMicroCny, 188);
    assert.equal(result.carryForward.sentRequests, 1); assert.equal(result.priorZip.length, 2109); assert.equal(f.downloads(), 1);
  } finally { await fs.rm(f.dir, { recursive: true, force: true }); }
});
for (const [label, change, code] of [
  ['old helper only', f => { f.history.workflow_runs[1].head_sha = tree; }, 'SAME_HELPER_MOCK_RUN_REQUIRED'],
  ['source absent', f => { f.history.workflow_runs[2].id = 3; }, 'CARRY_FORWARD_SOURCE_MISSING'],
  ['source rerun', f => { f.history.workflow_runs[2].run_attempt = 2; }, 'RERUN_FORBIDDEN'],
  ['duplicate source', f => { f.history.workflow_runs[1].id = RECOVERY.source.runId; }, 'RERUN_FORBIDDEN'],
  ['missing history page', f => { f.history.total_count++; }, 'RUN_HISTORY_INCOMPLETE'],
  ['another paid job started', f => { f.jobs.jobs[1].conclusion = 'failure'; }, 'SESSION_ALREADY_CONSUMED'],
  ['another paid job pending', f => { f.jobs.jobs[1].status = 'in_progress'; }, 'SESSION_ALREADY_CONSUMED'],
  ['prior run unresolved', f => { f.history.workflow_runs[1].status = 'in_progress'; }, 'PRIOR_RUN_UNRESOLVED'],
  ['no manual approval', f => { f.reviews.length = 0; }, 'MANUAL_APPROVAL_UNVERIFIED'],
  ['current full run still running', f => { f.full.run.status = 'in_progress'; }, 'FULL_GATE_SUCCESS_REQUIRED'],
  ['current required full job skipped', f => { f.full.jobs.jobs[0].conclusion = 'skipped'; }, 'FULL_GATE_SUCCESS_REQUIRED'],
  ['source upload failed', f => { f.source.jobs.jobs[0].steps[0].conclusion = 'failure'; }, 'CARRY_FORWARD_SOURCE_UNVERIFIED'],
  ['source artifact expired', f => { f.source.artifact.expired = true; }, 'CARRY_FORWARD_SOURCE_UNVERIFIED'],
  ['history source identity changed', f => { f.history.workflow_runs[2].head_sha = before; }, 'CARRY_FORWARD_SOURCE_UNVERIFIED'],
]) test(`recovery preflight rejects ${label}`, async () => {
  const f = await recoveryPreflightFixture();
  try { change(f); await assert.rejects(preflight({ env: f.env, read: f.read, download: f.download, approval: true }), { safeCode: code }); }
  finally { await fs.rm(f.dir, { recursive: true, force: true }); }
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
