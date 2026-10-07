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
        try {
          await page.goto(`http://127.0.0.1:5174/tests/desktop-layout/fixture.html?count=${count}&mascot=${mascot}`);
          const result = page.locator('[data-layout-result]');
          await page.waitForFunction(() => {
            const state = document.querySelector('[data-layout-result]')?.getAttribute('data-layout-result');
            return state && state !== 'pending';
          });
          assert.equal(await result.getAttribute('data-layout-result'), 'passed', name);
          if (mascot === 'normal') {
            await page.waitForFunction(() => ['ready', 'failed'].includes(document.documentElement.dataset.live2dState));
            assert.equal(await page.locator('html').getAttribute('data-live2d-state'), 'ready', `${name}: real Live2D mount`);
          }
          await page.screenshot({ path: path.join(output, `${name}.png`) });
          if (count) {
            await page.locator('tr[data-row-key="1"] td').first().click();
            await page.getByRole('dialog').waitFor();
            assert.match(await page.getByRole('dialog').innerText(), /特别长的公司名称以验证不会逐字折行/);
            await page.getByRole('button', { name: 'Close', exact: true }).click();
            await page.getByRole('dialog').waitFor({ state: 'hidden' });
            const table = page.locator('.ant-table-content');
            await table.evaluate(node => { node.scrollLeft = node.scrollWidth; });
            await page.locator('tr[data-row-key="1"]').getByRole('button', { name: '问 Pilot' }).click();
            await page.getByRole('dialog').waitFor();
            await page.getByRole('button', { name: 'Close', exact: true }).click();
            if (count > 10) {
              await page.getByTitle('2', { exact: true }).click();
              assert.equal(await page.locator('tr[data-row-key]').count(), 2);
            }
          }
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
          await page.screenshot({ path: path.join(output, `${name}-failed.png`) });
          results.push({ name, status: 'failed', error: String(error) });
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
