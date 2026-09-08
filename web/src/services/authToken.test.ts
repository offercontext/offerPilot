import { describe, expect, it } from 'vitest';
import { authHeaders, clearStoredAuthToken, getStoredAuthToken, setStoredAuthToken } from './authToken';

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

describe('auth token storage', () => {
  it('stores trimmed auth tokens', () => {
    const storage = memoryStorage();

    setStoredAuthToken('  local-secret  ', storage);

    expect(getStoredAuthToken(storage)).toBe('local-secret');
    expect(authHeaders('local-secret')).toEqual({ 'X-OfferPilot-Token': 'local-secret' });
  });

  it('clears blank tokens', () => {
    const storage = memoryStorage();
    setStoredAuthToken('local-secret', storage);

    setStoredAuthToken(' ', storage);

    expect(getStoredAuthToken(storage)).toBe('');
    expect(authHeaders('')).toEqual({});
  });

  it('removes stored auth tokens', () => {
    const storage = memoryStorage();
    setStoredAuthToken('local-secret', storage);

    clearStoredAuthToken(storage);

    expect(getStoredAuthToken(storage)).toBe('');
  });
});
