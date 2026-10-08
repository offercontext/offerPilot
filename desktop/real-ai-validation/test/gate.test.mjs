import test from 'node:test';
import assert from 'node:assert/strict';
import { marker, PIN, ENVIRONMENT, WORKFLOW, PAID_JOB, REVIEWER, validateTrigger, validateEnvironment,
  validateReview, validateHistory, validatePriorJobs, childEnvironment, validateFixedFiles } from '../contract.mjs';
const sha = 'a'.repeat(40), helperSha = 'b'.repeat(40), tree = 'c'.repeat(40);
function fixture() {
  return { event: { after: sha, before: helperSha, repository: { full_name: PIN.repository }, head_commit: { id: sha, message: marker(helperSha) } },
    env: { GITHUB_EVENT_NAME: 'push', GITHUB_REPOSITORY: PIN.repository, GITHUB_REF: `refs/heads/${PIN.branch}`,
      GITHUB_RUN_ATTEMPT: '1', GITHUB_SHA: sha, AI_APPROVED_HELPER_SHA: helperSha, AI_REQUEST_SHA: sha },
    head: { sha, message: marker(helperSha), parents: [{ sha: helperSha }], tree: { sha: tree } },
    helper: { sha: helperSha, tree: { sha: tree } }, branch: { object: { sha } } };
}
const trigger = value => validateTrigger(value.event, value.env, value.head, value.helper, value.branch);
test('fixed files match reviewed product and single-session manifest', validateFixedFiles);
test('only exact empty child and selected request pass', () => assert.deepEqual(trigger(fixture()), { helperSha, requestSha: sha }));
for (const [label, change] of [
  ['ordinary push', f => { f.event.head_commit.message = 'test: safe helper publication'; }],
  ['wrong branch', f => { f.env.GITHUB_REF = 'refs/heads/master'; }],
  ['pull request event', f => { f.env.GITHUB_EVENT_NAME = 'pull_request_target'; }],
  ['dispatch event', f => { f.env.GITHUB_EVENT_NAME = 'workflow_dispatch'; }],
  ['rerun', f => { f.env.GITHUB_RUN_ATTEMPT = '2'; }],
  ['missing variable', f => { delete f.env.AI_APPROVED_HELPER_SHA; }],
  ['changed tree', f => { f.head.tree.sha = 'd'.repeat(40); }],
  ['merge parent', f => { f.head.parents.push({ sha }); }],
  ['force push', f => { f.event.forced = true; }],
  ['stale branch', f => { f.branch.object.sha = helperSha; }],
  ['extra marker text', f => { f.event.head_commit.message += '\nextra'; }],
  ['budget override', f => { f.event.head_commit.message = marker(helperSha).replace('CNY: 10', 'CNY: 11'); }],
]) test(`activation rejects ${label}`, () => { const f = fixture(); change(f); assert.throws(() => trigger(f)); });
function protectedEnvironment() {
  return { name: ENVIRONMENT, id: 17, can_admins_bypass: false,
    protection_rules: [{ type: 'required_reviewers', prevent_self_review: false, reviewers: [{ type: 'User', reviewer: { type: 'User', ...REVIEWER } }] }],
    deployment_branch_policy: { protected_branches: false, custom_branch_policies: true } };
}
const policies = () => ({ total_count: 1, branch_policies: [{ name: PIN.branch, type: 'branch' }] });
test('exact human-protected environment passes', () => assert.deepEqual(validateEnvironment(protectedEnvironment(), policies()), { environmentId: 17, reviewerId: REVIEWER.id }));
for (const [label, change] of [
  ['self-review blocks sole owner', e => { e.protection_rules[0].prevent_self_review = true; }],
  ['missing reviewer', e => { e.protection_rules = []; }],
  ['bot reviewer', e => { e.protection_rules[0].reviewers[0].reviewer.type = 'Bot'; }],
  ['different reviewer', e => { e.protection_rules[0].reviewers[0].reviewer.id++; }],
  ['admin bypass', e => { e.can_admins_bypass = true; }],
  ['unknown bypass', e => { delete e.can_admins_bypass; }],
  ['all branches', e => { e.deployment_branch_policy = null; }],
  ['auto-created environment', e => { e.protection_rules = []; e.can_admins_bypass = true; }],
]) test(`protection rejects ${label}`, () => { const e = protectedEnvironment(); change(e); assert.throws(() => validateEnvironment(e, policies())); });
for (const policy of [{ total_count: 2, branch_policies: policies().branch_policies },
  { total_count: 1, branch_policies: [{ name: '*', type: 'branch' }] },
  { total_count: 1, branch_policies: [{ name: PIN.branch, type: 'tag' }] }]) {
  test('inexact or incomplete deployment policies block', () => assert.throws(() => validateEnvironment(protectedEnvironment(), policy)));
}
test('only actual selected human approval is sufficient', () => {
  const good = { state: 'approved', environments: [{ id: 17, name: ENVIRONMENT }], user: { type: 'User', id: REVIEWER.id } };
  validateReview([good], 17, REVIEWER.id);
  for (const value of [[], [{ ...good, state: 'rejected' }], [{ ...good, user: { type: 'User', id: 9 } }], [good, good]])
    assert.throws(() => validateReview(value, 17, REVIEWER.id));
});
test('whole-session latch rejects any prior paid job, even different request SHA', () => {
  validatePriorJobs({ total_count: 1, jobs: [{ name: PAID_JOB, conclusion: 'skipped' }] });
  for (const value of [{ total_count: 0, jobs: [] }, { total_count: 1, jobs: [] },
    { total_count: 1, jobs: [{ name: 'wrong job', conclusion: 'skipped' }] },
    ...['success', 'failure', 'cancelled', null].map(conclusion => ({ total_count: 1, jobs: [{ name: PAID_JOB, conclusion }] }))])
    assert.throws(() => validatePriorJobs(value));
});
test('history must contain all run numbers and current run', () => {
  const a = { id: 1, run_number: 1, path: WORKFLOW }, b = { id: 2, run_number: 2, path: WORKFLOW };
  assert.deepEqual(validateHistory({ total_count: 2, workflow_runs: [b, a] }, '2'), [a]);
  for (const runs of [{ total_count: 2, workflow_runs: [b] }, { total_count: 1, workflow_runs: [b] },
    { total_count: 1, workflow_runs: [a] }, { total_count: 0, workflow_runs: [] }]) assert.throws(() => validateHistory(runs, '2'));
});
test('EXE environment cannot inherit provider, GitHub, proxy or Node hooks', () => {
  const env = childEnvironment({ APPDATA: 'app', PATH: 'path', OFFERPILOT_REAL_AI_KEY: 'fake-secret', GH_TOKEN: 'fake-gh',
    HTTPS_PROXY: 'fake', NODE_OPTIONS: '--require fake', NODE_DEBUG: 'http', OPENAI_API_KEY: 'fake', DEEPSEEK_API_KEY: 'fake' });
  assert.deepEqual(Object.keys(env).sort(), ['APPDATA', 'LITELLM_LOCAL_MODEL_COST_MAP', 'PATH', 'PYTHONUNBUFFERED', 'PYTHONUTF8'].sort());
});
