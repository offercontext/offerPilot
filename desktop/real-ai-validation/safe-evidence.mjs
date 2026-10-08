import fs from 'node:fs/promises';
import path from 'node:path';
import { SCREEN_IDS, SKIP_CODES, GUARD_REASONS } from './mock-screenshots.mjs';
import { sanitizeUiDiagnostic } from './ui-diagnostics.mjs';
import { CASES, PIN, demand, EVIDENCE_CODES } from './contract.mjs';
const CHECKS = new Set('settingsSavedThroughUi connectionTestClicked connectionSucceeded incrementalAssistantRendering haruRunningAndIdleMirrored haruVisibleAssistantMatchesSnapshot hitlVisible rejectedThroughUi syntheticWriteAbsent haruPendingAndIdleMirrored stopClickedWhileRunning stopAcknowledged haruRunningAndStoppedMirrored sourceAndResumeSelected disclosureAccepted generatedProposalVisible classificationPreviewVisible cancelledThroughUi sourceUnchanged inputReviewedThroughUi generatedDraftVisible finalSaveNotSubmitted providerRequestObserved providerRequestSettled oneProviderRequestVerified productDisconnectObserved providerDisconnectObserved ownerRunningTransitionObserved haruRunningTransitionObserved positiveRunningConversationMatched finalRunningConversationMatched mirrorObserverCleanupFailed streamObserverCleanupFailed'.split(' '));
export function safeResults(results) {
  demand(Array.isArray(results) && results.length === CASES.length, 'SCENARIO_EVIDENCE_INVALID');
  return results.map((row, index) => {
    demand(row.id === CASES[index] && ['PASS', 'FAIL', 'BLOCKED'].includes(row.status) &&
      EVIDENCE_CODES.has(row.code), 'SCENARIO_EVIDENCE_INVALID');
    const checks = Object.fromEntries(Object.entries(row.checks || {}).map(([key, value]) => {
      demand(CHECKS.has(key) && typeof value === 'boolean', 'SCENARIO_EVIDENCE_INVALID');
      return [key, value];
    }));
    return { id: row.id, status: row.status, code: row.code, checks, diagnostic: sanitizeUiDiagnostic(row.diagnostic) };
  });
}
// No arbitrary strings, error messages, provider response bodies or nested objects can cross this boundary.
export function numericLedger(snapshot) {
  const output = { schema: 1, kind: 'bounded-real-provider-cost-counters', currency: 'CNY',
    priceCheckedAt: '2026-10-08', priceValidUntilUtc: '2026-10-08T23:59:59.999Z',
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
  demand(snapshot.provenance && typeof snapshot.provenance === 'object', 'LEDGER_SHAPE_INVALID');
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
  const screenshotEvidence = safeScreenshotEvidence(report.screenshotEvidence, isMock);
  const safe = { schema: 1, mode: isMock ? 'MOCK' : 'LIVE', realProviderCalled: !isMock && Boolean(ledger?.requests?.some(row => row.outboundStarted === true)), productCommit: PIN.commit, buildRunId: PIN.runId, artifactId: PIN.artifactId,
    installerSha256: PIN.installerSha256, releaseReady: false, independentFullGateCertified: false,
    temporaryLoopbackDebugging: true, normalUndebuggedLaunchValidated: false, ordinaryUserUacSmartScreenValidated: false,
    route: isMock ? 'real-installed-ui-via-loopback-broker-to-synthetic-mock-transport' : 'real-installed-ui-via-loopback-budget-broker-to-real-provider',
    screenshotsCaptured: screenshotEvidence.captured.length > 0, screenshotEvidence, rawLogsCaptured: false, status: report.status,
    code: report.code, cleanupCode: report.cleanupCode || 'CLEANUP_PENDING', scenarios: safeResults(report.scenarios), cleanupPassed: report.cleanupPassed === true };
  demand(['PASS', 'FAIL', 'BLOCKED'].includes(safe.status) && EVIDENCE_CODES.has(safe.code), 'REPORT_INVALID');
  demand(['CLEANUP_PASSED', 'CLEANUP_PENDING', 'BROKER_CLEANUP_FAILED', 'LEDGER_UNAVAILABLE', 'LEDGER_PERSISTENCE_FAILED', 'PROFILE_CLEANUP_FAILED', 'EVIDENCE_WRITE_BLOCKED'].includes(safe.cleanupCode), 'REPORT_INVALID');
  demand(safe.status !== 'PASS' || (safe.cleanupPassed && safe.cleanupCode === 'CLEANUP_PASSED' &&
    safe.scenarios.every(row => row.status === 'PASS') && ledger?.journalFailed === false && ledger?.closed === true &&
    ledger?.active === false && ledger.requests?.length === CASES.length &&
    CASES.every(id => ledger.requests.filter(row => row.caseId === id).length === 1)), 'REPORT_INVALID');
  const texts = [JSON.stringify(safe, null, 2) + '\n', JSON.stringify({ ...numericLedger(ledger), mode: isMock ? 'MOCK' : 'LIVE', billingKind: isMock ? 'simulated-counters-only' : 'real-provider-conservative-accounting' }, null, 2) + '\n'];
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
