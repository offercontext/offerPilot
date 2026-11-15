// Explicit OFFLINE capability. The live entrypoint neither imports nor accepts
// this factory. The actual network-locked broker identity cannot be forged by
// copying mode/snapshot fields or by stripping MOCK metadata.
import { isOfflineBroker } from './mock-provider.cjs';
import { observePilotCompletion, offlineSnapshot } from './live-pilot-completion.mjs';
import { MOCK_PILOT_CHECK_MAP, sanitizePilotProofDiagnostic } from './ui-diagnostics.mjs';
import { demand } from './contract.mjs';

export function createOfflinePilotProof({ mode, broker } = {}) {
  demand(mode === 'mock' && isOfflineBroker(broker) && offlineSnapshot(broker.snapshot()), 'INVALID_HARNESS');
  return async args => {
    // Screenshot capture may wrap prepareCase; the spending, snapshot and
    // cancellation functions must still belong to this exact original broker.
    demand(args?.mode === 'mock' && isOfflineBroker(broker) && args.broker?.mode === 'MOCK'
      && args.broker.origin === broker.origin && args.broker.snapshot === broker.snapshot
      && args.broker.armCase === broker.armCase && args.broker.cancelCase === broker.cancelCase,
    'INVALID_HARNESS');
    const started = performance.now();
    try {
      const outcome = await observePilotCompletion({ ...args, broker });
      demand(isOfflineBroker(broker) && offlineSnapshot(broker.snapshot()), 'INVALID_HARNESS');
      // Keep the pre-existing MOCK stream/running gate: natural completion
      // alone is diagnostic evidence, never a replacement streaming PASS.
      if (outcome.code !== 'STREAMING_PROVEN') {
        const elapsed = Math.max(0, Math.floor(performance.now() - started));
        throw Object.assign(new Error('MOCK_PILOT_COMPLETION_UNPROVEN'), {
          pilotProofDiagnostic: sanitizePilotProofDiagnostic({ fault: 'MOCK_STREAMING_UNPROVEN',
            faultStage: 'FINAL_MIRROR_READ', lastReadStage: 'FINAL_MIRROR_READ',
            elapsedMs: Math.min(elapsed, 600000), elapsedCapped: elapsed > 600000, naturalCompletionProven: true }),
        });
      }
      const checks = Object.fromEntries(Object.entries(outcome.checks).map(([key, value]) => [MOCK_PILOT_CHECK_MAP[key] ?? key, value]));
      return { code: `MOCK_PILOT_${outcome.code}`, checks };
    } catch (error) {
      // Only the fixed diagnostic projection may survive; no raw error text.
      throw Object.assign(new Error('MOCK_PILOT_COMPLETION_UNPROVEN'), {
        code: 'MOCK_PILOT_COMPLETION_UNPROVEN',
        ...(error?.pilotProofDiagnostic === undefined ? {} : { pilotProofDiagnostic: error.pilotProofDiagnostic }),
      });
    }
  };
}
