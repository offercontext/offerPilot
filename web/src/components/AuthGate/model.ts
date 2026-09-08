export interface AuthStatus {
  auth_enabled: boolean;
  authenticated: boolean;
}

export function shouldPromptForAuth(status?: AuthStatus): boolean {
  return Boolean(status?.auth_enabled && !status.authenticated);
}
