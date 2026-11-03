import type { CompanionSnapshot } from './contracts';
/** Explicit opt-in for tests of the companion's named surfaces. */
export const enabledCompanionSnapshot: CompanionSnapshot = {
  identity: 'fixture', revision: 1, persona_version: 'companion-v1',
  settings: { enabled: true, proactivity: 'off', hide_status: false },
  state: { interests: [], activity: null, wish: null },
  recent_events: [], shared_participation: [],
  presence: { session_id: 'fixture', session_quiet: false, active: false, blocked: true, blockers: [] },
};
