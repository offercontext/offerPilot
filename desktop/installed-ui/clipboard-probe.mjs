import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

export const SYNTHETIC_JD_SOURCE = 'https://example.invalid/qa-local-only';
const CHOICES = Object.freeze(['cancel', 'allow', 'cancel']);
const SUCCESS = '来源已复制';
const DENIED = '无法复制，请手动选择来源文字';

export function assertHostedClipboardEnvironment(environment = process.env, platform = process.platform) {
  assert.equal(platform, 'win32', 'clipboard probe requires actual Windows');
  assert.equal(environment.GITHUB_ACTIONS, 'true', 'clipboard probe requires GitHub Actions');
  assert.equal(environment.RUNNER_ENVIRONMENT, 'github-hosted', 'clipboard probe requires an isolated hosted runner');
}

// Serialized into the real Electron main process. The production permission
// handlers remain installed. Only their exact clipboard confirmation is answered.
// Never read or preserve the pre-existing clipboard, nor return clipboard text.
export function nativeClipboardInstrumentation({ dialog, clipboard }, args) {
  const key = Symbol.for(args.key);
  if (args.operation === 'install') {
    if (globalThis[key]) throw new Error('clipboard instrumentation already installed');
    const owner = args.owner;
    const contents = owner.webContents;
    if (contents.isDestroyed() || contents.getURL() !== args.ownerURL) throw new Error('clipboard owner mismatch');
    if (args.expectedText !== 'https://example.invalid/qa-local-only') throw new Error('non-synthetic clipboard target refused');
    const state = { owner, contents, ownerURL: args.ownerURL, expectedText: args.expectedText,
      originalMessage: dialog.showMessageBox, armed: null, baseline: null, baselineWritten: false,
      writes: 0, reads: 0, messages: [], failures: [] };
    state.wrapper = async (win, options) => {
      const active = state.armed;
      state.armed = null; // One answer only; a second request never reuses approval.
      const expected = Boolean(active && win === owner && !contents.isDestroyed()
        && contents.getURL() === state.ownerURL
        && options?.type === 'question' && options?.title === '复制到剪贴板？'
        && options?.message === '允许将当前选定内容复制到系统剪贴板？'
        && options?.detail === '会替换现有剪贴板内容，仅允许这一次复制，不读取剪贴板。'
        && options?.defaultId === 0 && options?.cancelId === 0 && options?.noLink === true
        && Array.isArray(options?.buttons) && options.buttons.length === 2
        && options.buttons[0] === '取消' && options.buttons[1] === '复制');
      const choice = expected ? active.choice : 'cancel';
      state.messages.push({ sequence: active?.sequence ?? null, expected, choice });
      if (!expected) state.failures.push('unexpected-native-confirmation');
      return { response: choice === 'allow' ? 1 : 0, checkboxChecked: false };
    };
    globalThis[key] = state;
    dialog.showMessageBox = state.wrapper;
    if (dialog.showMessageBox !== state.wrapper) throw new Error('clipboard dialog instrumentation unavailable');
    return { installed: true };
  }
  const state = globalThis[key];
  if (!state) {
    if (args.operation === 'restore') return { restored: true, absent: true };
    throw new Error('clipboard instrumentation missing');
  }
  const ownerUnchanged = () => !state.contents.isDestroyed() && state.contents.getURL() === state.ownerURL;
  if (args.operation === 'arm') {
    if (!ownerUnchanged() || state.armed || state.failures.length) throw new Error('clipboard probe not safe to arm');
    const choices = ['cancel', 'allow', 'cancel'];
    if (!Number.isInteger(args.sequence) || args.sequence !== state.messages.length
      || args.sequence < 0 || args.sequence >= choices.length || args.choice !== choices[args.sequence]) {
      throw new Error('clipboard probe sequence rejected');
    }
    state.baselineWritten = false;
    state.baseline = `OfferPilot hosted clipboard synthetic baseline ${args.sequence}`;
    // This successful write must precede every possible clipboard read, including
    // cancellation readback. No attempt is made to save/restore old contents.
    clipboard.writeText(state.baseline);
    state.writes++;
    state.baselineWritten = true;
    state.reads++;
    const baselineMatched = clipboard.readText() === state.baseline;
    state.baselineWritten = baselineMatched;
    if (!baselineMatched) throw new Error('synthetic clipboard baseline not established');
    state.armed = { choice: args.choice, sequence: args.sequence };
    return { baselineMatched, syntheticBaselineWrittenBeforeRead: true };
  }
  if (args.operation === 'snapshot') {
    return { messages: state.messages, failures: state.failures, ownerURLUnchanged: ownerUnchanged(),
      syntheticBaselineWrites: state.writes, comparisonOnlyReads: state.reads };
  }
  if (args.operation === 'readback') {
    if (!state.baselineWritten || !ownerUnchanged() || state.armed || state.failures.length) {
      throw new Error('clipboard readback not safe');
    }
    const last = state.messages.at(-1);
    if (!last || last.sequence !== args.sequence || !last.expected) throw new Error('clipboard request not confirmed');
    state.reads++;
    const exactExpectedValue = clipboard.readText() === (last.choice === 'allow' ? state.expectedText : state.baseline);
    return { exactExpectedValue, comparisonOnly: true };
  }
  if (args.operation === 'restore') {
    dialog.showMessageBox = state.originalMessage;
    if (dialog.showMessageBox !== state.originalMessage) throw new Error('clipboard dialog restoration failed');
    // Freeze the observation window atomically with restoration, including any
    // late confirmation that arrived after the previous asynchronous snapshot.
    const finalSnapshot = { messages: state.messages, failures: state.failures, ownerURLUnchanged: ownerUnchanged(),
      syntheticBaselineWrites: state.writes, comparisonOnlyReads: state.reads };
    delete globalThis[key];
    return { restored: true, finalSnapshot, preExistingClipboardNeverRead: true, oldClipboardRestorationAttempted: false };
  }
  throw new Error('unknown clipboard instrumentation operation');
}

