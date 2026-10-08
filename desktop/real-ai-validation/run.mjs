// Fixed live entrypoint: remove the key before loading any third-party helper.
// The GitHub runner and parent step can still access their own environment.
const providerKey = process.env.OFFERPILOT_REAL_AI_KEY;
delete process.env.OFFERPILOT_REAL_AI_KEY;
try {
  const [{ createBroker }, { executeValidation }] = await Promise.all([
    import('./broker.cjs'), import('./validation-runner.mjs'),
  ]);
  const result = await executeValidation({ mode: 'live', brokerFactory: createBroker, providerKey });
  process.exitCode = result.exitCode;
} catch { console.error('LIVE_ENTRYPOINT_BLOCKED'); process.exitCode = 1; }
