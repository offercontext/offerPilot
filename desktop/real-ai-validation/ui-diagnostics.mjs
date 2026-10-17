// Only fixed stage identifiers and booleans cross the UI diagnostic boundary.
export const UI_STAGES = Object.freeze(['MOCK_PILOT_PROOF_BASELINE', 'MOCK_PILOT_PROOF_SEND', 'MOCK_PILOT_PROOF_OBSERVE', 'MOCK_PILOT_PROOF_COMPARE', 'LIVE_FRESH_BASELINE', 'LIVE_SEND_OBSERVE', 'LIVE_TERMINAL_OBSERVE', 'LIVE_TERMINAL_COMPARE', 'UNSPECIFIED', 'CASE_START', 'BROKER_PREPARE',
  'LEAVE_TASK', 'TASK_CLOSE_OWNER', 'TASK_CLOSE_CLICK', 'TASK_OWNER_DETACHED', 'PILOT_EXIT', 'RESUME_RETURN',
  'NAVIGATE', 'SETTINGS_OPEN', 'PROVIDER_LIST', 'PROVIDER_LABEL', 'PROVIDER_KEY',
  'PROVIDER_TYPE_OPEN', 'PROVIDER_TYPE_SELECT', 'PROVIDER_TYPE_CLOSED', 'PROVIDER_ENDPOINT', 'PROVIDER_MODEL',
  'PROVIDER_CONTEXT', 'PROVIDER_OUTPUT', 'PROVIDER_ENABLED', 'PROVIDER_JSON_SCHEMA', 'PROVIDER_HITL',
  'SETTINGS_SAVE', 'SETTINGS_READBACK', 'SETTINGS_CLOSED', 'CONNECTION_REOPEN', 'CONNECTION_ARM',
  'CONNECTION_CLICK', 'CONNECTION_RESPONSE', 'CONNECTION_SUCCESS', 'CONNECTION_RETURN',
  'PILOT_OPEN', 'PILOT_COMPOSE', 'PILOT_ARM', 'PILOT_SEND', 'PILOT_HITL_VISIBLE', 'PILOT_REJECT',
  'PILOT_OBSERVER_INSTALL', 'PILOT_RUNNING', 'PILOT_FINAL_MIRROR', 'PILOT_STOP', 'PILOT_STOP_READBACK', 'PILOT_STREAM_READBACK', 'HARU_MIRROR',
  'INTERVIEW_OPEN', 'INTERVIEW_CARD', 'INTERVIEW_READINESS', 'INTERVIEW_RESUME_OPEN', 'INTERVIEW_RESUME_SELECT', 'INTERVIEW_START', 'INTERVIEW_PROPOSAL', 'INTERVIEW_PROPOSAL_RESUME', 'INTERVIEW_SOURCE', 'INTERVIEW_GENERATE', 'RESUME_OPEN', 'RESUME_SEARCH', 'RESUME_EDIT', 'RESUME_CLASSIFY_OPEN', 'RESUME_GENERATE', 'OFFER_OPEN', 'OFFER_CARD',
  'OFFER_REVIEW', 'OFFER_GENERATE', 'MOCK_CAPTURE', 'PROVIDER_TERMINAL', 'CASE_CLEANUP']);
export const MIRROR_DIAGNOSTIC_FIELDS = Object.freeze(['observerInstalled', 'ownerBaselineReady', 'haruBaselineReady',
  'ownerConnected', 'haruConnected', 'ownerIdleNow', 'haruIdleNow', 'ownerRunningPositiveSeen', 'haruRunningPositiveSeen',
  'ownerRunningNullSeen', 'haruRunningNullSeen', 'ownerRunningDomSeen', 'haruRunningDomSeen',
  'sameRunningConversation', 'currentConversationMatches', 'invalidObservation', 'observationReadFailed']);
export const CONTINUATION_CODES = Object.freeze(['MOCK_CONTINUATION_PROVEN', 'NOT_ELIGIBLE',
  'MOCK_BOUNDARY_REQUIRED', 'LEDGER_UNSAFE', 'MIRROR_UNPROVEN', 'TERMINAL_UNPROVEN',
  'DOM_MISMATCH', 'READ_FAILED', 'DEADLINE', 'LEDGER_CHANGED']);
export const STREAM_DIAGNOSTIC_FIELDS = Object.freeze(['installed', 'originalTargetConnected', 'originalTargetCurrent',
  'currentPilotUnique', 'targetReplacementObserved', 'stopPresentNow', 'readFailed']);
export const STREAM_DIAGNOSTIC_COUNTS = Object.freeze(['originalGrowthCount', 'originalGrowthWithStopCount',
  'currentGrowthCount', 'currentGrowthWithStopCount']);
