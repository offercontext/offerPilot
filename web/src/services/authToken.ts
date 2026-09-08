const AUTH_TOKEN_KEY = 'offerpilot.auth_token';

interface TokenStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function browserStorage(): TokenStorage | undefined {
  if (typeof window === 'undefined') {
    return undefined;
  }
  return window.localStorage;
}

export function getStoredAuthToken(storage: TokenStorage | undefined = browserStorage()): string {
  return storage?.getItem(AUTH_TOKEN_KEY)?.trim() ?? '';
}

export function setStoredAuthToken(
  value: string,
  storage: TokenStorage | undefined = browserStorage(),
): void {
  const token = value.trim();
  if (!storage) {
    return;
  }
  if (!token) {
    storage.removeItem(AUTH_TOKEN_KEY);
    return;
  }
  storage.setItem(AUTH_TOKEN_KEY, token);
}

export function clearStoredAuthToken(storage: TokenStorage | undefined = browserStorage()): void {
  storage?.removeItem(AUTH_TOKEN_KEY);
}

export function authHeaders(token = getStoredAuthToken()): Record<string, string> {
  return token ? { 'X-OfferPilot-Token': token } : {};
}
