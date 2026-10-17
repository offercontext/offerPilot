import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

export const SYNTHETIC_JD_SOURCE = 'https://example.invalid/qa-local-only';
const CHOICES = Object.freeze(['cancel', 'allow', 'cancel']);
const SUCCESS = '来源已复制';
const DENIED = '无法复制，请手动选择来源文字';

const DIAGNOSTIC_PHASES = Object.freeze(['install', 'focus-owner', 'wait-prior-toast', 'arm', 'click',
  'confirm', 'toast', 'readback', 'capture', 'final-snapshot', 'restore', 'dispose-owner', 'complete']);
const NATIVE_PHASES = Object.freeze(['installed', 'arm-check', 'baseline-write', 'baseline-read', 'baseline-verified',
  'dialog-check', 'dialog-answered', 'readback-check', 'readback-read', 'restored']);
const NATIVE_FAILURES = Object.freeze(['none', 'unsafe-to-arm', 'sequence-rejected', 'baseline-write-error',
  'baseline-read-error', 'baseline-mismatch', 'unexpected-confirmation', 'unsafe-readback', 'wrong-request',
  'readback-error', 'readback-mismatch', 'dialog-restore-failed', 'baseline-write-timeout', 'baseline-read-timeout', 'readback-timeout']);

// Only this fixed projection may reach evidence. Never pass a main-process
// object, exception, clipboard value, URL, or path through the callback.
export function clipboardProbeDiagnostic({ phase, sequence, native, outcome = 'observed', errorClass }) {
  const flag = value => typeof value === 'boolean' ? value : 'not-observed';
  const count = (value, max) => Number.isInteger(value) && value >= 0 && value <= max ? value : 'not-observed';
  return { schemaVersion: 1, probe: 'clipboard', phase: DIAGNOSTIC_PHASES.includes(phase) ? phase : 'install',
    sequence: Number.isInteger(sequence) && sequence >= 0 && sequence <= 2 ? sequence : null,
    outcome: ['observed', 'failed', 'complete'].includes(outcome) ? outcome : 'failed',
    observed: {
      nativeDiagnosticAvailable: Boolean(native && NATIVE_PHASES.includes(native.nativePhase)),
      nativePhase: NATIVE_PHASES.includes(native?.nativePhase) ? native.nativePhase : 'not-observed',
      nativeFailure: NATIVE_FAILURES.includes(native?.nativeFailure) ? native.nativeFailure : 'not-observed',
      ownerURLUnchanged: flag(native?.ownerURLUnchanged), ownerDestroyed: flag(native?.ownerDestroyed),
      pendingConfirmation: flag(native?.pendingConfirmation), hasFailures: flag(native?.hasFailures),
      operationAwaitPending: flag(native?.operationAwaitPending),
      baselineWriteAttempted: flag(native?.baselineWriteAttempted), baselineWriteCompleted: flag(native?.baselineWriteCompleted),
      baselineReadAttempted: flag(native?.baselineReadAttempted), baselineMatched: flag(native?.baselineMatched),
      confirmationCount: count(native?.confirmationCount, 4), baselineWrites: count(native?.baselineWrites, 3),
      comparisonReads: count(native?.comparisonReads, 6),
    },
    errorClass: ['Error', 'AssertionError', 'TimeoutError', 'TypeError', 'SyntaxError'].includes(errorClass) ? errorClass
      : errorClass === undefined ? 'none' : 'unknown',
  };
}

export function assertHostedClipboardEnvironment(environment = process.env, platform = process.platform) {
  assert.equal(platform, 'win32', 'clipboard probe requires actual Windows');
  assert.equal(environment.GITHUB_ACTIONS, 'true', 'clipboard probe requires GitHub Actions');
  assert.equal(environment.RUNNER_ENVIRONMENT, 'github-hosted', 'clipboard probe requires an isolated hosted runner');
}

