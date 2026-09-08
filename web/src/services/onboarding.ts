import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 10000 });

export const ONBOARDING_QUERY_KEY = ['onboarding'] as const;

export interface OnboardingStatus {
  steps: {
    configure_ai: boolean;
    create_primary_resume: boolean;
    create_first_application: boolean;
    send_first_pilot_message: boolean;
  };
  completed_count: number;
  is_complete: boolean;
  force_open: boolean;
}

export async function getOnboarding(): Promise<OnboardingStatus> {
  const { data } = await http.get<OnboardingStatus>('/onboarding');
  return data;
}

export async function setOnboardingForceOpen(force_open: boolean): Promise<OnboardingStatus> {
  const { data } = await http.patch<OnboardingStatus>('/onboarding', { force_open });
  return data;
}
