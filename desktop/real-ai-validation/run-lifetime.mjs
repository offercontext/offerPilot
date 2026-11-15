import { performance } from 'node:perf_hooks';
import { demand } from './contract.mjs';

// Deadline expiry stops new operations immediately. The owner must await run()
// before resource cleanup: even an already-started bounded launch must settle.
export function createRunLifetime(deadlineMs, { monotonicDeadline } = {}) {
  demand(Number.isFinite(deadlineMs) && (monotonicDeadline === undefined || Number.isFinite(monotonicDeadline)), 'INVALID_HARNESS');
  const remaining = () => Math.min(deadlineMs - Date.now(), monotonicDeadline === undefined ? Infinity : monotonicDeadline - performance.now());
  let stopped = false, started = false;
  const assertActive = () => demand(!stopped && remaining() > 0, 'SESSION_DEADLINE');
  return Object.freeze({
    assertActive,
    async run(operation, onTimeout = async () => {}) {
      demand(!started && typeof operation === 'function' && typeof onTimeout === 'function', 'INVALID_HARNESS');
      started = true;
      let timer, timeoutCleanup;
      const work = Promise.resolve().then(() => { assertActive(); return operation(); }).then(result => { assertActive(); return result; });
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => {
          stopped = true;
          timeoutCleanup = Promise.resolve().then(onTimeout).catch(() => undefined);
          reject(Object.assign(new Error('SESSION_DEADLINE'), { safeCode: 'SESSION_DEADLINE' }));
        }, Math.max(0, remaining()));
      });
      try { return await Promise.race([work, timeout]); }
      finally {
        stopped = true;
        clearTimeout(timer);
        // Never let a timed-out coroutine create resources after cleanup.
        await work.catch(() => undefined);
        await timeoutCleanup;
      }
    },
  });
}