// Serialized into the real Electron main process. The production permission
// handlers remain installed. Only their exact clipboard confirmation is answered.
// Never read or preserve the pre-existing clipboard, nor return clipboard text.
export async function nativeClipboardInstrumentation({ dialog, clipboard }, args) {
  const operationTimeoutMs = args.operationTimeoutMs ?? 15000;
  if (!Number.isFinite(operationTimeoutMs) || operationTimeoutMs <= 0 || operationTimeoutMs > 30000) {
    throw new Error('invalid native clipboard operation timeout');
  }
  // Electron 44.5.1 readText/writeText return Promises. Await completion in the
  // main process; comparing an unawaited read Promise to text always mismatches.
  // https://github.com/electron/electron/blob/v44.5.1/docs/api/clipboard.md
  // A deadline stops this probe, not the underlying native operation. A late
  // write completion/rejection must never schedule a read or an automatic retry.
  const complete = async operation => {
    let timer;
    try {
      return await Promise.race([operation(), new Promise((_, reject) => {
        timer = setTimeout(() => {
          const error = new Error('native clipboard operation timed out');
          error.code = 'CLIPBOARD_OPERATION_TIMEOUT';
          reject(error);
        }, operationTimeoutMs);
      })]);
    } finally { clearTimeout(timer); }
  };
  const key = Symbol.for(args.key);
  if (args.operation === 'install') {
    if (globalThis[key]) throw new Error('clipboard instrumentation already installed');
    const owner = args.owner;
    const contents = owner.webContents;
    if (contents.isDestroyed() || contents.getURL() !== args.ownerURL) throw new Error('clipboard owner mismatch');
    if (args.expectedText !== 'https://example.invalid/qa-local-only') throw new Error('non-synthetic clipboard target refused');
    const state = { owner, contents, ownerURL: args.ownerURL, expectedText: args.expectedText,
      originalMessage: dialog.showMessageBox, armed: null, baseline: null, baselineWritten: false,
      writes: 0, reads: 0, messages: [], failures: [], nativePhase: 'installed', nativeFailure: 'none',
      baselineWriteAttempted: false, baselineWriteCompleted: false, baselineReadAttempted: false, baselineMatched: null,
      ioPending: false };
    state.wrapper = async (win, options) => {
      state.nativePhase = 'dialog-check';
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
      if (!expected) { state.failures.push('unexpected-native-confirmation'); state.nativeFailure = 'unexpected-confirmation'; }
      state.nativePhase = 'dialog-answered';
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
  const diagnostic = () => ({ nativePhase: state.nativePhase, nativeFailure: state.nativeFailure,
      ownerURLUnchanged: ownerUnchanged(), ownerDestroyed: state.contents.isDestroyed(),
      pendingConfirmation: Boolean(state.armed), hasFailures: state.failures.length > 0,
      operationAwaitPending: state.ioPending,
      baselineWriteAttempted: state.baselineWriteAttempted, baselineWriteCompleted: state.baselineWriteCompleted,
      baselineReadAttempted: state.baselineReadAttempted, baselineMatched: state.baselineMatched,
      confirmationCount: state.messages.length, baselineWrites: state.writes, comparisonReads: state.reads });
  if (args.operation === 'diagnostic') return diagnostic();
  if (args.operation === 'arm') {
    state.nativePhase = 'arm-check';
    if (!ownerUnchanged() || state.armed || state.failures.length || state.ioPending) {
      state.nativeFailure = 'unsafe-to-arm'; throw new Error('clipboard probe not safe to arm');
    }
    const choices = ['cancel', 'allow', 'cancel'];
    if (!Number.isInteger(args.sequence) || args.sequence !== state.messages.length
      || args.sequence < 0 || args.sequence >= choices.length || args.choice !== choices[args.sequence]) {
      state.nativeFailure = 'sequence-rejected'; throw new Error('clipboard probe sequence rejected');
    }
    state.baselineWritten = false;
    state.baseline = `OfferPilot hosted clipboard synthetic baseline ${args.sequence}`;
    // This successful write must precede every possible clipboard read, including
    // cancellation readback. No attempt is made to save/restore old contents.
    state.baselineWriteAttempted = true;
    state.baselineWriteCompleted = false;
    state.baselineReadAttempted = false;
    state.baselineMatched = null;
    state.nativePhase = 'baseline-write';
    state.ioPending = true;
    try {
      try { await complete(() => clipboard.writeText(state.baseline)); }
      catch (error) {
        state.nativeFailure = error?.code === 'CLIPBOARD_OPERATION_TIMEOUT' ? 'baseline-write-timeout' : 'baseline-write-error';
        throw new Error('synthetic clipboard baseline write failed');
      }
      state.baselineWriteCompleted = true;
      state.writes++;
      if (!ownerUnchanged() || globalThis[key] !== state) {
        state.nativeFailure = 'unsafe-to-arm'; throw new Error('clipboard owner changed before baseline read');
      }
      state.reads++;
      state.nativePhase = 'baseline-read';
      state.baselineReadAttempted = true;
      let baselineMatched;
      try { baselineMatched = (await complete(() => clipboard.readText())) === state.baseline; }
      catch (error) {
        state.nativeFailure = error?.code === 'CLIPBOARD_OPERATION_TIMEOUT' ? 'baseline-read-timeout' : 'baseline-read-error';
        throw new Error('synthetic clipboard baseline read failed');
      }
      state.baselineMatched = baselineMatched;
      state.baselineWritten = baselineMatched;
      if (!baselineMatched) { state.nativeFailure = 'baseline-mismatch'; throw new Error('synthetic clipboard baseline not established'); }
      if (!ownerUnchanged() || globalThis[key] !== state) {
        state.nativeFailure = 'unsafe-to-arm'; throw new Error('clipboard owner changed during baseline read');
      }
      state.nativePhase = 'baseline-verified';
      state.armed = { choice: args.choice, sequence: args.sequence };
      return { baselineMatched, syntheticBaselineWrittenBeforeRead: true };
    } finally { state.ioPending = false; }
  }
  if (args.operation === 'snapshot') {
    return { messages: state.messages, failures: state.failures, ownerURLUnchanged: ownerUnchanged(),
      syntheticBaselineWrites: state.writes, comparisonOnlyReads: state.reads };
  }
  if (args.operation === 'readback') {
    state.nativePhase = 'readback-check';
    if (!state.baselineWritten || !ownerUnchanged() || state.armed || state.failures.length || state.ioPending) {
      state.nativeFailure = 'unsafe-readback'; throw new Error('clipboard readback not safe');
    }
    const last = state.messages.at(-1);
    if (!last || last.sequence !== args.sequence || !last.expected) {
      state.nativeFailure = 'wrong-request'; throw new Error('clipboard request not confirmed');
    }
    state.reads++;
    state.nativePhase = 'readback-read';
    state.ioPending = true;
    try {
      let exactExpectedValue;
      try { exactExpectedValue = (await complete(() => clipboard.readText())) === (last.choice === 'allow' ? state.expectedText : state.baseline); }
      catch (error) {
        state.nativeFailure = error?.code === 'CLIPBOARD_OPERATION_TIMEOUT' ? 'readback-timeout' : 'readback-error';
        throw new Error('synthetic clipboard readback failed');
      }
      if (!ownerUnchanged() || globalThis[key] !== state) {
        state.nativeFailure = 'unsafe-readback'; throw new Error('clipboard owner changed during readback');
      }
      if (!exactExpectedValue) state.nativeFailure = 'readback-mismatch';
      return { exactExpectedValue, comparisonOnly: true };
    } finally { state.ioPending = false; }
  }
  if (args.operation === 'restore') {
    dialog.showMessageBox = state.originalMessage;
    if (dialog.showMessageBox !== state.originalMessage) {
      state.nativeFailure = 'dialog-restore-failed'; throw new Error('clipboard dialog restoration failed');
    }
    state.nativePhase = 'restored';
    // Freeze the observation window atomically with restoration, including any
    // late confirmation that arrived after the previous asynchronous snapshot.
    const finalSnapshot = { messages: state.messages, failures: state.failures, ownerURLUnchanged: ownerUnchanged(),
      syntheticBaselineWrites: state.writes, comparisonOnlyReads: state.reads };
    const finalDiagnostic = diagnostic();
    delete globalThis[key];
    return { restored: true, finalSnapshot, diagnostic: finalDiagnostic,
      preExistingClipboardNeverRead: true, oldClipboardRestorationAttempted: false };
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
  expectedText = SYNTHETIC_JD_SOURCE, capture = async () => {}, setStage = () => {}, timeoutMs = 15000,
  onDiagnostic = async () => {} }) {
  assertHostedClipboardEnvironment();
  assert.equal(expectedText, SYNTHETIC_JD_SOURCE, 'only the fixed synthetic JD source may be copied');
  assert.ok(Number.isFinite(timeoutMs) && timeoutMs > 0 && timeoutMs <= 30000);
  assert.equal(typeof capture, 'function');
  assert.equal(typeof setStage, 'function');
  assert.equal(typeof onDiagnostic, 'function');
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
    { operation, key, ownerURL, operationTimeoutMs: timeoutMs, ...extra });
  const steps = [];
  let phase = 'install';
  let sequence = null;
  let native;
  const publish = async (nextPhase, outcome = 'observed', errorClass) => {
    phase = nextPhase;
    try { native = await run('diagnostic'); } catch { /* Preserve the last safe observation if state is gone. */ }
    await onDiagnostic(clipboardProbeDiagnostic({ phase, sequence, native, outcome, errorClass }));
  };
  let primaryError;
  let failurePhase;
  let result;
  try {
    setStage('clipboard-instrumentation');
    await run('install', { owner, expectedText });
    await publish('focus-owner');
    await page.bringToFront();
    for (sequence = 0; sequence < CHOICES.length; sequence++) {
      const choice = CHOICES[sequence];
      setStage(`clipboard-${sequence}-${choice}`);
      await publish('wait-prior-toast');
      // Previous toasts cannot satisfy a later interaction's result assertion.
      await page.getByText(SUCCESS, { exact: true }).waitFor({ state: 'hidden', timeout: timeoutMs });
      await page.getByText(DENIED, { exact: true }).waitFor({ state: 'hidden', timeout: timeoutMs });
      await publish('arm');
      const baseline = await run('arm', { sequence, choice });
      assert.equal(baseline.baselineMatched, true);
      assert.equal(baseline.syntheticBaselineWrittenBeforeRead, true);
      await publish('click');
      await copyButton.click(); // Ordinary production UI click. Never force or replace navigator.clipboard.
      await publish('confirm');
      const deadline = Date.now() + timeoutMs;
      let observed;
      do {
        observed = await run('snapshot');
        assertClipboardSnapshot(observed, sequence + 1);
        if (observed.messages.length === sequence + 1) break;
        await new Promise(resolve => setTimeout(resolve, 25));
      } while (Date.now() < deadline);
      assert.equal(observed.messages.length, sequence + 1, 'fresh native confirmation required for every copy');
      await publish('toast');
      await page.getByText(choice === 'allow' ? SUCCESS : DENIED, { exact: true })
        .waitFor({ state: 'visible', timeout: timeoutMs });
      await publish('readback');
      const readback = await run('readback', { sequence });
      assert.equal(readback.exactExpectedValue, true, 'system clipboard did not match the expected synthetic value');
      assert.equal(readback.comparisonOnly, true);
      await publish('capture');
      await capture(`clipboard-${sequence}-${choice}`);
      steps.push({ choice, nativeConfirmationObserved: true, exactSyntheticReadback: true,
        productResult: choice === 'allow' ? 'copy-success' : 'copy-denied' });
    }
    await publish('final-snapshot');
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
  } catch (error) {
    primaryError = error; failurePhase = phase;
    try { await publish(failurePhase, 'failed', error?.name); } catch { /* Cleanup remains mandatory if evidence callback fails. */ }
  }
  const cleanupErrors = [];
  try {
    const restored = await run('restore');
    if (!primaryError) {
      native = restored.diagnostic;
      assert.equal(restored.restored, true);
      assertClipboardSnapshot(restored.finalSnapshot, CHOICES.length);
      assert.equal(restored.finalSnapshot.messages.length, 3);
      assert.equal(restored.finalSnapshot.syntheticBaselineWrites, 3);
      assert.equal(restored.finalSnapshot.comparisonOnlyReads, 6);
    }
  } catch { cleanupErrors.push(new Error('clipboard instrumentation cleanup or final observation failed')); }
  try { await owner.dispose(); } catch { cleanupErrors.push(new Error('clipboard owner handle cleanup failed')); }
  try { await publish(failurePhase ?? (cleanupErrors.length ? 'restore' : 'complete'),
    primaryError || cleanupErrors.length ? 'failed' : 'complete', primaryError?.name ?? cleanupErrors[0]?.name); }
  catch { cleanupErrors.push(new Error('clipboard diagnostic callback failed')); }
  if (primaryError && cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'clipboard probe and cleanup failed');
  if (primaryError) throw primaryError;
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'clipboard probe cleanup failed');
  return { ...result, instrumentationRestored: true };
}
