import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Reuse the already locked, isolated UI helper; no production dependency changes.
const root = fileURLToPath(new URL('../../../', import.meta.url));
const require = createRequire(path.join(root, 'desktop/installed-ui/package.json'));
const { chromium } = require('playwright-core');
const output = path.resolve(process.env.LAYOUT_EVIDENCE_DIR || path.join(root, 'desktop/ci-evidence/layout'));
await mkdir(output, { recursive: true });
const server = spawn(process.execPath, [path.join(root, 'web/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '5174', '--strictPort'], { cwd: path.join(root, 'web'), stdio: ['ignore', 'pipe', 'pipe'] });
let serverLog = '';
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
let browser;
const results = [];
try {
  for (let i = 0; ; i++) {
    try { if ((await fetch('http://127.0.0.1:5174/tests/desktop-layout/fixture.html')).ok) break; } catch {}
    if (server.exitCode !== null || i >= 100) throw new Error(`Vite did not start: ${serverLog}`);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  browser = await chromium.launch({ channel: process.env.LAYOUT_BROWSER_CHANNEL || 'msedge' });
  for (const width of [900, 1008, 1280, 1440]) {
    for (const count of [0, 1, 12]) {
      for (const mascot of ['failure', 'normal', 'hidden']) {
        const name = `${width}x689-${count}-${mascot}`;
        const page = await browser.newPage({ viewport: { width, height: 689 }, colorScheme: 'dark' });
        page.setDefaultTimeout(15_000);
        page.setDefaultNavigationTimeout(15_000);
        let stage = 'load';
        try {
          await page.goto(`http://127.0.0.1:5174/tests/desktop-layout/fixture.html?count=${count}&mascot=${mascot}`);
          const result = page.locator('[data-layout-result]');
          await page.waitForFunction(() => {
            const state = document.querySelector('[data-layout-result]')?.getAttribute('data-layout-result');
            return state && state !== 'pending';
          });
          assert.equal(await result.getAttribute('data-layout-result'), 'passed', name);
          stage = 'normal-layout';
          if (mascot === 'normal') {
            await page.waitForFunction(() => ['ready', 'failed'].includes(document.documentElement.dataset.live2dState));
            assert.equal(await page.locator('html').getAttribute('data-live2d-state'), 'ready', `${name}: real Live2D mount`);
            await page.screenshot({ path: path.join(output, `${name}.png`), animations: 'disabled' });
            const overlap = await page.evaluate(() => {
              const mascot = document.querySelector('aside[aria-label="Haru 助手"]').getBoundingClientRect();
              const table = document.querySelector('[data-pilot-mascot-safe-area]').getBoundingClientRect();
              return mascot.left < table.right && mascot.right > table.left && mascot.top < table.bottom && mascot.bottom > table.top;
            });
            assert.equal(overlap, false, 'normal character must not obscure table or pagination');
          }
          if (mascot !== 'normal') await page.screenshot({ path: path.join(output, `${name}.png`), animations: 'disabled' });
          stage = 'filter-list';
          if (count > 10) {
            const search = page.getByPlaceholder('搜索公司、岗位、备注');
            await search.fill(`layout-row-${count}`);
            await page.waitForFunction(() => document.querySelectorAll('tr[data-row-key]').length === 1);
            if (mascot === 'normal') await page.waitForFunction(() => !document.querySelector('[data-pilot-list-character]'));
            await page.screenshot({ path: path.join(output, `${name}-filtered-one.png`), animations: 'disabled' });
            await search.fill('');
            await page.waitForFunction(() => document.querySelectorAll('tr[data-row-key]').length === 10);
            if (mascot === 'normal') await page.waitForFunction(() => document.querySelector('[data-pilot-list-character]'));
          }
          if (count) {
            stage = 'open-detail';
            const visibleRow = page.locator('tr[data-row-key]').first();
            const rowId = await visibleRow.getAttribute('data-row-key');
            assert.ok(rowId, 'current page must expose a real record id');
            const completeText = await visibleRow.locator('td').first().locator('[title]').evaluateAll(nodes => nodes.map(node => node.getAttribute('title')));
            assert.equal(completeText.length, 2);
            await visibleRow.locator('td').first().click();
            await page.getByRole('dialog').waitFor();
            for (const text of completeText) {
              assert.ok(text && text.length > 20);
              assert.equal(await page.getByRole('dialog').getByText(text, { exact: true }).count(), 1);
            }
            await page.getByRole('button', { name: 'Close', exact: true }).click();
            await page.getByRole('dialog').waitFor({ state: 'hidden' });
            stage = 'horizontal-scroll';
            const table = page.locator('.ant-table-content');
            const overflow = await table.evaluate(node => node.scrollWidth > node.clientWidth);
            if (overflow) {
              await page.getByRole('region', { name: '投递表格，可横向滚动' }).focus();
              await page.keyboard.press('ArrowRight');
              await page.waitForFunction(() => document.querySelector('.ant-table-content').scrollLeft > 0);
              await page.keyboard.press('ArrowLeft');
              await page.waitForFunction(() => document.querySelector('.ant-table-content').scrollLeft === 0);
              await table.hover();
              await page.mouse.wheel(1200, 0);
              await page.waitForFunction(() => document.querySelector('.ant-table-content').scrollLeft > 0);
              await page.screenshot({ path: path.join(output, `${name}-scrolled-right.png`), animations: 'disabled' });
            }
            stage = 'pointer-pilot';
            await page.locator(`tr[data-row-key="${rowId}"]`).getByRole('button', { name: '问 Pilot' }).click();
            await page.getByRole('dialog').waitFor();
            await page.getByRole('button', { name: 'Close', exact: true }).click();
            if (count > 10) {
              stage = 'pagination';
              await page.getByTitle('2', { exact: true }).click();
              assert.equal(await page.locator('tr[data-row-key]').count(), 2);
              if (mascot === 'normal' && width >= 1280) await page.waitForFunction(() => !document.querySelector('[data-pilot-list-character]'));
              await page.screenshot({ path: path.join(output, `${name}-page-two.png`), animations: 'disabled' });
            }
          }
          if (count) {
            stage = 'keyboard-focus';
            await page.getByPlaceholder('搜索公司、岗位、备注').click();
            let keyboardPilot = false;
            for (let tab = 0; tab < 12; tab++) {
              await page.keyboard.press('Tab');
              keyboardPilot = await page.evaluate(() => document.activeElement?.tagName === 'BUTTON' && document.activeElement.textContent?.includes('问 Pilot'));
              if (keyboardPilot) break;
            }
            assert.equal(keyboardPilot, true, 'Pilot table action must be keyboard reachable');
            stage = 'keyboard-pilot';
            await page.keyboard.press('Enter');
            await page.getByRole('dialog').waitFor();
            await page.getByRole('button', { name: 'Close', exact: true }).click();
          }
          stage = 'fallback-actions';
          if (mascot === 'failure') {
            const trigger = page.getByRole('button', { name: /打开 OfferPilot 领航员（Haru 模型未加载/ });
            await trigger.click();
            await page.getByRole('dialog').waitFor();
            await page.getByRole('button', { name: 'Close', exact: true }).click();
            await trigger.click({ button: 'right' });
            await page.getByRole('menuitem', { name: '隐藏角色' }).click();
            assert.equal(await page.getByRole('complementary', { name: 'Haru 助手' }).count(), 0);
          }
          results.push({ name, status: 'passed' });
        } catch (error) {
          await page.screenshot({ path: path.join(output, `${name}-failed.png`), animations: 'disabled' });
          results.push({ name, status: 'failed', stage, error: String(error) });
        } finally { await page.close(); }
      }
    }
  }
  assert.equal(results.filter(item => item.status === 'failed').length, 0, JSON.stringify(results.filter(item => item.status === 'failed')));
} finally {
  await writeFile(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  await writeFile(path.join(output, 'vite.log'), serverLog);
  await browser?.close();
  server.kill();
}
console.log(`Passed ${results.length} rendered viewport cases and interactions.`);
