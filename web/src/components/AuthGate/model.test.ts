import { describe, expect, it } from 'vitest';
import { shouldPromptForAuth } from './model';

describe('AuthGate model', () => {
  it('prompts only when auth is enabled and the request is not authenticated', () => {
    expect(shouldPromptForAuth()).toBe(false);
    expect(shouldPromptForAuth({ auth_enabled: false, authenticated: true })).toBe(false);
    expect(shouldPromptForAuth({ auth_enabled: true, authenticated: true })).toBe(false);
    expect(shouldPromptForAuth({ auth_enabled: true, authenticated: false })).toBe(true);
  });
});
