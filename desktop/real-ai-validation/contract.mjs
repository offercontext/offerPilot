import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { RECOVERY, validateCarryForward } from './carry-forward-policy.cjs';
import { PIN as PRODUCT, validateRequest as validateProduct } from '../installed-ui/contract.mjs';

export const PIN = PRODUCT;
export const ENVIRONMENT = 'offerpilot-real-ai-validation';
export const REVIEWER = Object.freeze({ login: 'XiaoZheBrother', id: 112178718 });
export const OFFLINE_JOB = 'Pinned EXE offline UI';
export const PAID_JOB = 'Approved bounded real AI';
export const WORKFLOW = '.github/workflows/desktop-real-ai.yml';
export const CASES = Object.freeze(['connection', 'pilot-stream', 'pilot-cancel', 'pilot-hitl-reject',
  'interview-preparation', 'resume-structure', 'offer-negotiation']);
export const LIMITS = Object.freeze({ currency: 'CNY', budget: 10, requests: 8, seconds: 600, retries: 0 });
export const marker = sha => `test: request OfferPilot real AI validation\n\nOfferPilot-AI-Request: ${sha}\nOfferPilot-AI-Budget-CNY: 10\nOfferPilot-AI-Scope: ${RECOVERY.scope}\nOfferPilot-AI-Carry-Run: ${RECOVERY.source.runId}\nOfferPilot-AI-Carry-Artifact: ${RECOVERY.source.artifactId}\nOfferPilot-AI-Carry-SHA256: ${RECOVERY.source.digest}\nOfferPilot-AI-Additional-Requests: ${RECOVERY.additionalRequests}\nOfferPilot-AI-Execution-Seconds: ${RECOVERY.executionMs / 1000}`;
export const EVIDENCE_CODES = new Set('PILOT_HARU_RECOVERY_PASSED PILOT_HARU_RECOVERY_COMPLETION_ONLY CARRY_FORWARD_INVALID CARRY_FORWARD_SOURCE_UNVERIFIED CARRY_FORWARD_PREPARED_INVALID CARRY_FORWARD_SOURCE_MISSING FULL_GATE_SUCCESS_REQUIRED MOCK_PILOT_COMPLETION_UNPROVEN MOCK_PILOT_STREAMING_PROVEN FULL_GATE_PROVENANCE_REQUIRED LIVE_COMPLETION_UNPROVEN LIVE_STREAMING_PROVEN LIVE_COMPLETION_ONLY ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY PUSH_RANGE_UNVERIFIED PUSH_HISTORY_INCOMPLETE MERGE_OR_MISSING_HISTORY PUSH_FILES_UNVERIFIED SAME_HELPER_MOCK_RUN_REQUIRED MOCK_JOB_HISTORY_INCOMPLETE SAME_HELPER_MOCK_JOB_REQUIRED ALL_UI_CASES_PASSED AUXILIARY_READBACK_FAILED BRANCH_PROTECTION_MISSING BROKER_CLEANUP_FAILED CANCEL_NOT_OBSERVED CLEANUP_HOST_UNVERIFIED CLEANUP_PASSED CLEANUP_PATH_UNVERIFIED CLEANUP_PENDING CONNECTION_FAILED CURRENT_RUN_NOT_IN_HISTORY DEBUG_ENVIRONMENT_FORBIDDEN DEDICATED_ENVIRONMENT_KEY_MISSING ENVIRONMENT_BYPASS_NOT_DISABLED EVIDENCE_CONTAINS_CREDENTIAL EVIDENCE_WRITE_BLOCKED EXACT_BRANCH_POLICY_MISSING EXACT_HUMAN_REVIEWER_MISSING EXACT_SHA_APPROVAL_MISSING EXISTING_INSPECT_FUSE_REQUIRED GITHUB_READ_INCOMPLETE GITHUB_READ_INVALID GITHUB_READ_PATH_DENIED GITHUB_READ_TOKEN_MISSING GITHUB_READ_UNAVAILABLE HARU_SYNC_FAILED HELPER_COMMAND_FAILED HITL_NOT_OBSERVED HOSTED_WINDOWS_REQUIRED INSTALLED_PROCESS_IDENTITY_MISMATCH INSTALLER_SHA_MISMATCH INSTALLER_UNEXPECTED_LAUNCH INVALID_HARNESS LEDGER_PERSISTENCE_FAILED LEDGER_SHAPE_INVALID LEDGER_UNAVAILABLE MANUAL_APPROVAL_UNVERIFIED NOT_STARTED OWNED_PROCESS_CLEANUP_FAILED PASSED PAYLOAD_COUNT_MISMATCH PREEXISTING_PRODUCT_PROCESS PREPARED_IDENTITY_MISMATCH PREVIOUS_SCENARIO_FAILED PRIOR_JOB_HISTORY_INCOMPLETE PRIOR_RUN_UNRESOLVED PRODUCT_ORIGIN_INVALID PRODUCT_SECURITY_ASSERTION_FAILED PRODUCT_SOURCE_MISMATCH PROFILE_CLEANUP_FAILED PROFILE_NOT_FRESH PROTECTED_ENVIRONMENT_MISSING PROVIDER_BUDGET_BLOCKED REPORT_INVALID REQUEST_MARKER_MISMATCH REQUEST_MUST_BE_EMPTY_CHILD REQUIRED_REVIEWER_MISSING RERUN_FORBIDDEN RUN_HISTORY_GAP RUN_HISTORY_INCOMPLETE RUN_HISTORY_WRONG_WORKFLOW SCENARIO_EVIDENCE_INVALID SCRATCH_NOT_FRESH SECRET_PRESENT_DURING_PREPARATION SESSION_ALREADY_CONSUMED SESSION_DEADLINE SETTINGS_SAVE_FAILED SINGLE_OWNER_SELF_REVIEW_CONFIGURATION_BLOCKED STREAM_NOT_OBSERVED SUITE_DEADLINE SYNTHETIC_API_ROUTE_DENIED SYNTHETIC_PROFILE_INVALID SYNTHETIC_SETUP_FAILED TRIGGER_NOT_AUTHORIZED UI_ACTION_FAILED UI_ASSERTION_FAILED UI_CASES_INCOMPLETE UI_TIMEOUT UNEXPECTED_DIALOG UNEXPECTED_PROVIDER_REQUESTS UNEXPECTED_RENDERER_NETWORK UNOWNED_PROFILE_PRESERVED UNSAFE_SCREENSHOT VALIDATION_BLOCKED'.split(' '));
export function demand(condition, code) { if (!condition) throw Object.assign(new Error(code), { safeCode: code }); }
export function safeCode(error) { return EVIDENCE_CODES.has(error?.safeCode) ? error.safeCode : 'VALIDATION_BLOCKED'; }
export function validateLiveProduct() {
  demand(PIN.fullRegressionRunId !== null, 'FULL_GATE_PROVENANCE_REQUIRED');
}
export async function validateFixedFiles() {
  validateProduct(JSON.parse(await fs.readFile(new URL('./product.json', import.meta.url), 'utf8')));
  const manifest = JSON.parse(await fs.readFile(new URL('./request.json', import.meta.url), 'utf8'));
  assert.deepEqual(manifest, { schema: 1, requestId: 'offerpilot-fixed-exe-real-ai-20261008',
    environment: ENVIRONMENT, workflow: WORKFLOW, limits: LIMITS, scope: RECOVERY.scope, cases: RECOVERY.allowedCases,
    carryForward: { runId: RECOVERY.source.runId, artifactId: RECOVERY.source.artifactId, digest: RECOVERY.source.digest },
    execution: { additionalRequests: RECOVERY.additionalRequests, seconds: RECOVERY.executionMs / 1000 },
    transport: 'fixed-exe-ui-loopback-budget-broker-real-provider', profile: 'single-synthetic-fresh-profile',
    rawEvidence: false, screenshots: false, reruns: false });
}
export function validateTrigger(event, env, head, helper, branchHead) {
  demand(env.GITHUB_EVENT_NAME === 'push' && env.GITHUB_REPOSITORY === PIN.repository &&
    env.GITHUB_REF === `refs/heads/${PIN.branch}`, 'TRIGGER_NOT_AUTHORIZED');
  demand(env.GITHUB_RUN_ATTEMPT === '1', 'RERUN_FORBIDDEN');
  const approved = env.AI_APPROVED_HELPER_SHA, request = env.AI_REQUEST_SHA;
  demand(/^[0-9a-f]{40}$/.test(approved || '') && /^[0-9a-f]{40}$/.test(request || ''), 'EXACT_SHA_APPROVAL_MISSING');
  demand(env.GITHUB_SHA === request && event.after === request && event.before === approved &&
    !event.deleted && !event.forced && event.repository?.full_name === PIN.repository &&
    event.head_commit?.id === request && event.head_commit?.message?.trim() === marker(approved), 'REQUEST_MARKER_MISMATCH');
  demand(head.sha === request && helper.sha === approved && branchHead.object?.sha === request &&
    head.parents?.length === 1 && head.parents[0].sha === approved && head.tree?.sha === helper.tree?.sha &&
    /^[0-9a-f]{40}$/.test(head.tree?.sha || '') && head.message?.trim() === marker(approved), 'REQUEST_MUST_BE_EMPTY_CHILD');
  return { helperSha: approved, requestSha: request };
}
export function validateEnvironment(environment, branches, reviewerLogin = REVIEWER.login) {
  demand(environment?.name === ENVIRONMENT && Number.isSafeInteger(environment.id) && environment.id > 0,
    'PROTECTED_ENVIRONMENT_MISSING');
  demand(environment.can_admins_bypass === false, 'ENVIRONMENT_BYPASS_NOT_DISABLED');
  const rules = environment.protection_rules?.filter(rule => rule.type === 'required_reviewers');
  demand(rules?.length === 1 && rules[0].reviewers?.length === 1, 'REQUIRED_REVIEWER_MISSING');
  demand(rules[0].prevent_self_review === false, 'SINGLE_OWNER_SELF_REVIEW_CONFIGURATION_BLOCKED');
  const reviewer = rules[0].reviewers[0];
  demand(/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(reviewerLogin || '') && reviewer.type === 'User' &&
    reviewer.reviewer?.type === 'User' && reviewer.reviewer.login === reviewerLogin && reviewerLogin === REVIEWER.login &&
    reviewer.reviewer.id === REVIEWER.id, 'EXACT_HUMAN_REVIEWER_MISSING');
  demand(environment.deployment_branch_policy?.protected_branches === false &&
    environment.deployment_branch_policy?.custom_branch_policies === true, 'BRANCH_PROTECTION_MISSING');
  demand(branches?.total_count === 1 && branches.branch_policies?.length === 1 &&
    branches.branch_policies[0].name === PIN.branch && branches.branch_policies[0].type === 'branch', 'EXACT_BRANCH_POLICY_MISSING');
  return { environmentId: environment.id, reviewerId: reviewer.reviewer.id };
}
export function validateReview(reviews, environmentId, reviewerId) {
  demand(Array.isArray(reviews) && reviews.length > 0, 'MANUAL_APPROVAL_UNVERIFIED');
  const relevant = reviews.filter(review => review.environments?.some(item => item.id === environmentId && item.name === ENVIRONMENT));
  demand(relevant.length === 1 && relevant[0].state === 'approved' && relevant[0].user?.type === 'User' &&
    relevant[0].user.id === reviewerId, 'MANUAL_APPROVAL_UNVERIFIED');
}
export function validateHistory(runs, currentRun) {
  demand(runs && Number.isInteger(runs.total_count) && runs.total_count > 0 && runs.total_count <= 100 &&
    runs.total_count === runs.workflow_runs?.length, 'RUN_HISTORY_INCOMPLETE');
  const all = runs.workflow_runs;
  demand(all.every(run => Number.isSafeInteger(run.id) && run.id > 0 && run.run_attempt === 1) &&
    new Set(all.map(run => run.id)).size === all.length, 'RERUN_FORBIDDEN');
  demand(all.filter(run => String(run.id) === String(currentRun)).length === 1, 'CURRENT_RUN_NOT_IN_HISTORY');
  const numbers = all.map(run => run.run_number).sort((a, b) => a - b);
  demand(numbers.every((number, index) => number === index + 1), 'RUN_HISTORY_GAP');
  demand(all.every(run => run.path?.split('@')[0] === WORKFLOW), 'RUN_HISTORY_WRONG_WORKFLOW');
  return all.filter(run => String(run.id) !== String(currentRun));
}
export function validatePriorJobs(jobs, { run, carryForward } = {}) {
  demand(jobs && Number.isInteger(jobs.total_count) && jobs.total_count > 0 && jobs.total_count <= 100 && jobs.total_count === jobs.jobs?.length,
    'PRIOR_JOB_HISTORY_INCOMPLETE');
  const paid = jobs.jobs.filter(job => job.name === PAID_JOB);
  if (run?.id === RECOVERY.source.runId) {
    validateCarryForward(carryForward, 'live');
    demand(run.head_sha === RECOVERY.source.requestCommit && run.path === WORKFLOW && run.run_attempt === 1 &&
      run.status === 'completed' && run.conclusion === 'failure' && run.event === 'push' && run.head_branch === PIN.branch &&
      run.repository?.full_name === PIN.repository && run.head_repository?.full_name === PIN.repository,
      'CARRY_FORWARD_SOURCE_UNVERIFIED');
    demand(paid.length === 1 && paid[0].run_id === run.id && paid[0].head_sha === run.head_sha &&
      paid[0].status === 'completed' && paid[0].conclusion === 'failure', 'SESSION_ALREADY_CONSUMED');
    const upload = paid[0].steps?.filter(step => step.name === RECOVERY.sourceUploadStep);
    demand(upload?.length === 1 && upload[0].status === 'completed' && upload[0].conclusion === 'success', 'CARRY_FORWARD_SOURCE_UNVERIFIED');
    return;
  }
  demand(paid.length === 1 && paid[0].status === 'completed' && paid[0].conclusion === 'skipped', 'SESSION_ALREADY_CONSUMED');
}
export function childEnvironment(env) {
  const allowed = /^(SystemRoot|WINDIR|SystemDrive|COMSPEC|PATH|PATHEXT|TEMP|TMP|APPDATA|LOCALAPPDATA|USERPROFILE|HOMEDRIVE|HOMEPATH|ProgramFiles|ProgramFiles\(x86\)|ProgramData|ALLUSERSPROFILE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE|OS|SESSIONNAME)$/i;
  return { ...Object.fromEntries(Object.entries(env).filter(([key]) => allowed.test(key))),
    LITELLM_LOCAL_MODEL_COST_MAP: 'True', PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' };
}

export function validateOfflineRun(run, jobs, helperSha) {
  demand(run?.head_sha === helperSha && run.path?.split('@')[0] === WORKFLOW && run.event === 'push' &&
    run.head_branch === PIN.branch && run.run_attempt === 1 && run.status === 'completed' && run.conclusion === 'success' &&
    run.repository?.full_name === PIN.repository && run.head_repository?.full_name === PIN.repository,
    'SAME_HELPER_MOCK_RUN_REQUIRED');
  demand(jobs && Number.isInteger(jobs.total_count) && jobs.total_count === jobs.jobs?.length, 'MOCK_JOB_HISTORY_INCOMPLETE');
  const offline = jobs.jobs.filter(job => job.name === OFFLINE_JOB);
  demand(offline.length === 1 && offline[0].head_sha === helperSha && offline[0].run_id === run.id &&
    offline[0].status === 'completed' && offline[0].conclusion === 'success', 'SAME_HELPER_MOCK_JOB_REQUIRED');
}

// LIVE-only certification: installed-ui intentionally checks provenance identity
// without making this assertion, so do not weaken or reuse its generic semantics.
export function validateCurrentFullGate(run, jobs) {
  validateLiveProduct();
  demand(run?.id === PIN.fullRegressionRunId && run.head_sha === PIN.commit &&
    run.path === '.github/workflows/desktop-windows.yml' && run.event === 'push' && run.head_branch === PIN.branch &&
    run.run_attempt === 1 && run.status === 'completed' && run.conclusion === 'success' &&
    run.repository?.full_name === PIN.repository && run.head_repository?.full_name === PIN.repository &&
    Number.isSafeInteger(run.repository.id) && run.repository.id > 0 && run.head_repository.id === run.repository.id,
    'FULL_GATE_SUCCESS_REQUIRED');
  demand(jobs && Number.isSafeInteger(jobs.total_count) && jobs.total_count > 0 && jobs.total_count <= 100 &&
    jobs.total_count === jobs.jobs?.length && new Set(jobs.jobs.map(job => job.id)).size === jobs.jobs.length &&
    jobs.jobs.every(job => Number.isSafeInteger(job.id) && job.id > 0 && job.run_id === run.id && job.head_sha === PIN.commit && job.status === 'completed'),
    'FULL_GATE_SUCCESS_REQUIRED');
  for (const name of ['Full release regression (required for release)', 'Validation results (both jobs must pass)']) {
    const selected = jobs.jobs.filter(job => job.name === name);
    demand(selected.length === 1 && selected[0].conclusion === 'success', 'FULL_GATE_SUCCESS_REQUIRED');
  }
}