export const PILOT_PROOF_FAULTS = Object.freeze(['UNEXPECTED_FAILURE', 'HARNESS_INVALID', 'OBSERVE_DEADLINE',
  'OPERATION_TIMEOUT', 'MOCK_STREAMING_UNPROVEN', 'READ_FAILED', 'BASELINE_LEDGER_INVALID', 'FRESH_MIRROR_INVALID', 'HISTORY_INVALID',
  'ADMISSION_INVALID', 'ADMISSION_DUPLICATE', 'ADMISSION_REDIRECTED', 'ADMISSION_READ_FAILED', 'ADMISSION_METADATA_FAILED', 'ADMISSION_CLEANUP_FAILED',
  'MIRROR_INVALID', 'RENDERER_FORMAT_UNSUPPORTED', 'STATUS_IDENTITY_INVALID', 'STATUS_PROTOCOL_INVALID', 'STATUS_UNSAFE', 'STATUS_RECOVERY_INVALID',
  'STATUS_STATE_INVALID', 'STATUS_TERMINAL_INVALID', 'LEDGER_INVALID', 'RENDERER_UNPROVEN', 'FINAL_STABILITY_UNPROVEN']);
export const PILOT_PROOF_READ_STAGES = Object.freeze(['INITIAL_MIRROR', 'HISTORY_READ', 'SEND', 'ADMISSION_READ',
  'STATUS_AND_MIRROR_READ', 'TERMINAL_RENDER_READ', 'FINAL_MIRROR_READ', 'POLL_WAIT', 'CLEANUP']);
export const PILOT_PROOF_READ_OUTCOMES = Object.freeze(['OK', 'INVALID_CONTEXT', 'INVALID_REQUEST', 'DEADLINE',
  'TIMEOUT', 'TRANSPORT', 'HTTP_STATUS', 'ROUTE_MISMATCH', 'BODY_MISSING', 'BODY_TOO_LARGE', 'DECODE_OR_JSON']);
export const MOCK_PILOT_CHECK_MAP = Object.freeze(Object.fromEntries([
  'sustainedStreamingObserved', 'freshConversationProven', 'uiAdmissionProven', 'naturalCompletionProven',
  'terminalIdentityProven', 'terminalMirrorProven', 'terminalTextProven', 'ledgerUnchanged', 'admissionObserverCleanupPassed',
].map(key => [key, `mockPilot${key[0].toUpperCase()}${key.slice(1)}`])));
export const RENDERER_DIAGNOSTIC_REASONS = Object.freeze(['DEADLINE', 'BRIDGE', 'CONNECTION', 'GENERATION',
  'SNAPSHOT', 'CONVERSATION', 'PENDING_ACTION', 'STOPPING', 'ERROR', 'STOP_MESSAGE', 'MESSAGES',
  'TASK_STATE', 'LOADING', 'STOP_CONTROL', 'ROOT_COUNT', 'ROOT_VISIBILITY', 'STRUCTURED_CONTENT',
  'DUPLICATE_BODY', 'BODY_MISSING', 'BODY_VISIBILITY', 'OWNER_SHAPE', 'MARKDOWN_VISIBILITY',
  'PARAGRAPH_SHAPE', 'PARAGRAPH_VISIBILITY', 'INLINE_FORMAT', 'PARAGRAPHS_MISSING', 'TEXT_SHAPE',
  'CONTROLS_PENDING', 'EMPTY_TEXT', 'OPACITY_ZERO', 'READY']);
