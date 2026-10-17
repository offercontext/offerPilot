import assert from 'node:assert/strict';
import path from 'node:path';

const boardSelector = '[data-kanban-board]';
const bodySelector = '[data-kanban-column="pending"] [data-kanban-column-body]';

async function measure(page) {
  return page.locator(boardSelector).evaluate(board => {
    const columns = [...board.children];
    const bodies = columns.map(column => column.lastElementChild);
    const cards = [...bodies[0].querySelectorAll('[aria-roledescription="draggable"]')];
    return {
      board: { width: board.clientWidth, scrollWidth: board.scrollWidth, overflowX: getComputedStyle(board).overflowX },
      pageFits: document.documentElement.scrollWidth <= innerWidth + 1,
      columns: bodies.map(body => ({ width: body.clientWidth, scrollWidth: body.scrollWidth, height: body.clientHeight, scrollHeight: body.scrollHeight })),
      cards: cards.map(card => {
        const bounds = card.getBoundingClientRect();
        const controls = [...card.querySelectorAll('.ant-select, button, [aria-label="delete"]')];
        const text = [...card.children].slice(0, 2);
        return {
          width: card.clientWidth, scrollWidth: card.scrollWidth,
          controlsFit: controls.every(control => {
            const rect = control.getBoundingClientRect();
            return rect.left >= bounds.left + 1 && rect.right <= bounds.right - 1 && rect.top >= bounds.top && rect.bottom <= bounds.bottom;
          }),
          textFits: text.every(node => {
            const range = document.createRange(); range.selectNodeContents(node);
            return [...range.getClientRects()].every(rect => rect.left >= bounds.left + 1 && rect.right <= bounds.right - 1);
          }),
        };
      }),
    };
  });
}

async function checkControls(page) {
  const first = page.locator(bodySelector).locator('[aria-roledescription="draggable"]').first();
  await first.getByTitle('查看详情', { exact: true }).click();
  const detail = page.getByRole('dialog', { name: '投递详情', exact: true });
  await detail.waitFor();
  await detail.getByRole('button', { name: 'Close', exact: true }).click();
  await detail.waitFor({ state: 'hidden' });

  // The Select and its portal remain usable after wrapping, without starting a drag.
  await first.locator('.ant-select-selector').click();
  await page.locator('.ant-select-item-option').filter({ hasText: '面试' }).click();
  const confirmation = page.getByRole('dialog', { name: '确认更新投递状态', exact: true });
  await confirmation.waitFor();
  await confirmation.getByRole('button', { name: /取\s*消/ }).click();
  await confirmation.waitFor({ state: 'hidden' });
  assert.equal(await first.locator('.ant-select-selection-item').textContent(), '待投递');

  await first.locator('[aria-label="delete"]').click();
  await page.getByText('确定删除这条投递？', { exact: true }).waitFor();
  await page.locator('.ant-popconfirm').getByRole('button', { name: /取\s*消/ }).click();
  await page.locator('.ant-popconfirm').waitFor({ state: 'hidden' });

  // Drag the card itself to the neighboring lifecycle column, then cancel.
  const card = await first.boundingBox();
  const target = await page.locator(`${boardSelector} > div`).nth(1).boundingBox();
  assert.ok(card && target);
  await page.mouse.move(card.x + card.width / 2, card.y + 20);
  await page.mouse.down();
  await page.mouse.move(card.x + card.width / 2 + 8, card.y + 20, { steps: 3 });
  await page.mouse.move(target.x + target.width / 2, target.y + 100, { steps: 12 });
  await page.mouse.up();
  await confirmation.waitFor();
  assert.match(await confirmation.textContent(), /已投递/);
  await confirmation.getByRole('button', { name: /取\s*消/ }).click();
  await confirmation.waitFor({ state: 'hidden' });

  // Long unbroken labels remain fully available in the existing detail dialog.
  const english = page.locator(bodySelector).locator('[aria-roledescription="draggable"]').nth(1);
  await english.getByTitle('查看详情', { exact: true }).click();
  await detail.waitFor();
  assert.match(await detail.textContent(), /InternationalCompanyWithAnExtremelyLongUnbrokenEnglishNameForLayoutRegression/);
  assert.match(await detail.textContent(), /SeniorPlatformEngineerWithAnUnbrokenTitleAndAdditionalResponsibilities/);
  await detail.getByRole('button', { name: 'Close', exact: true }).click();
  await detail.waitFor({ state: 'hidden' });
}

