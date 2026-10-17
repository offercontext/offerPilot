import { describe, expect, it } from 'vitest';
import { classifyMascotFailure } from './mascotFailure';

describe('bounded mascot failure diagnostics', () => {
  it.each([
    ['Current environment does not allow unsafe-eval', 'dynamic-code-policy'],
    ['Code generation from strings disallowed for this context', 'dynamic-code-policy'],
    ['Refused script: violates Content Security Policy', 'content-security-policy'],
    ['WebGL unsupported in this browser', 'webgl'],
    ['Failed to fetch https://private.invalid/model?token=do-not-retain', 'resource'],
    ['secret arbitrary failure data', 'unknown'],
  ])('classifies without exposing exception text: %s', (message, expected) => {
    expect(classifyMascotFailure(new Error(message))).toBe(expected);
  });
  it('does not stringify arbitrary thrown objects', () => {
    expect(classifyMascotFailure({ toString: () => { throw new Error('must not execute'); } })).toBe('unknown');
    expect(classifyMascotFailure('unsafe-eval')).toBe('unknown');
  });
});
