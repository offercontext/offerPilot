import { describe, expect, it } from 'vitest';
import { shouldShowOnboarding, type OnboardingStatus } from './onboarding';
const fresh: OnboardingStatus = { steps: { configure_ai: false, create_primary_resume: false, create_first_application: false, send_first_pilot_message: false }, completed_count: 0, is_complete: false, force_open: false };
describe('persisted onboarding visibility', () => {
  it('shows first use but respects dismissal at any stage, including after reload', () => {
    expect(shouldShowOnboarding(fresh)).toBe(true);
    const saved = JSON.parse(JSON.stringify({ ...fresh, dismissed: true }));
    expect(shouldShowOnboarding(saved)).toBe(false);
    expect(shouldShowOnboarding({ ...saved, force_open: true })).toBe(true);
  });
  it('does not reopen completed old users without an explicit request', () => {
    expect(shouldShowOnboarding({ ...fresh, is_complete: true })).toBe(false);
  });
});
