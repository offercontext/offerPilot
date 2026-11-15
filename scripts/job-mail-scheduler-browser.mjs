// Synthetic desktop scheduler evidence; no mailbox, vault, model, or business write.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { waitForMailState } from './job-mail-browser-wait.mjs';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const base = 'http://127.0.0.1:38091';
const output = process.env.MAIL_EVIDENCE_DIR;
assert.ok(output);
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH, headless: true, chromiumSandbox: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' });
const blocked = [];
await context.route('**/*', route => {
  if (new URL(route.request().url()).origin !== base) { blocked.push('external-request-blocked'); return route.abort(); }
  return route.continue();
});
const page = await context.newPage();
const pageErrors = [];
page.on('pageerror', () => pageErrors.push('page-error'));
async function api(endpoint, method = 'GET') {
  const response = await context.request.fetch(base + '/api' + endpoint, { method });
  assert.ok(response.ok(), `${endpoint}: ${response.status()}`);
  return response.json();
}
const checks = [];
const settings = page.locator('section[aria-labelledby="job-mail-settings-title"]');
async function screenshot(name) {
  await page.evaluate(() => document.fonts.ready);
  let previous = '';
  let stable = 0;
  await waitForMailState(async () => page.evaluate(() => {
    const panel = document.querySelector('section[aria-labelledby="job-mail-settings-title"]');
    const rect = panel?.getBoundingClientRect();
    const moving = [...document.querySelectorAll('.ant-modal, .ant-modal-mask, .ant-modal-content')]
      .some(element => element.getAnimations().some(animation => animation.playState === 'running'));
    return { moving, geometry: rect ? [rect.x, rect.y, rect.width, rect.height] : null };
  }), snapshot => {
    const current = JSON.stringify(snapshot.geometry);
    stable = !snapshot.moving && snapshot.geometry && current === previous ? stable + 1 : 0;
    previous = current;
    return stable >= 2;
  }, { timeoutMs: 3000, pollMs: 100 });
  await settings.screenshot({ path: path.join(output, name + '.png') });
}
async function awaitRun(previous, expected) {
  return waitForMailState(() => api('/job-mail/status'), state => {
    if (!state.run || state.run.id === previous || state.run.status === 'running') return false;
    assert.equal(state.run.status, expected);
    return true;
  }, { timeoutMs: 25000, pollMs: 200 });
}
try {
  await page.goto(base + '/?view=settings');
  await settings.getByRole('button', { name: '连接合成测试邮箱', exact: true }).click();
  const scope = page.getByRole('dialog', { name: '连接合成测试邮箱' });
  await scope.getByLabel('INBOX', { exact: true }).check();
  await scope.getByLabel(/最近 7 天/).check();
  await scope.getByRole('button', { name: '确认范围并保存' }).click();
  await scope.waitFor({ state: 'hidden' });
  await settings.getByRole('switch', { name: '自动同步' }).click();
  await settings.getByLabel('同步间隔', { exact: true }).fill('5');
  await settings.getByRole('button', { name: '保存同步方式' }).click();
  const pending = await waitForMailState(() => api('/job-mail/status'), s => s.connection.sync_mode === 'automatic');
  assert.equal(pending.run, null);
  assert.equal((await api('/job-mail/fixture-scheduler')).read_attempts, 0);
  await api('/job-mail/fixture-scheduler/due', 'POST');
  let result = await awaitRun(null, 'completed');
  assert.equal(result.run.trigger, 'automatic');
  assert.equal(result.run.progress.candidates, 1);
  assert.equal((await api('/application-events')).length, 0);
  await page.reload();
  await settings.getByText('最近任务：本次已完成', { exact: true }).waitFor();
  await screenshot('10-synthetic-automatic-tick-completed');
  checks.push('Real runtime loop tick starts due automatic work; unopened business events remain zero');

  await api('/job-mail/fixture-scheduler/fail', 'POST');
  for (let i = 0; i < 3; i++) {
    const previous = result.run.id;
    await api('/job-mail/fixture-scheduler/due', 'POST');
    result = await awaitRun(previous, 'failed');
    assert.equal(result.run.trigger, 'automatic');
  }
  assert.equal(result.connection.sync_mode, 'manual');
  assert.equal(result.connection.next_run_at, null);
  await page.reload();
  await settings.getByText('最近任务：本次失败', { exact: true }).waitFor();
  assert.equal(await settings.getByRole('switch', { name: '自动同步' }).getAttribute('aria-checked'), 'false');
  await screenshot('11-synthetic-three-failures-paused');
  checks.push('Three actual automatic failures pause future scheduling and expose failure in product UI');

  await api('/job-mail/fixture-scheduler/recover', 'POST');
  await api('/job-mail/fixture-scheduler/cooldown', 'POST');
  await page.reload();
  await settings.getByRole('button', { name: '立即同步', exact: true }).click();
  result = await awaitRun(result.run.id, 'completed');
  assert.equal(result.run.trigger, 'manual');
  assert.equal(result.connection.sync_mode, 'manual');
  assert.equal(result.connection.next_run_at, null);
  assert.equal((await api('/job-mail/suggestions')).total, 1);
  await page.reload();
  await settings.getByText('最近任务：本次已完成', { exact: true }).waitFor();
  await screenshot('12-synthetic-manual-recovery-no-auto-resume');
  checks.push('Manual UI retry succeeds without duplicate suggestion or silently re-enabling automatic');
  assert.deepEqual(blocked, []);
  assert.deepEqual(pageErrors, []);
  assert.equal((await api('/application-events')).length, 0);
  await fs.writeFile(path.join(output, 'scheduler-result.json'), JSON.stringify({ status: 'passed', synthetic_only: true, clock_scope: 'Fixture seeds due/throttle deadlines; unmodified scheduler loop invokes tick; not five-minute wall-clock endurance', checks }, null, 2));
} catch (error) {
  await fs.writeFile(path.join(output, 'scheduler-result.json'), JSON.stringify({ status: 'failed', synthetic_only: true, completed_checks: checks }, null, 2));
  await page.screenshot({ path: path.join(output, 'scheduler-failure.png'), fullPage: true }).catch(() => {});
  throw error;
} finally { await browser.close(); }
