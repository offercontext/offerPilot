// Only fixed stage identifiers and booleans cross the UI diagnostic boundary.
export const UI_STAGES = Object.freeze(['UNSPECIFIED', 'CASE_START', 'BROKER_PREPARE',
  'LEAVE_TASK', 'NAVIGATE', 'SETTINGS_OPEN', 'PROVIDER_LIST', 'PROVIDER_LABEL', 'PROVIDER_KEY',
  'PROVIDER_TYPE_OPEN', 'PROVIDER_TYPE_SELECT', 'PROVIDER_TYPE_CLOSED', 'PROVIDER_ENDPOINT', 'PROVIDER_MODEL',
  'PROVIDER_CONTEXT', 'PROVIDER_OUTPUT', 'PROVIDER_ENABLED', 'PROVIDER_JSON_SCHEMA', 'PROVIDER_HITL',
  'SETTINGS_SAVE', 'SETTINGS_READBACK', 'SETTINGS_CLOSED', 'CONNECTION_REOPEN', 'CONNECTION_ARM',
  'CONNECTION_CLICK', 'CONNECTION_RESPONSE', 'CONNECTION_SUCCESS', 'CONNECTION_RETURN',
  'PILOT_OPEN', 'PILOT_COMPOSE', 'PILOT_ARM', 'PILOT_SEND', 'PILOT_HITL_VISIBLE', 'PILOT_REJECT',
  'PILOT_RUNNING', 'PILOT_STOP', 'PILOT_STOP_READBACK', 'PILOT_STREAM_READBACK', 'HARU_MIRROR',
  'INTERVIEW_OPEN', 'INTERVIEW_GENERATE', 'RESUME_OPEN', 'RESUME_GENERATE', 'OFFER_OPEN',
  'OFFER_REVIEW', 'OFFER_GENERATE', 'MOCK_CAPTURE', 'PROVIDER_TERMINAL', 'CASE_CLEANUP']);
export function sanitizeUiDiagnostic(value) {
  return { stage: UI_STAGES.includes(value?.stage) ? value.stage : 'UNSPECIFIED',
    targetProbed: value?.targetProbed === true, targetFound: value?.targetFound === true,
    targetUnique: value?.targetUnique === true, targetVisible: value?.targetVisible === true };
}
export function createUiDiagnostic() {
  let stage = 'UNSPECIFIED', locator;
  return {
    mark(next, nextLocator) { stage = UI_STAGES.includes(next) ? next : 'UNSPECIFIED'; locator = nextLocator; },
    target(next) { locator = next; },
    async snapshot() {
      const result = sanitizeUiDiagnostic({ stage });
      if (!locator || typeof locator.count !== 'function') return result;
      let timer;
      try {
        const probe = (async () => {
          const count = await locator.count();
          const observed = sanitizeUiDiagnostic({ stage, targetProbed: true,
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