export function assertClipboardSnapshot(value, expectedCount) {
  assert.deepEqual(value.failures, [], 'native clipboard confirmation failed');
  assert.equal(value.ownerURLUnchanged, true, 'clipboard owner navigated');
  assert.ok(value.messages.length <= expectedCount, 'unexpected extra native confirmation');
  for (let index = 0; index < value.messages.length; index++) {
    assert.deepEqual(value.messages[index], { sequence: index, expected: true, choice: CHOICES[index] });
  }
}

/** Called while the real synthetic JD source and its copy button are visible. */
export async function probeInstalledClipboard({ app, page, copyButton,
  expectedText = SYNTHETIC_JD_SOURCE, capture = async () => {}, setStage = () => {}, timeoutMs = 15000 }) {
  assertHostedClipboardEnvironment();
  assert.equal(expectedText, SYNTHETIC_JD_SOURCE, 'only the fixed synthetic JD source may be copied');
  assert.ok(Number.isFinite(timeoutMs) && timeoutMs > 0 && timeoutMs <= 30000);
  assert.equal(typeof capture, 'function');
  assert.equal(typeof setStage, 'function');
  assert.equal(await copyButton.count(), 1, 'exactly one real JD copy control required');
  assert.equal(await copyButton.isVisible(), true, 'real JD copy control must be visible');
  assert.equal(await copyButton.evaluate((node, expected) => Boolean(node.parentElement
    && [...node.parentElement.children].some(sibling => sibling !== node
      && sibling.textContent?.trim() === `来源：${expected}`)), expectedText), true,
  'copy control must display the exact synthetic JD source before clipboard access');
  const ownerURL = page.url();
  assert.equal(new URL(ownerURL).hostname, '127.0.0.1', 'installed loopback owner required');
  const owner = await app.browserWindow(page);
  const key = `offerpilot.installed-clipboard.${randomUUID()}`;
  const run = (operation, extra = {}) => app.evaluate(nativeClipboardInstrumentation,
    { operation, key, ownerURL, ...extra });
  const steps = [];
  let primaryError;
  let result;
  try {
    setStage('clipboard-instrumentation');
    await run('install', { owner, expectedText });
    await page.bringToFront();
    for (let sequence = 0; sequence < CHOICES.length; sequence++) {
      const choice = CHOICES[sequence];
      setStage(`clipboard-${sequence}-${choice}`);
      // Previous toasts cannot satisfy a later interaction's result assertion.
      await page.getByText(SUCCESS, { exact: true }).waitFor({ state: 'hidden', timeout: timeoutMs });
      await page.getByText(DENIED, { exact: true }).waitFor({ state: 'hidden', timeout: timeoutMs });
      const baseline = await run('arm', { sequence, choice });
      assert.equal(baseline.baselineMatched, true);
      assert.equal(baseline.syntheticBaselineWrittenBeforeRead, true);
      await copyButton.click(); // Ordinary production UI click. Never force or replace navigator.clipboard.
      const deadline = Date.now() + timeoutMs;
      let observed;
      do {
        observed = await run('snapshot');
        assertClipboardSnapshot(observed, sequence + 1);
        if (observed.messages.length === sequence + 1) break;
        await new Promise(resolve => setTimeout(resolve, 25));
      } while (Date.now() < deadline);
      assert.equal(observed.messages.length, sequence + 1, 'fresh native confirmation required for every copy');
      await page.getByText(choice === 'allow' ? SUCCESS : DENIED, { exact: true })
        .waitFor({ state: 'visible', timeout: timeoutMs });
      const readback = await run('readback', { sequence });
      assert.equal(readback.exactExpectedValue, true, 'system clipboard did not match the expected synthetic value');
      assert.equal(readback.comparisonOnly, true);
      await capture(`clipboard-${sequence}-${choice}`);
      steps.push({ choice, nativeConfirmationObserved: true, exactSyntheticReadback: true,
        productResult: choice === 'allow' ? 'copy-success' : 'copy-denied' });
    }
    const final = await run('snapshot');
    assertClipboardSnapshot(final, CHOICES.length);
    assert.equal(final.messages.length, 3);
    assert.equal(final.syntheticBaselineWrites, 3);
    assert.equal(final.comparisonOnlyReads, 6);
    assert.equal(page.url(), ownerURL);
    result = { mechanism: 'real-production-jd-copy-and-native-system-clipboard', steps,
      nativeDialogChoicesAutomated: true, nativePointerInteractionValidated: false,
      permissionHandlersReplaced: false, rendererClipboardAPIReplaced: false,
      preExistingClipboardNeverRead: true, syntheticBaselineWrittenBeforeEveryReadback: true,
      clipboardContentsIncludedInEvidence: false, readbackViaElectronMainProcessOnly: true,
      rendererClipboardReadAttempted: false, persistentClipboardApprovalGranted: false,
      oldClipboardRestorationAttempted: false, finalClipboardContainsSyntheticBaseline: true };
  } catch (error) { primaryError = error; }
  const cleanupErrors = [];
  try {
    const restored = await run('restore');
    if (!primaryError) {
      assert.equal(restored.restored, true);
      assertClipboardSnapshot(restored.finalSnapshot, CHOICES.length);
      assert.equal(restored.finalSnapshot.messages.length, 3);
      assert.equal(restored.finalSnapshot.syntheticBaselineWrites, 3);
      assert.equal(restored.finalSnapshot.comparisonOnlyReads, 6);
    }
  } catch { cleanupErrors.push(new Error('clipboard instrumentation cleanup or final observation failed')); }
  try { await owner.dispose(); } catch { cleanupErrors.push(new Error('clipboard owner handle cleanup failed')); }
  if (primaryError && cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'clipboard probe and cleanup failed');
  if (primaryError) throw primaryError;
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'clipboard probe cleanup failed');
  return { ...result, instrumentationRestored: true };
}
