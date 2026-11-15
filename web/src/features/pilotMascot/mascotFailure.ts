export type MascotFailureReason = 'dynamic-code-policy' | 'content-security-policy' | 'webgl' | 'resource' | 'unknown';

// Expose only a fixed category. Never retain or render exception text, URLs,
// credentials, stack traces, or arbitrary values from a failed model loader.
export function classifyMascotFailure(error: unknown): MascotFailureReason {
  if (!(error instanceof Error)) return 'unknown';
  const message = error.message;
  if (/unsafe-eval|code generation from strings disallowed|does not allow.*(?:eval|dynamic code)/i.test(message)) return 'dynamic-code-policy';
  if (/content security policy|violates.*script-src/i.test(message)) return 'content-security-policy';
  if (/webgl|web gl|graphics context/i.test(message)) return 'webgl';
  if (/failed to fetch|networkerror|failed to load.*(?:model|texture|resource)|error loading.*(?:model|texture|resource)/i.test(message)) return 'resource';
  return 'unknown';
}
