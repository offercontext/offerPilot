import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { ENVIRONMENT, PIN, WORKFLOW, validateFixedFiles, validateTrigger, validateEnvironment,
  validateReview, validateLiveProduct, validateHistory, validatePriorJobs, validateOfflineRun, safeCode, demand } from './contract.mjs';
import { githubReader } from './github-read.mjs';
export async function preflight({ env = process.env, read = githubReader(env.GH_TOKEN), approval = false } = {}) {
  await validateFixedFiles();
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
  let offlineVerified = false;
  for (const run of previous) {
    demand(run.status === 'completed', 'PRIOR_RUN_UNRESOLVED');
    const jobs = await read(`actions/runs/${run.id}/jobs?filter=all&per_page=100`);
    validatePriorJobs(jobs);
    if (run.head_sha === identity.helperSha) {
      validateOfflineRun(run, jobs, identity.helperSha); offlineVerified = true;
    }
  }
  demand(offlineVerified, 'SAME_HELPER_MOCK_RUN_REQUIRED');
  if (approval) validateReview(await read(`actions/runs/${env.GITHUB_RUN_ID}/approvals`), protection.environmentId, protection.reviewerId);
  // A successful MOCK or reviewer approval cannot authorize a package-only live run.
  validateLiveProduct();
  return { schema: 1, status: 'passed', ...identity, environmentId: protection.environmentId,
    manualReviewVerified: approval, sameHelperOfflineVerified: true, requestBudgetCny: 10 };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const result = await preflight({ approval: process.argv.includes('--approval') });
    if (process.env.GITHUB_OUTPUT) await fs.appendFile(process.env.GITHUB_OUTPUT, `helper_sha=${result.helperSha}\n`);
    console.log('Protected environment and exact request verified.');
  } catch (error) { console.error(safeCode(error)); process.exitCode = 1; }
}
