import fs from 'node:fs/promises';
import path from 'node:path';
import { safeFailure } from './diagnostics.mjs';
import { observeRuntime } from './runtime-observer.mjs';
export { observeRuntime };
import { bindUiSteps, markUiStep, safeUiFailure } from './ui-locators.mjs';
import { readSurfaceIdentity } from './surface-identity.mjs';
import { checkGeometry, safeShotName, summarizeCoverage, SUBVIEWS, ROOT_CASES } from './coverage-model.mjs';

export async function createCoverage({ app, page, evidence, pin, installedExeSha256, runtime, setStage }) {
  const dir = path.join(evidence, 'screens');
  await fs.mkdir(dir, { recursive: true });
  const report = { schema: 2, sourceCommit: pin.commit, buildCommit: pin.buildCommit, buildWorkflow: pin.buildWorkflow,
    buildRunId: pin.runId, fullRegressionRunId: pin.fullRegressionRunId, artifactId: pin.artifactId,
    installerSha256: pin.installerSha256, installedExeSha256, execution: 'real-installed-electron-native-content-size',
    screenshotAnimationPolicy: 'CSS finite transitions fast-forwarded and infinite CSS animations temporarily cancelled by Playwright; not an animation-quality test',
    syntheticProfileOnly: true, aiInvocationsAuthorized: false, browserFixturesUsed: false, expectedRootCases: ROOT_CASES.length,
    cases: [], screens: [], fixtures: [], summary: null, runtime: null };
  let active;
  bindUiSteps(page, (value) => { if (active) active.lastStep = value; });
  let sequence = 0;
  let width = 1280;
  let height = 900;
  const save = async () => {
    report.summary = { ...summarizeCoverage(report.cases),
      confirmedTargetScreens: report.screens.filter((item) => item.targetSurfaceConfirmed).length,
      unconfirmedTargetScreens: report.screens.filter((item) => !item.targetSurfaceConfirmed).length };
    report.runtime = runtime.snapshot();
    await fs.writeFile(path.join(evidence, 'coverage.json'), `${JSON.stringify(report, null, 2)}\n`);
  };
  const size = async (requested, requestedHeight = 900) => {
    markUiStep(page, 'viewport-set');
    const window = await app.browserWindow(page);
    await window.evaluate((win, value) => win.setContentSize(value.width, value.height), { width: requested, height: requestedHeight });
    await page.waitForFunction((value) => window.innerWidth === value.width && window.innerHeight === value.height, { width: requested, height: requestedHeight });
    width = requested;
    height = requestedHeight;
  };
  const capture = async (label, extra = {}) => {
    markUiStep(page, 'screenshot-capture');
    const filename = safeShotName(`${String(++sequence).padStart(3, '0')}-${label}`);
    await page.screenshot({ path: path.join(dir, filename), timeout: 15000, animations: 'disabled' });
    const measured = await page.evaluate(() => {
      const controls = [...document.querySelectorAll('button, input, textarea, select, [role="button"]')];
      let haruCoveredControls = 0;
      for (const element of controls) {
        if (element.closest('[aria-label="Haru 助手"], [aria-hidden="true"], [inert]') || element.disabled || !element.getClientRects().length) continue;
        const style = getComputedStyle(element);
        if (style.visibility !== 'visible' || style.opacity === '0') continue;
        const rect = element.getBoundingClientRect();
        const x = rect.left + rect.width / 2; const y = rect.top + rect.height / 2;
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
        const top = document.elementFromPoint(x, y);
        // An explicitly opened context menu is expected to overlay the page.
        if (top?.closest('[aria-label="Haru 助手"]') && !top.closest('[role="menu"]')) haruCoveredControls++;
      }
      return { width: window.innerWidth, height: window.innerHeight,
        documentWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
        theme: document.documentElement.dataset.theme || 'unknown', haruCoveredControls };
    });
    const identity = await readSurfaceIdentity(page, active?.surfaceId);
    if (identity.targetSurfaceConfirmed && active) active.targetSurfaceConfirmed = true;
    const item = { ...identity, filename: `screens/${filename}`, caseId: active?.caseId || null,
      ...measured, fixtureIds: report.fixtures.map(({ kind, id }) => ({ kind, id })), ...extra };
    report.screens.push(item);
    active?.screenshots.push(item.filename);
    await save(); // Geometry failures keep their screenshot.
    markUiStep(page, 'geometry-check');
    // Preserve visible product defects without letting them prevent independent
    // synthetic fixture creation. The case remains irrevocably FAIL at completion.
    const issues = [];
    if (measured.documentWidth > measured.width + 1) issues.push('horizontal-overflow');
    if (measured.haruCoveredControls > 0) issues.push('haru-occlusion');
    item.geometryIssues = issues;
    if (issues.length && active) active.visualFailures.push({ screenshot: item.filename, issues });
    else if (issues.length) { const error = new Error('screen geometry failed'); error.code = 'UI_VISUAL_FAILURE'; throw error; }
    checkGeometry({ ...measured, documentWidth: measured.width }, width);
    if (measured.height !== height) throw new Error('native content height changed before capture');
    await save();
    return measured;
  };
  const run = async (surfaceId, caseId, uiPath, action, kind = 'interaction') => {
    active = { surfaceId, caseId, uiPath, kind, outcome: 'NOT RUN', assertions: [], screenshots: [],
      reason: 'started-not-completed', targetSurfaceConfirmed: false, visualFailures: [], lastStep: null };
    report.cases.push(active);
    setStage(`coverage-${caseId}`);
    await save();
    const runtimeBefore = runtime.snapshot();
    active.runtimeBefore = runtimeBefore.classifications;
    try {
      if (runtime.hasPendingWrite()) {
        active.outcome = 'BLOCKED';
        active.reason = 'previous-ui-write-still-pending';
        return;
      }
      await action();
      if (!active.screenshots.length) await capture(caseId);
      if (!active.assertions.length) throw new Error('case has no explicit assertions');
      if (active.kind !== 'diagnostic' && !active.targetSurfaceConfirmed) throw new Error('target surface was never verified in a screenshot');
      if (active.visualFailures.length) { const error = new Error('screen geometry failed'); error.code = 'UI_VISUAL_FAILURE'; throw error; }
      const runtimeAfter = runtime.snapshot();
      active.runtimeDelta = Object.fromEntries(Object.entries(runtimeAfter.classifications)
        .map(([key, count]) => [key, count - (runtimeBefore.classifications[key] || 0)]).filter(([, count]) => count > 0));
      if ((runtimeAfter.ownCriticalFailureCount || 0) > (runtimeBefore.ownCriticalFailureCount || 0)) throw new Error('required own-origin request failed in this case');
      if (active.runtimeDelta['unexpected-page-error']) throw new Error('unexpected renderer error in this case');
      if (Object.keys(active.runtimeDelta).some((key) => key !== 'expected-resource-console') && active.outcome !== 'BLOCKED') {
        active.outcome = 'BLOCKED';
        active.reason = 'runtime-error-observed-during-case-see-safe-classifications';
      }
      if (active.outcome !== 'BLOCKED') { active.outcome = 'PASS'; delete active.reason; }
    } catch (error) {
      active.outcome = 'FAIL';
      active.failure = { ...safeFailure(error), uiIssue: safeUiFailure(error) };
      active.failedStep = active.lastStep;
      active.reason = 'assertion-or-ui-action-failed';
      try { await capture(`${caseId}-failure`); } catch { /* Failed capture is not a pass. */ }
      if (!runtime.hasPendingWrite()) {
        try {
          markUiStep(page, 'recovery-reload');
          await page.reload({ waitUntil: 'domcontentloaded' });
          active.recovery = 'ordinary-reload-no-write-pending';
        } catch { active.recovery = 'failed'; }
      } else active.recovery = 'blocked-pending-write';
    } finally {
      await save();
      active = undefined;
    }
  };
  const observed = (value) => { if (active) active.assertions.push(value); };
  const blocked = (reason) => { if (active) { active.outcome = 'BLOCKED'; active.reason = reason; } };
  const disposition = async (surfaceId, caseId, outcome, reason, uiPath = []) => {
    report.cases.push({ surfaceId, caseId, outcome, reason, uiPath, kind: 'scope', assertions: [], screenshots: [] });
    await save();
  };
  const finish = async () => {
    for (const item of ROOT_CASES) if (!report.cases.some((row) => row.caseId === item.caseId)) {
      await disposition(item.surfaceId, item.caseId, 'NOT RUN', 'planned-root-state-theme-width-not-reached', [item.view]);
    }
    for (const item of SUBVIEWS) if (!report.cases.some((row) => row.surfaceId === item.id)) {
      await disposition(item.id, item.view, 'NOT RUN', 'no-completed-supported-UI-path-in-this-run');
    }
    await save();
    return report;
  };
  return { report, size, capture, run, observed, blocked, disposition, finish, save, runtimeSnapshot: runtime.snapshot, canProceed: () => !runtime.hasPendingWrite(),
    fixture: (kind, id) => { if (!Number.isSafeInteger(id) || id <= 0) throw new Error('invalid fixture identity'); report.fixtures.push({ kind, id }); } };
}
