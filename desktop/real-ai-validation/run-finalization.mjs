import { demand } from './contract.mjs';

export function trackAppResource(app) {
  const child = app.process();
  demand(child && typeof child.once === 'function', 'INVALID_HARNESS');
  const resource = { app, launcherClosed: false, closeConfirmed: false };
  // Playwright removes its launcher exit hook on this exact ChildProcess event.
  // An exit code or closed browser context alone does not prove pipe closure.
  child.once('close', () => { resource.launcherClosed = true; });
  return resource;
}

// Racing a close does not cancel it. Keep the same promise so verified OS exit
// can release the Playwright process/pipes before the CLI finishes.
export async function waitForResourceSettlement(pending, timeoutMs) {
  demand(Number.isFinite(timeoutMs) && timeoutMs > 0, 'INVALID_HARNESS');
  let timer;
  try {
    return await Promise.race([
      Promise.resolve(pending).then(() => true, () => true),
      new Promise(resolve => { timer = setTimeout(() => resolve(false), timeoutMs); }),
    ]);
  } finally { clearTimeout(timer); }
}

export function finishValidationProcess(result, target = process) {
  demand(result && [0, 1].includes(result.exitCode), 'INVALID_HARNESS');
  target.exitCode = result.exitCode;
  // Never use forced exit to conceal unverified cleanup or a failed evidence
  // write. A UI failure remains exit 1 even when its resources are safely gone.
  // Playwright registers an exit hook that force-kills an unsettled launcher.
  // Require its own close to finish too; verified product PIDs alone do not
  // establish that the launcher and its inherited pipes have been released.
  if (result.finalEvidenceSaved === true && result.cleanupPassed === true && result.cleanupCode === 'CLEANUP_PASSED'
    && result.appResourcesReleased === true) {
    target.exit(result.exitCode);
  }
}
