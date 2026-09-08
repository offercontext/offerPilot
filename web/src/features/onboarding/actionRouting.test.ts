import { describe, expect, it } from 'vitest';
import { onboardingActionIntent } from './actionRouting';

describe('onboardingActionIntent', () => {
  it('maps the three navigation or form actions without implicit writes', () => {
    expect(onboardingActionIntent('configure_ai', true)).toEqual({ view: 'settings', openAISettings: true });
    expect(onboardingActionIntent('create_primary_resume', true)).toEqual({ view: 'resumes', focusResumeEntry: true });
    expect(onboardingActionIntent('create_first_application', true)).toEqual({ openApplicationForm: true });
  });

  it('keeps desktop Pilot docked but opens the mobile drawer', () => {
    expect(onboardingActionIntent('send_first_pilot_message', true)).toEqual({ focusPilot: true, openPilotDrawer: false });
    expect(onboardingActionIntent('send_first_pilot_message', false)).toEqual({ focusPilot: true, openPilotDrawer: true });
  });
});