export async function runKanbanCases(browser, output, results) {
  for (const width of [900, 1008, 1280, 1440]) {
    for (const theme of ['dark', 'light']) {
      for (const count of [0, 12]) {
        const name = `${width}x689-kanban-${count}-${theme}`;
        const page = await browser.newPage({ viewport: { width, height: 689 }, colorScheme: theme });
        page.setDefaultTimeout(15_000);
        const requests = [];
        await page.route('**/api/**', async route => {
          requests.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
          await route.fulfill({ status: 403, contentType: 'application/json', body: '{}' });
        });
        let geometry;
        let stage = 'load';
        try {
          await page.goto(`http://127.0.0.1:5174/tests/desktop-layout/kanban.html?theme=${theme}&count=${count}`);
          await page.locator(`${boardSelector} > div`).nth(5).waitFor({ state: 'attached' });
          await page.screenshot({ path: path.join(output, `${name}.png`), animations: 'disabled' });
          stage = 'layout';
          geometry = await measure(page);
          assert.equal(geometry.pageFits, true, 'only the board, not the page, may scroll sideways');
          assert.equal(geometry.board.overflowX, 'auto', 'preserve horizontal access to all six status columns');
          assert.ok(geometry.board.scrollWidth > geometry.board.width, 'desktop fixture must exercise board scrolling');
          for (const column of geometry.columns) assert.ok(column.scrollWidth <= column.width + 1, 'columns must not have an inner horizontal scrollbar');
          assert.equal(geometry.cards.length, count);
          for (const card of geometry.cards) {
            assert.ok(card.scrollWidth <= card.width + 1, 'card content must fit without hiding overflow');
            assert.equal(card.controlsFit, true, 'status, detail and delete controls must remain inside each card');
            assert.equal(card.textFits, true, 'Chinese and unbroken English labels must wrap inside each card');
          }
          if (count) {
            assert.ok(geometry.columns[0].scrollHeight > geometry.columns[0].height, '12 cards must exercise vertical scrolling');
            stage = 'controls-and-drag';
            await checkControls(page);
            stage = 'last-card';
            const last = page.locator(bodySelector).locator('[aria-roledescription="draggable"]').last();
            await last.getByTitle('查看详情', { exact: true }).click();
            await page.getByRole('dialog', { name: '投递详情', exact: true }).waitFor();
            await page.getByRole('dialog', { name: '投递详情', exact: true }).getByRole('button', { name: 'Close', exact: true }).click();
            await page.getByRole('dialog', { name: '投递详情', exact: true }).waitFor({ state: 'hidden' });
            assert.ok(await page.locator(bodySelector).evaluate(node => node.scrollTop > 0), 'vertical scroll must reach the last card');
            await page.screenshot({ path: path.join(output, `${name}-last-card.png`), animations: 'disabled' });
          }
          stage = 'board-scroll';
          await page.locator(boardSelector).hover({ position: { x: 20, y: 20 } });
          await page.mouse.wheel(1600, 0);
          await page.waitForFunction(selector => document.querySelector(selector).scrollLeft > 0, boardSelector);
          await page.screenshot({ path: path.join(output, `${name}-right.png`), animations: 'disabled' });
          assert.deepEqual(requests, [], 'cancelled controls and drag must not call the backend');
          results.push({ name, status: 'passed', geometry });
        } catch (error) {
          await page.screenshot({ path: path.join(output, `${name}-failed.png`), animations: 'disabled' });
          results.push({ name, status: 'failed', stage, error: String(error), geometry });
        } finally { await page.close(); }
      }
    }
  }
}
