import fs from 'node:fs/promises';
import path from 'node:path';
import { safeFailure } from './diagnostics.mjs';
import { checkGeometry, safeShotName, summarizeCoverage, classifyRuntimeMessage, publicRequestFailure, SUBVIEWS, ROOT_CASES } from './coverage-model.mjs';

export function observeRuntime(page) {
  const counts = {};
  const requests = [];
  let ownCriticalFailureCount = 0;
  let requestFailureCount = 0;
  let uncertainMutationOutcome = false;
  const inflightWrites = new Set();
  const add = (name) => { counts[name] = (counts[name] || 0) + 1; };
  page.on('pageerror', (error) => add(classifyRuntimeMessage(error.message, 'pageerror')));
  page.on('console', (message) => { if (message.type() === 'error') add(classifyRuntimeMessage(message.text())); });
  page.on('request', (request) => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) inflightWrites.add(request); });
  page.on('requestfinished', (request) => inflightWrites.delete(request));
  page.on('requestfailed', (request) => {
    if (inflightWrites.has(request)) uncertainMutationOutcome = true;
    inflightWrites.delete(request);
    const safe = publicRequestFailure(request.url(), new URL(page.url()).origin, null);
    if (safe) {
      const expectedCancellation = request.failure?.()?.errorText === 'net::ERR_ABORTED';
      requestFailureCount++;
      if (!expectedCancellation) ownCriticalFailureCount++;
      if (requests.length < 100) requests.push({ ...safe, expectedCancellation });
    }
  });
  page.on('response', (response) => {
    if (response.status() < 400) return;
    const safe = publicRequestFailure(response.url(), new URL(page.url()).origin, response.status(), response.request?.().method() || 'GET');
    if (safe) {
      requestFailureCount++;
      if (safe.status >= 500 || safe.category === 'own-asset' || (safe.category === 'own-api' && !safe.expectedAbsent)) ownCriticalFailureCount++;
      if (requests.length < 100) requests.push(safe);
    }
  });
  return { snapshot: () => ({ classifications: { ...counts }, ownRequestFailures: [...requests],
    ownCriticalFailureCount, requestFailureCount, uncertainMutationOutcome, bounded: requestFailureCount > requests.length }),
    hasPendingWrite: () => inflightWrites.size > 0 || uncertainMutationOutcome };
}

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
  let sequence = 0;
  let width = 1280;
  let height = 900;
  const save = async () => {
    report.summary = summarizeCoverage(report.cases);
    report.runtime = runtime.snapshot();
    await fs.writeFile(path.join(evidence, 'coverage.json'), `${JSON.stringify(report, null, 2)}\n`);
  };
  const size = async (requested, requestedHeight = 900) => {
    const window = await app.browserWindow(page);
    await window.evaluate((win, value) => win.setContentSize(value.width, value.height), { width: requested, height: requestedHeight });
    await page.waitForFunction((value) => window.innerWidth === value.width && window.innerHeight === value.height, { width: requested, height: requestedHeight });
    width = requested;
    height = requestedHeight;
  };
  const capture = async (label, extra = {}) => {
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
    const item = { filename: `screens/${filename}`, caseId: active?.caseId || null,
      ...measured, fixtureIds: report.fixtures.map(({ kind, id }) => ({ kind, id })), ...extra };
    report.screens.push(item);
    active?.screenshots.push(item.filename);
    await save(); // Geometry failures keep their screenshot.
    checkGeometry(measured, width);
    if (measured.height !== height) throw new Error('native content height changed before capture');
    if (measured.haruCoveredControls > 0) throw new Error('Haru intercepts visible product controls');
    return measured;
  };
  const run = async (surfaceId, caseId, uiPath, action, kind = 'interaction') => {
    active = { surfaceId, caseId, uiPath, kind, outcome: 'NOT RUN', assertions: [], screenshots: [],
      reason: 'started-not-completed' };
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
      const runtimeAfter = runtime.snapshot();
      active.runtimeDelta = Object.fromEntries(Object.entries(runtimeAfter.classifications)
        .map(([key, count]) => [key, count - (runtimeBefore.classifications[key] || 0)]).filter(([, count]) => count > 0));
      if ((runtimeAfter.ownCriticalFailureCount || 0) > (runtimeBefore.ownCriticalFailureCount || 0)) throw new Error('required own-origin request failed in this case');
      if (active.runtimeDelta['unexpected-page-error']) throw new Error('unexpected renderer error in this case');
      if (Object.keys(active.runtimeDelta).length && active.outcome !== 'BLOCKED') {
        active.outcome = 'BLOCKED';
        active.reason = 'runtime-error-observed-during-case-see-safe-classifications';
      }
      if (active.outcome !== 'BLOCKED') { active.outcome = 'PASS'; delete active.reason; }
    } catch (error) {
      active.outcome = 'FAIL';
      active.failure = safeFailure(error);
      active.reason = 'assertion-or-ui-action-failed';
      try { await capture(`${caseId}-failure`); } catch { /* Failed capture is not a pass. */ }
      if (!runtime.hasPendingWrite()) {
        try {
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
