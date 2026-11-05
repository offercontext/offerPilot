import assert from 'node:assert/strict';

// Retain the historical policy without permitting mixed old/new dependencies:
// retry also requires identical harness/product locks before any installation.
// Both exact versions omit devTools in SaveLastPreferences; OpenDevTools still
// rejects the call when the constructor disables it. Do not accept a semver range.
// https://github.com/electron/electron/blob/v44.10.2/shell/browser/web_contents_preferences.cc#L389-L411
// https://github.com/electron/electron/blob/v44.10.2/shell/browser/api/electron_api_web_contents.cc#L3499-L3527
const reviewedOmittedDevToolsVersions = new Set(['44.5.1', '44.10.2']);

export function preferenceObservation(value) {
  if (value === undefined) return 'undefined';
  if (typeof value === 'boolean') return value;
  return 'invalid-type';
}

export function assertDevToolsPreference(value, runtimeElectron, proof) {
  assert.equal(typeof runtimeElectron, 'string', 'installed Electron version must be observed');
  assert.equal(runtimeElectron, proof?.lockedElectron, 'installed Electron must match the exact product source lock');
  if (value !== undefined) {
    assert.equal(value, false, 'reported packaged devTools preference must be explicitly false');
    return;
  }
  // The reviewed runtimes omit this constructor-only preference. Missing is not
  // equivalent to false: require the exact version and verified source policy.
  assert.ok(reviewedOmittedDevToolsVersions.has(runtimeElectron), 'missing devTools getter requires an explicitly reviewed Electron version');
  assert.deepEqual(proof, { lockedElectron: runtimeElectron, installedSourcesHashMatched: true,
    ownerPackagedDisabled: true, haruDisabled: true }, 'missing devTools getter requires exact hash-bound packaged constructor policies');
}

export function assertDevToolsProbe(probe) {
  assert.deepEqual(probe, { beforeOpen: false, beforeContents: false, openedEvent: false,
    afterOpen: false, afterContents: false }, 'installed renderer must reject opening DevTools without any event or contents');
}

// Serialized by Playwright into the real Electron main process. This observes
// the existing product window and never patches preferences or closes a probe
// window to convert a security failure into passing evidence.
export async function probeDevToolsDisabled({ BrowserWindow }, windowId) {
  const windows = BrowserWindow.getAllWindows().filter(win => win.id === windowId);
  if (windows.length !== 1) throw new Error('exact-product-window-required');
  const contents = windows[0].webContents;
  const beforeOpen = contents.isDevToolsOpened();
  const beforeContents = Boolean(contents.devToolsWebContents);
  let openedEvent = false;
  const opened = () => { openedEvent = true; };
  contents.on('devtools-opened', opened);
  try {
    if (!beforeOpen && !beforeContents) contents.openDevTools({ mode: 'detach', activate: false });
    await new Promise(resolve => setTimeout(resolve, 1000));
    return { beforeOpen, beforeContents, openedEvent,
      afterOpen: contents.isDevToolsOpened(), afterContents: Boolean(contents.devToolsWebContents) };
  } finally { contents.removeListener('devtools-opened', opened); }
}
