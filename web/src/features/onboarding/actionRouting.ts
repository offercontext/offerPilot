export const ONBOARDING_ACTIONS = [
  'configure_ai',
  'create_primary_resume',
  'create_first_application',
  'send_first_pilot_message',
] as const;

export type OnboardingAction = (typeof ONBOARDING_ACTIONS)[number];

export type OnboardingActionIntent = {
  view?: 'settings' | 'resumes';
  openAISettings?: true;
  openApplicationForm?: true;
  focusResumeEntry?: true;
  focusPilot?: true;
  openPilotDrawer?: boolean;
};

export function onboardingActionIntent(
  action: OnboardingAction,
  pilotRailAvailable: boolean,
): OnboardingActionIntent {
  switch (action) {
    case 'configure_ai':
      return { view: 'settings', openAISettings: true };
    case 'create_primary_resume':
      return { view: 'resumes', focusResumeEntry: true };
    case 'create_first_application':
      return { openApplicationForm: true };
    case 'send_first_pilot_message':
      return { focusPilot: true, openPilotDrawer: !pilotRailAvailable };
  }
}
