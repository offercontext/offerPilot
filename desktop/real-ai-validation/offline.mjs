// Deliberately separate entrypoint. No production key, secret expression, or live fallback.
import { prepare } from './prepare.mjs';
import { executeValidation } from './validation-runner.mjs';
import { createOfflineBroker } from './mock-provider.cjs';
import { demand, safeCode } from './contract.mjs';
try {
  demand(!process.env.OFFERPILOT_REAL_AI_KEY, 'SECRET_PRESENT_DURING_PREPARATION');
  if (process.argv[2] === 'prepare') await prepare({ mode: 'mock' });
  else if (process.argv[2] === 'run') {
    const result = await executeValidation({ mode: 'mock', brokerFactory: createOfflineBroker });
    process.exitCode = result.exitCode;
  } else demand(false, 'INVALID_HARNESS');
} catch (error) { console.error(`MOCK validation blocked: ${safeCode(error)}`); process.exitCode = 1; }