export function sanitizeRendererDiagnostic(value) {
  const nullableBool = v => typeof v === 'boolean' ? v : null;
  const count = v => Number.isSafeInteger(v) && v >= 0 && v <= 2 ? v : null;
  return {
    status: ['READY', 'NOT_READY', 'UNSUPPORTED_FORMAT', 'UNSAFE'].includes(value?.status) ? value.status : 'UNAVAILABLE',
    reason: RENDERER_DIAGNOSTIC_REASONS.includes(value?.reason) ? value.reason : null,
    rootCount: count(value?.rootCount), bodyCount: count(value?.bodyCount), assistantCount: count(value?.assistantCount),
    documentVisible: nullableBool(value?.documentVisible), rootVisible: nullableBool(value?.rootVisible),
    bodyVisible: nullableBool(value?.bodyVisible), generationMatches: nullableBool(value?.generationMatches),
    conversationMatches: nullableBool(value?.conversationMatches),
  };
}
export function sanitizePilotProofDiagnostic(value, isMock = false) {
  const nullableBool = v => typeof v === 'boolean' ? v : null;
  const count = (v, max) => Number.isSafeInteger(v) && v >= 0 && v <= max ? v : null;
  return {
    fault: PILOT_PROOF_FAULTS.includes(value?.fault) ? value.fault : 'UNEXPECTED_FAILURE',
    faultStage: PILOT_PROOF_READ_STAGES.includes(value?.faultStage) ? value.faultStage : null,
    lastReadStage: PILOT_PROOF_READ_STAGES.includes(value?.lastReadStage) ? value.lastReadStage : null,
    elapsedMs: count(value?.elapsedMs, 600000), elapsedCapped: nullableBool(value?.elapsedCapped),
    pollCount: count(value?.pollCount, 65535), admissionObserved: nullableBool(value?.admissionObserved),
    rendererReady: nullableBool(value?.rendererReady), rendererTextMatched: nullableBool(value?.rendererTextMatched),
    admissionValid: nullableBool(value?.admissionValid), naturalCompletionProven: nullableBool(value?.naturalCompletionProven),
    admissionHttpStatus: Number.isSafeInteger(value?.admissionHttpStatus) && value.admissionHttpStatus >= 100 && value.admissionHttpStatus <= 599
      ? value.admissionHttpStatus : null,
    admissionBodyOutcome: ['OK', 'CDP_RESOURCE_UNAVAILABLE', 'BODY_UNAVAILABLE', 'TARGET_CLOSED', 'REQUEST_ABORTED', 'CAPTURE_INVALID', 'OTHER'].includes(value?.admissionBodyOutcome)
      ? value.admissionBodyOutcome : null,
    lastValidState: ['queued', 'running', 'completed'].includes(value?.lastValidState) ? value.lastValidState : null,
    workerDone: nullableBool(value?.workerDone), actualWorkerAlive: nullableBool(value?.actualWorkerAlive),
    modelCallsReserved: count(value?.modelCallsReserved, 20),
    lastReadOutcome: PILOT_PROOF_READ_OUTCOMES.includes(value?.lastReadOutcome) ? value.lastReadOutcome : null,
    lastHttpStatus: Number.isSafeInteger(value?.lastHttpStatus) && value.lastHttpStatus >= 100 && value.lastHttpStatus <= 599
      ? value.lastHttpStatus : null,
    ...(isMock === true && value?.rendererOwner !== undefined ? { rendererOwner: sanitizeRendererDiagnostic(value.rendererOwner) } : {}),
    ...(isMock === true && value?.rendererHaru !== undefined ? { rendererHaru: sanitizeRendererDiagnostic(value.rendererHaru) } : {}),
  };
}
export function sanitizeStreamDiagnostic(value) {
  return { ...Object.fromEntries(STREAM_DIAGNOSTIC_FIELDS.map(key => [key, value?.[key] === true])),
    ...Object.fromEntries(STREAM_DIAGNOSTIC_COUNTS.map(key => [key,
      Number.isSafeInteger(value?.[key]) && value[key] >= 0 && value[key] <= 255 ? value[key] : 0])) };
}
export function sanitizeMirrorDiagnostic(value) {
  return Object.fromEntries(MIRROR_DIAGNOSTIC_FIELDS.map(key => [key, value?.[key] === true]));
}
export function sanitizeUiDiagnostic(value, isMock = false) {
  return { stage: UI_STAGES.includes(value?.stage) ? value.stage : 'UNSPECIFIED',
    targetProbed: value?.targetProbed === true, targetFound: value?.targetFound === true,
    targetUnique: value?.targetUnique === true, targetVisible: value?.targetVisible === true,
    ...(value?.mirror === undefined ? {} : { mirror: sanitizeMirrorDiagnostic(value.mirror) }),
    ...(value?.stream === undefined ? {} : { stream: sanitizeStreamDiagnostic(value.stream) }),
    ...(value?.pilotProof === undefined ? {} : { pilotProof: sanitizePilotProofDiagnostic(value.pilotProof, isMock) }) };
}
export function createUiDiagnostic(isMock = false) {
  let stage = 'UNSPECIFIED', locator, mirror, stream, pilotProof;
  return {
    mark(next, nextLocator) { if (next === 'CASE_START') { mirror = undefined; stream = undefined; pilotProof = undefined; } stage = UI_STAGES.includes(next) ? next : 'UNSPECIFIED'; locator = nextLocator; },
    target(next) { locator = next; },
    mirror(value) { mirror = sanitizeMirrorDiagnostic(value); },
    stream(value) { stream = sanitizeStreamDiagnostic(value); },
    pilotProof(value) { pilotProof = sanitizePilotProofDiagnostic(value, isMock); },
    async snapshot() {
      const result = sanitizeUiDiagnostic({ stage, mirror, stream, pilotProof }, isMock);
      if (!locator || typeof locator.count !== 'function') return result;
      let timer;
      try {
        const probe = (async () => {
          const count = await locator.count();
          const observed = sanitizeUiDiagnostic({ stage, mirror, stream, pilotProof, targetProbed: true,
            targetFound: Number.isSafeInteger(count) && count > 0, targetUnique: count === 1 }, isMock);
          if (count === 1) observed.targetVisible = await locator.isVisible();
          return sanitizeUiDiagnostic(observed, isMock);
        })();
        // A disconnected renderer must not stall the catch path or replace the
        // primary UI failure with the suite's much longer deadline.
        return await Promise.race([probe, new Promise(resolve => {
          timer = setTimeout(() => resolve(result), 5000);
        })]);
      } catch { return result; } // No raw locator or transport error escapes.
      finally { clearTimeout(timer); }
    },
  };
}
