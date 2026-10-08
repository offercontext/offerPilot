// Only fixed stage identifiers and booleans cross the UI diagnostic boundary.
export const UI_STAGES = Object.freeze(['UNSPECIFIED', 'CASE_START', 'BROKER_PREPARE',
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
export function sanitizeMirrorDiagnostic(value) {
  return Object.fromEntries(MIRROR_DIAGNOSTIC_FIELDS.map(key => [key, value?.[key] === true]));
}
export function sanitizeUiDiagnostic(value) {
  return { stage: UI_STAGES.includes(value?.stage) ? value.stage : 'UNSPECIFIED',
    targetProbed: value?.targetProbed === true, targetFound: value?.targetFound === true,
    targetUnique: value?.targetUnique === true, targetVisible: value?.targetVisible === true,
    ...(value?.mirror === undefined ? {} : { mirror: sanitizeMirrorDiagnostic(value.mirror) }) };
}
export function createUiDiagnostic() {
  let stage = 'UNSPECIFIED', locator, mirror;
  return {
    mark(next, nextLocator) { if (next === 'CASE_START') mirror = undefined; stage = UI_STAGES.includes(next) ? next : 'UNSPECIFIED'; locator = nextLocator; },
    target(next) { locator = next; },
    mirror(value) { mirror = sanitizeMirrorDiagnostic(value); },
    async snapshot() {
      const result = sanitizeUiDiagnostic({ stage, mirror });
      if (!locator || typeof locator.count !== 'function') return result;
      let timer;
      try {
        const probe = (async () => {
          const count = await locator.count();
          const observed = sanitizeUiDiagnostic({ stage, mirror, targetProbed: true,
            targetFound: Number.isSafeInteger(count) && count > 0, targetUnique: count === 1 });
          if (count === 1) observed.targetVisible = await locator.isVisible();
          return sanitizeUiDiagnostic(observed);
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
