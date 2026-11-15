import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { ENVIRONMENT, PIN, WORKFLOW, validateFixedFiles, validateTrigger, validateEnvironment,
  validateReview, validateLiveProduct, validateHistory, validatePriorJobs, validateOfflineRun, validateCurrentFullGate, safeCode, demand } from './contract.mjs';
import { githubReader } from './github-read.mjs';
import { RECOVERY } from './carry-forward-policy.cjs';
import { verifyHistoricalSource, downloadPriorZip } from './carry-forward-evidence.mjs';
export async function preflight({ env = process.env, read = githubReader(env.GH_TOKEN), approval = false, download = () => downloadPriorZip(env.GH_TOKEN) } = {}) {
  await validateFixedFiles();
  validateLiveProduct();
  demand(env.GITHUB_RUN_ATTEMPT === '1', 'RERUN_FORBIDDEN');
  const event = JSON.parse(await fs.readFile(env.GITHUB_EVENT_PATH, 'utf8'));
  demand(/^[0-9a-f]{40}$/.test(env.AI_APPROVED_HELPER_SHA || '') && /^[0-9a-f]{40}$/.test(env.AI_REQUEST_SHA || ''), 'EXACT_SHA_APPROVAL_MISSING');
  const [head, helper, branch, environment, policies, runs] = await Promise.all([
    read(`git/commits/${env.AI_REQUEST_SHA}`), read(`git/commits/${env.AI_APPROVED_HELPER_SHA}`),
    read(`git/ref/heads/${PIN.branch}`), read(`environments/${ENVIRONMENT}`),
    read(`environments/${ENVIRONMENT}/deployment-branch-policies?per_page=100`),
    read(`actions/workflows/${WORKFLOW.split('/').at(-1)}/runs?per_page=100`),
  ]);
  const identity = validateTrigger(event, env, head, helper, branch);
  const protection = validateEnvironment(environment, policies, env.AI_REVIEWER_LOGIN);
  const previous = validateHistory(runs, env.GITHUB_RUN_ID);
  const sourceRuns = previous.filter(run => run.id === RECOVERY.source.runId);
  demand(sourceRuns.length === 1, 'CARRY_FORWARD_SOURCE_MISSING');
  let offlineVerified = false;
  for (const run of previous) {
    demand(run.status === 'completed', 'PRIOR_RUN_UNRESOLVED');
    if (run.id === RECOVERY.source.runId) continue;
    const jobs = await read(`actions/runs/${run.id}/jobs?filter=all&per_page=100`);
    validatePriorJobs(jobs);
    if (run.head_sha === identity.helperSha) {
      validateOfflineRun(run, jobs, identity.helperSha); offlineVerified = true;
    }
  }
  demand(offlineVerified, 'SAME_HELPER_MOCK_RUN_REQUIRED');
  if (approval) validateReview(await read(`actions/runs/${env.GITHUB_RUN_ID}/approvals`), protection.environmentId, protection.reviewerId);
  // MOCK + manual approval cannot replace the current product's actual full gate.
  const [fullRun, fullJobs] = await Promise.all([
    read(`actions/runs/${PIN.fullRegressionRunId}`), read(`actions/runs/${PIN.fullRegressionRunId}/jobs?filter=all&per_page=100`),
  ]);
  validateCurrentFullGate(fullRun, fullJobs);
  const prior = await verifyHistoricalSource({ read, download });
  demand(sourceRuns[0].repository?.id === prior.run.repository.id && sourceRuns[0].head_repository?.id === prior.run.head_repository.id,
    'CARRY_FORWARD_SOURCE_UNVERIFIED');
  // This is the only non-skipped prior paid run exception, and it requires the
  // fixed ZIP's private brand as well as authenticated complete API metadata.
  validatePriorJobs(prior.jobs, { run: sourceRuns[0], carryForward: prior.carryForward });
  return { schema: 1, status: 'passed', ...identity, environmentId: protection.environmentId,
    manualReviewVerified: approval, sameHelperOfflineVerified: true, currentFullGateVerified: true, requestBudgetCny: 10,
    carryForward: prior.carryForward, priorZip: prior.priorZip };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const result = await preflight({ approval: process.argv.includes('--approval') });
    if (process.env.GITHUB_OUTPUT) await fs.appendFile(process.env.GITHUB_OUTPUT, `helper_sha=${result.helperSha}\n`);
    console.log('Protected environment and exact request verified.');
  } catch (error) { console.error(safeCode(error)); process.exitCode = 1; }
}
