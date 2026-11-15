import { PRICE_REVIEW, usageCost } from './broker-core.cjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { SCREEN_IDS, SKIP_CODES, GUARD_REASONS } from './mock-screenshots.mjs';
import { sanitizeUiDiagnostic, CONTINUATION_CODES, MOCK_PILOT_CHECK_MAP } from './ui-diagnostics.mjs';
import { CASES, PIN, demand, EVIDENCE_CODES } from './contract.mjs';
import { HARU_SESSION_CHECKS, HARU_SESSION_STAGES, HARU_SESSION_DIAGNOSTIC_FLAGS } from './mock-haru-session.mjs';
import { RECOVERY, validateCarryForwardPrefix } from './carry-forward-policy.cjs';
const RECOVERY_SUCCESS = ['PILOT_HARU_RECOVERY_PASSED', 'PILOT_HARU_RECOVERY_COMPLETION_ONLY'];
const FULL_SUCCESS = ['ALL_UI_CASES_PASSED', 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY'];
const isRecovery = scope => scope === RECOVERY.scope;
const validScope = scope => scope === undefined || scope === 'full-suite' || isRecovery(scope);
const CHECKS = new Set('sustainedStreamingObserved freshConversationProven uiAdmissionProven naturalCompletionProven terminalIdentityProven terminalMirrorProven terminalTextProven ledgerUnchanged admissionObserverCleanupPassed settingsSavedThroughUi connectionTestClicked connectionSucceeded incrementalAssistantRendering haruRunningAndIdleMirrored haruVisibleAssistantMatchesSnapshot hitlVisible rejectedThroughUi syntheticWriteAbsent haruPendingAndIdleMirrored stopClickedWhileRunning stopAcknowledged haruRunningAndStoppedMirrored sourceAndResumeSelected disclosureAccepted generatedProposalVisible classificationPreviewVisible cancelledThroughUi sourceUnchanged inputReviewedThroughUi generatedDraftVisible finalSaveNotSubmitted providerRequestObserved providerRequestSettled oneProviderRequestVerified productDisconnectObserved providerDisconnectObserved ownerRunningTransitionObserved haruRunningTransitionObserved positiveRunningConversationMatched finalRunningConversationMatched brokerCleanupFailed mirrorObserverCleanupFailed streamObserverCleanupFailed'.split(' '));
export function safeHaruSessionDiagnostic(value, isMock) {
  demand(isMock === true && value && HARU_SESSION_STAGES.includes(value.stage)
    && Object.keys(value).sort().join('|') === ['stage', ...HARU_SESSION_DIAGNOSTIC_FLAGS].sort().join('|')
    && HARU_SESSION_DIAGNOSTIC_FLAGS.every(key => typeof value[key] === 'boolean'), 'REPORT_INVALID');
  return { stage: value.stage, ...Object.fromEntries(HARU_SESSION_DIAGNOSTIC_FLAGS.map(key => [key, value[key]])) };
}
export function safeResults(results, isMock = false, scope) {
  demand(validScope(scope), 'SCENARIO_EVIDENCE_INVALID');
  const expected = isRecovery(scope) ? RECOVERY.allowedCases : CASES;
  demand(Array.isArray(results) && results.length === expected.length, 'SCENARIO_EVIDENCE_INVALID');
  return results.map((row, index) => {
    demand(row.id === expected[index] && ['PASS', 'FAIL', 'BLOCKED'].includes(row.status) &&
      EVIDENCE_CODES.has(row.code), 'SCENARIO_EVIDENCE_INVALID');
    const checks = Object.fromEntries(Object.entries(row.checks || {}).map(([key, value]) => {
      demand((CHECKS.has(key) || (isMock && row.id === 'pilot-stream'
        && [...HARU_SESSION_CHECKS, ...Object.values(MOCK_PILOT_CHECK_MAP)].includes(key)))
        && typeof value === 'boolean', 'SCENARIO_EVIDENCE_INVALID');
      return [key, value];
    }));
    demand(isMock || row.id !== 'pilot-stream' || row.status !== 'PASS'
      || ['LIVE_COMPLETION_ONLY', 'LIVE_STREAMING_PROVEN'].includes(row.code), 'SCENARIO_EVIDENCE_INVALID');
    demand(![...FULL_SUCCESS, ...RECOVERY_SUCCESS].includes(row.code)
      && (!isMock || !row.code.startsWith('LIVE_')), 'SCENARIO_EVIDENCE_INVALID');
    demand(!isRecovery(scope) || row.status !== 'PASS' || (isMock
      ? row.code === 'MOCK_PILOT_STREAMING_PROVEN'
      : ['LIVE_COMPLETION_ONLY', 'LIVE_STREAMING_PROVEN'].includes(row.code)), 'SCENARIO_EVIDENCE_INVALID');
    const exclusiveLiveChecks = ['sustainedStreamingObserved', 'freshConversationProven', 'uiAdmissionProven',
      'naturalCompletionProven', 'terminalIdentityProven', 'terminalMirrorProven', 'terminalTextProven',
      'ledgerUnchanged', 'admissionObserverCleanupPassed'];
    demand(!exclusiveLiveChecks.some(key => Object.hasOwn(checks, key)) || (!isMock && row.id === 'pilot-stream'),
      'SCENARIO_EVIDENCE_INVALID');
    const liveCode = ['LIVE_COMPLETION_ONLY', 'LIVE_STREAMING_PROVEN'].includes(row.code);
    const liveChecks = Object.hasOwn(checks, 'sustainedStreamingObserved');
    if (liveCode || liveChecks) {
      demand(isMock === false && row.id === 'pilot-stream' && liveChecks, 'SCENARIO_EVIDENCE_INVALID');
      demand(!liveCode || row.status === 'PASS', 'SCENARIO_EVIDENCE_INVALID');
      if (row.status === 'PASS') {
        demand(liveCode && ['freshConversationProven', 'uiAdmissionProven', 'naturalCompletionProven',
          'terminalIdentityProven', 'terminalMirrorProven', 'terminalTextProven', 'ledgerUnchanged',
          'admissionObserverCleanupPassed', 'providerRequestSettled', 'oneProviderRequestVerified',
          'haruVisibleAssistantMatchesSnapshot'].every(key => checks[key] === true)
          && !['brokerCleanupFailed', 'mirrorObserverCleanupFailed', 'streamObserverCleanupFailed'].some(key => checks[key] === true),
        'SCENARIO_EVIDENCE_INVALID');
        const sustained = row.code === 'LIVE_STREAMING_PROVEN';
        demand(checks.sustainedStreamingObserved === sustained && checks.incrementalAssistantRendering === sustained
          && (!sustained || ['ownerRunningTransitionObserved', 'haruRunningTransitionObserved',
            'positiveRunningConversationMatched', 'finalRunningConversationMatched', 'haruRunningAndIdleMirrored']
            .every(key => checks[key] === true)), 'SCENARIO_EVIDENCE_INVALID');
        demand(['ownerRunningTransitionObserved', 'haruRunningTransitionObserved', 'positiveRunningConversationMatched',
          'finalRunningConversationMatched', 'haruRunningAndIdleMirrored'].every(key => typeof checks[key] === 'boolean')
          && checks.positiveRunningConversationMatched === checks.finalRunningConversationMatched
          && checks.haruRunningAndIdleMirrored === checks.positiveRunningConversationMatched
          && (!checks.positiveRunningConversationMatched || (checks.ownerRunningTransitionObserved && checks.haruRunningTransitionObserved)),
        'SCENARIO_EVIDENCE_INVALID');
      }
    }
    demand(!['LIVE_COMPLETION_ONLY', 'LIVE_STREAMING_PROVEN'].includes(row.code) || liveChecks, 'SCENARIO_EVIDENCE_INVALID');
    const mockCode = row.code === 'MOCK_PILOT_STREAMING_PROVEN';
    const mockProofKeys = Object.values(MOCK_PILOT_CHECK_MAP);
    const mockChecks = mockProofKeys.some(key => Object.hasOwn(checks, key));
    if (mockCode || mockChecks) {
      demand(isMock && row.id === 'pilot-stream' && mockChecks
        && mockProofKeys.every(key => typeof checks[key] === 'boolean'), 'SCENARIO_EVIDENCE_INVALID');
      demand(!mockCode || row.status === 'PASS', 'SCENARIO_EVIDENCE_INVALID');
      if (row.status === 'PASS') {
        demand(mockCode && mockProofKeys.every(key => checks[key] === true)
          && ['providerRequestSettled', 'oneProviderRequestVerified', 'haruVisibleAssistantMatchesSnapshot',
            'incrementalAssistantRendering', 'ownerRunningTransitionObserved', 'haruRunningTransitionObserved',
            'positiveRunningConversationMatched', 'finalRunningConversationMatched', 'haruRunningAndIdleMirrored']
            .every(key => checks[key] === true)
          && !['brokerCleanupFailed', 'mirrorObserverCleanupFailed', 'streamObserverCleanupFailed'].some(key => checks[key] === true),
        'SCENARIO_EVIDENCE_INVALID');
      }
    }
    demand(row.code !== 'MOCK_PILOT_COMPLETION_UNPROVEN' || (isMock && row.status !== 'PASS'), 'SCENARIO_EVIDENCE_INVALID');
    let continuation;
    if (row.continuation !== undefined) {
      const value = row.continuation;
      demand(!isRecovery(scope) && isMock === true && row.id === 'pilot-stream' && row.status === 'FAIL'
        && row.code === 'STREAM_NOT_OBSERVED' && row.diagnostic?.stage === 'PILOT_STREAM_READBACK'
        && ['PROVEN', 'BLOCKED'].includes(value?.status) && CONTINUATION_CODES.includes(value.code), 'SCENARIO_EVIDENCE_INVALID');
      continuation = { status: value.status, code: value.code };
      for (const key of ['eligible', 'ledgerSafe', 'mirrorProven', 'domEqual', 'noNewRequests', 'cleanupPassed', 'continued']) {
        demand(typeof value[key] === 'boolean', 'SCENARIO_EVIDENCE_INVALID'); continuation[key] = value[key];
      }
      demand(!continuation.continued || (continuation.status === 'PROVEN'
        && continuation.code === 'MOCK_CONTINUATION_PROVEN'
        && ['eligible', 'ledgerSafe', 'mirrorProven', 'domEqual', 'noNewRequests', 'cleanupPassed']
          .every(key => continuation[key])), 'SCENARIO_EVIDENCE_INVALID');
    }
    return { id: row.id, status: row.status, code: row.code, checks, diagnostic: sanitizeUiDiagnostic(row.diagnostic, isMock && row.id === 'pilot-stream'),
      ...(continuation === undefined ? {} : { continuation }) };
  });
}
// No arbitrary strings, error messages, provider response bodies or nested objects can cross this boundary.
export function numericLedger(snapshot) {
  const output = { schema: 1, kind: 'bounded-real-provider-cost-counters', currency: 'CNY',
    priceCheckedAt: PRICE_REVIEW.priceCheckedAt, priceValidUntilUtc: PRICE_REVIEW.validUntilUtc,
    sessionId: 'offerpilot-fixed-exe-real-ai-20261008', model: 'deepseek-flash',
    productCommit: PIN.commit, buildRunId: PIN.runId, artifactId: PIN.artifactId };
  if (!snapshot || Object.keys(snapshot).length === 0) return { ...output, unavailable: true, budgetUnavailableMicroCny: 10000000 };
  demand(snapshot.model === 'deepseek-flash' && snapshot.budgetMicroCny === 10000000 && snapshot.reserveMicroCny === 3000000 &&
    snapshot.settledMicroCny + snapshot.retainedMicroCny <= 10000000, 'LEDGER_SHAPE_INVALID');
  for (const key of ['budgetMicroCny', 'reserveMicroCny', 'sentRequests', 'settledMicroCny', 'retainedMicroCny']) {
    demand(Number.isSafeInteger(snapshot[key]) && snapshot[key] >= 0, 'LEDGER_SHAPE_INVALID'); output[key] = snapshot[key];
  }
  for (const key of ['active', 'closed', 'journalFailed']) {
    demand(typeof snapshot[key] === 'boolean', 'LEDGER_SHAPE_INVALID'); output[key] = snapshot[key];
  }
  const reasons = ['AUTH', 'ROUTE', 'CLOSED', 'DEADLINE', 'UNARMED', 'BUSY', 'CASE', 'BUDGET', 'COUNT',
    'BODY', 'MODEL', 'PARAMETER', 'CANCELLED', 'DISCONNECT', 'TIMEOUT', 'LEDGER', 'UPSTREAM', 'REDIRECT', 'PROTOCOL', 'USAGE', 'SETTLED', 'EXPIRED', 'UPSTREAM_DISCONNECT'];
  demand(snapshot.provenance && typeof snapshot.provenance === 'object' && !Array.isArray(snapshot.provenance), 'LEDGER_SHAPE_INVALID');
  // Reject stale/mixed review windows and session identities before exporting:
  // current helper policy must never relabel an older snapshot or reset its budget.
  demand(Object.entries(PRICE_REVIEW).every(([key, value]) => snapshot.provenance[key] === value)
    && snapshot.provenance.sessionId === output.sessionId
    && snapshot.provenance.mode === 'FIXED_EXE_UI_BUDGET_BROKER', 'LEDGER_SHAPE_INVALID');
  demand(snapshot.provenance.productCommit === PIN.commit && snapshot.provenance.buildRunId === String(PIN.runId)
    && snapshot.provenance.artifactId === String(PIN.artifactId)
    && snapshot.provenance.installerSha256 === PIN.installerSha256, 'LEDGER_SHAPE_INVALID');
  if (snapshot.provenance) {
    for (const key of ['helperCommit', 'requestCommit']) {
      demand(/^[0-9a-f]{40}$/.test(snapshot.provenance[key] || ''), 'LEDGER_SHAPE_INVALID'); output[key] = snapshot.provenance[key];
    }
    demand(/^[0-9]{1,20}$/.test(snapshot.provenance.runId || '') && snapshot.provenance.runAttempt === 1, 'LEDGER_SHAPE_INVALID');
    output.runId = snapshot.provenance.runId; output.runAttempt = 1;
  }
  output.denied = {};
  for (const key of reasons) { demand(Number.isSafeInteger(snapshot.denied?.[key]) && snapshot.denied[key] >= 0,
    'LEDGER_SHAPE_INVALID'); output.denied[key] = snapshot.denied[key]; }
  demand(Array.isArray(snapshot.requests) && snapshot.requests.length === snapshot.sentRequests && snapshot.sentRequests <= 8,
    'LEDGER_SHAPE_INVALID');
  output.requests = snapshot.requests.map(row => {
    demand(CASES.includes(row.caseId) && [...reasons, 'RESERVED'].includes(row.status) &&
      ['INSERTED', 'TIGHTENED', 'UNCHANGED'].includes(row.envelope), 'LEDGER_SHAPE_INVALID');
    const safe = { caseId: row.caseId, status: row.status, envelope: row.envelope };
    for (const key of ['cap', 'micro', 'promptTokens', 'completionTokens', 'cacheHitTokens', 'cacheMissTokens']) {
      if (row[key] === undefined && !['cap', 'micro'].includes(key)) continue;
      demand(Number.isSafeInteger(row[key]) && row[key] >= 0, 'LEDGER_SHAPE_INVALID'); safe[key] = row[key];
    }
    for (const key of ['outboundStarted', 'upstreamResponded', 'clientDisconnectObserved']) {
      demand(typeof row[key] === 'boolean', 'LEDGER_SHAPE_INVALID'); safe[key] = row[key];
    }
    return safe;
  });
  if (snapshot.carryForward !== undefined) {
    let carry;
    try { carry = validateCarryForwardPrefix(snapshot, snapshot.mode === 'MOCK' ? 'mock' : 'live'); }
    catch { demand(false, 'LEDGER_SHAPE_INVALID'); }
    const current = output.requests.slice(carry.priorRequestCount);
    demand(current.every(row => row.caseId === 'pilot-stream' && row.cap > 0 && row.cap <= 4096), 'LEDGER_SHAPE_INVALID');
    let currentSettled = 0, currentRetained = 0;
    for (const row of current) {
      if (row.status === 'SETTLED') {
        let cost;
        try { cost = usageCost({ prompt_tokens: row.promptTokens, completion_tokens: row.completionTokens,
          prompt_cache_hit_tokens: row.cacheHitTokens, prompt_cache_miss_tokens: row.cacheMissTokens,
          total_tokens: row.promptTokens + row.completionTokens }, row.cap); }
        catch { demand(false, 'LEDGER_SHAPE_INVALID'); }
        demand(cost.micro === row.micro && row.outboundStarted === true && row.upstreamResponded === true
          && row.clientDisconnectObserved === false, 'LEDGER_SHAPE_INVALID');
        currentSettled += row.micro;
      } else {
        demand(row.micro === output.reserveMicroCny, 'LEDGER_SHAPE_INVALID');
        currentRetained += row.micro;
      }
    }
    demand(snapshot.settledMicroCny === carry.priorSettledMicroCny + currentSettled
      && snapshot.retainedMicroCny === carry.priorRetainedMicroCny + currentRetained, 'LEDGER_SHAPE_INVALID');
    output.carryForward = structuredClone(carry);
    output.currentRequests = snapshot.currentRequests;
    output.currentSettledMicroCny = currentSettled;
    output.currentRetainedMicroCny = currentRetained;
  } else if (snapshot.currentRequests !== undefined) {
    demand(snapshot.currentRequests === snapshot.sentRequests, 'LEDGER_SHAPE_INVALID');
  }
  return output;
}
export function safeScreenshotEvidence(value, isMock) {
  const captured = value?.captured || [], skipped = value?.skipped || [];
  demand(Array.isArray(captured) && Array.isArray(skipped) && captured.length <= SCREEN_IDS.length &&
    skipped.length <= SCREEN_IDS.length && new Set(captured).size === captured.length, 'REPORT_INVALID');
  demand(isMock || (!captured.length && !skipped.length), 'REPORT_INVALID');
  demand(captured.every(id => SCREEN_IDS.includes(id)), 'REPORT_INVALID');
  return { captured: [...captured], skipped: skipped.map(row => {
    demand(SCREEN_IDS.includes(row?.id) && SKIP_CODES.includes(row?.code), 'REPORT_INVALID');
    demand(row.reason === undefined || (row.code === 'SCREEN_GUARD_REJECTED' && GUARD_REASONS.includes(row.reason) && row.reason !== 'PASSED'), 'REPORT_INVALID');
    return { id: row.id, code: row.code, ...(row.reason === undefined ? {} : { reason: row.reason }) };
  }) };
}
export async function saveEvidence(directory, report, ledger, secrets = []) {
  demand(report.mode === undefined || ['live', 'mock'].includes(report.mode), 'REPORT_INVALID');
  const isMock = report.mode === 'mock';
  demand(validScope(report.scope), 'REPORT_INVALID');
  const recovery = isRecovery(report.scope);
  const cost = numericLedger(ledger);
  demand(recovery ? cost.unavailable === true || cost.carryForward !== undefined
    : cost.carryForward === undefined, 'REPORT_INVALID');
  if (recovery && cost.unavailable !== true) {
    try { validateCarryForwardPrefix(ledger, isMock ? 'mock' : 'live'); }
    catch { demand(false, 'REPORT_INVALID'); }
    demand(isMock ? ledger.mode === 'MOCK' && ledger.mock?.transport === 'IN_PROCESS_SYNTHETIC_HTTPS'
      && ledger.mock.externalNetwork === 'DENIED' && ledger.mock.realProviderCalls === 0
      && ledger.mock.usageIsSynthetic === true && ledger.mock.fakeRequests === ledger.currentRequests
      && ledger.mock.clockScope === 'BROKER_VM_ONLY'
      : ledger.mode !== 'MOCK' && ledger.mock === undefined, 'REPORT_INVALID');
  }
  const currentRows = cost.requests?.slice(cost.carryForward?.priorRequestCount ?? 0) ?? [];
  const screenshotEvidence = safeScreenshotEvidence(report.screenshotEvidence, isMock);
  const safe = { schema: 1, mode: isMock ? 'MOCK' : 'LIVE', realProviderCalled: !isMock && currentRows.some(row => row.outboundStarted === true), productCommit: PIN.commit, buildRunId: PIN.runId, artifactId: PIN.artifactId,
    fullRegressionRunId: PIN.fullRegressionRunId,
    fullRegression: PIN.fullRegressionRunId === null ? 'not-run-package-only' : 'independent-not-certified',
    installerSha256: PIN.installerSha256, releaseReady: false, independentFullGateCertified: false,
    temporaryLoopbackDebugging: true, normalUndebuggedLaunchValidated: false, ordinaryUserUacSmartScreenValidated: false,
    route: isMock ? 'real-installed-ui-via-loopback-broker-to-synthetic-mock-transport' : 'real-installed-ui-via-loopback-budget-broker-to-real-provider',
    screenshotsCaptured: screenshotEvidence.captured.length > 0, screenshotEvidence, rawLogsCaptured: false, status: report.status,
    code: report.code, cleanupCode: report.cleanupCode || 'CLEANUP_PENDING', scenarios: safeResults(report.scenarios, isMock, report.scope), cleanupPassed: report.cleanupPassed === true,
    ...(report.scope === undefined ? {} : { scope: report.scope }),
    ...(recovery ? { notRunCases: CASES.filter(id => !RECOVERY.allowedCases.includes(id))
      .map(id => ({ id, status: 'NOT_RUN', code: 'OUTSIDE_RECOVERY_SCOPE' })) } : {}) };
  demand(['PASS', 'FAIL', 'BLOCKED'].includes(safe.status) && EVIDENCE_CODES.has(safe.code), 'REPORT_INVALID');
  demand(['CLEANUP_PASSED', 'CLEANUP_PENDING', 'BROKER_CLEANUP_FAILED', 'LEDGER_UNAVAILABLE', 'LEDGER_PERSISTENCE_FAILED', 'PROFILE_CLEANUP_FAILED', 'OWNED_PROCESS_CLEANUP_FAILED', 'EVIDENCE_WRITE_BLOCKED'].includes(safe.cleanupCode), 'REPORT_INVALID');
  demand(safe.status !== 'PASS' || (safe.cleanupPassed && safe.cleanupCode === 'CLEANUP_PASSED' &&
    safe.scenarios.every(row => row.status === 'PASS') && ledger?.journalFailed === false && ledger?.closed === true &&
    ledger?.active === false && (recovery
      ? cost.currentRequests === 1 && cost.sentRequests === 2 && cost.retainedMicroCny === 0
        && currentRows.length === 1 && currentRows[0].caseId === 'pilot-stream'
      : ledger.requests?.length === CASES.length && CASES.every(id => ledger.requests.filter(row => row.caseId === id).length === 1))), 'REPORT_INVALID');
  if (report.executionTiming !== undefined) {
    const timing = report.executionTiming;
    demand(recovery && timing && typeof timing === 'object' && !Array.isArray(timing)
      && Object.keys(timing).sort().join('|') === 'elapsedBeforeUiMs|remainingBeforeUiMs|windowMs'
      && timing.windowMs === RECOVERY.executionMs
      && Number.isSafeInteger(timing.elapsedBeforeUiMs) && timing.elapsedBeforeUiMs >= 0 && timing.elapsedBeforeUiMs <= 600000
      && Number.isSafeInteger(timing.remainingBeforeUiMs) && timing.remainingBeforeUiMs >= 0
      && timing.remainingBeforeUiMs <= RECOVERY.executionMs, 'REPORT_INVALID');
    safe.executionTiming = { windowMs: RECOVERY.executionMs, elapsedBeforeUiMs: timing.elapsedBeforeUiMs,
      remainingBeforeUiMs: timing.remainingBeforeUiMs };
  }
  if (report.haruSessionDiagnostic !== undefined) {
    safe.haruSessionDiagnostic = safeHaruSessionDiagnostic(report.haruSessionDiagnostic, isMock);
  }
  const haruScope = safe.scenarios.find(row => row.id === 'pilot-stream')?.checks || {};
  if (HARU_SESSION_CHECKS.some(key => Object.hasOwn(haruScope, key))) {
    demand(isMock && (safe.status !== 'PASS' || HARU_SESSION_CHECKS.every(key => haruScope[key] === true)), 'REPORT_INVALID');
  }
  const livePilotProven = safe.scenarios.some(row => ['LIVE_COMPLETION_ONLY', 'LIVE_STREAMING_PROVEN'].includes(row.code));
  const mockPilotProven = safe.scenarios.some(row => row.code === 'MOCK_PILOT_STREAMING_PROVEN');
  if (livePilotProven || mockPilotProven) {
    demand(livePilotProven ? ledger?.mode !== 'MOCK' && ledger?.mock === undefined
      : isMock && ledger?.mode === 'MOCK' && ledger.mock?.transport === 'IN_PROCESS_SYNTHETIC_HTTPS'
        && ledger.mock.externalNetwork === 'DENIED' && ledger.mock.realProviderCalls === 0
        && ledger.mock.usageIsSynthetic === true && ledger.mock.fakeRequests === (recovery ? ledger.currentRequests : ledger.sentRequests),
    'REPORT_INVALID');
    const rows = ledger?.requests?.filter(row => row.caseId === 'pilot-stream');
    demand(rows?.length === 1 && rows[0].status === 'SETTLED' && rows[0].outboundStarted === true
      && rows[0].upstreamResponded === true && rows[0].clientDisconnectObserved === false
      && (safe.status !== 'PASS' || (ledger.journalFailed === false && Object.values(ledger.denied ?? {}).every(value => value === 0)))
      && Number.isSafeInteger(rows[0].cap) && rows[0].cap > 0 && rows[0].cap <= 4096, 'REPORT_INVALID');
    const row = rows[0];
    let validUsage = false;
    try { validUsage = usageCost({ prompt_tokens: row.promptTokens, completion_tokens: row.completionTokens,
      prompt_cache_hit_tokens: row.cacheHitTokens, prompt_cache_miss_tokens: row.cacheMissTokens,
      total_tokens: row.promptTokens + row.completionTokens }, row.cap).micro === row.micro; } catch { /* Invalid usage never certifies a report. */ }
    demand(validUsage, 'REPORT_INVALID');
  }
  const completionOnly = safe.scenarios.some(row => row.code === 'LIVE_COMPLETION_ONLY');
  safe.coverageLimitations = completionOnly ? ['PILOT_SUSTAINED_STREAMING_NOT_PROVEN'] : [];
  if (HARU_SESSION_CHECKS.some(key => Object.hasOwn(haruScope, key))) {
    safe.coverageLimitations.push('UNSENT_DRAFT_RESTART_PERSISTENCE_NOT_TESTED');
  }
  const expectedSuccess = recovery
    ? (completionOnly ? 'PILOT_HARU_RECOVERY_COMPLETION_ONLY' : 'PILOT_HARU_RECOVERY_PASSED')
    : (completionOnly ? 'ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY' : 'ALL_UI_CASES_PASSED');
  demand(safe.status !== 'PASS' || safe.code === expectedSuccess, 'REPORT_INVALID');
  demand(recovery ? !FULL_SUCCESS.includes(safe.code) : !RECOVERY_SUCCESS.includes(safe.code), 'REPORT_INVALID');
  demand(!RECOVERY_SUCCESS.includes(safe.code) || safe.status === 'PASS', 'REPORT_INVALID');
  demand(!['ALL_UI_CASES_PASSED_WITH_COMPLETION_ONLY', 'PILOT_HARU_RECOVERY_COMPLETION_ONLY'].includes(safe.code)
    || (!isMock && completionOnly), 'REPORT_INVALID');
  const texts = [JSON.stringify(safe, null, 2) + '\n', JSON.stringify({ ...cost, mode: isMock ? 'MOCK' : 'LIVE', billingKind: isMock ? 'simulated-counters-only' : 'real-provider-conservative-accounting' }, null, 2) + '\n'];
  for (const secret of secrets) if (typeof secret === 'string' && secret.length > 0) {
    demand(texts.every(text => !text.includes(secret)), 'EVIDENCE_CONTAINS_CREDENTIAL');
  }
  await fs.mkdir(directory, { recursive: true });
  // Commit the ledger first. A failed ledger write can never publish a new PASS.
  await fs.writeFile(path.join(directory, 'ledger.json.tmp'), texts[1], { flag: 'w' });
  await fs.rename(path.join(directory, 'ledger.json.tmp'), path.join(directory, 'ledger.json'));
  await fs.writeFile(path.join(directory, 'result.json.tmp'), texts[0], { flag: 'w' });
  await fs.rename(path.join(directory, 'result.json.tmp'), path.join(directory, 'result.json'));
}
