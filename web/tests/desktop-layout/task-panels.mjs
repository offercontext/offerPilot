import assert from 'node:assert/strict';
import path from 'node:path';

async function checkOffer(page) {
  await page.locator('[data-testid="offer-negotiation-drawer"]').waitFor();
  await page.locator('#negotiation-goal').fill('合成演练：核对固定薪酬、年度奖金与长期成长空间');
  await page.locator('#negotiation-concerns').fill('尚未核对福利细节，仅保留本地未发送草稿。');
  await page.locator('#negotiation-scenario').fill('Synthetic preparation only, no AI request');
  const colors = await page.evaluate(() => {
    const owner = document.querySelector('[data-core-task-owner]');
    const drawer = document.querySelector('[data-testid="offer-negotiation-drawer"]');
    const heading = drawer.querySelector('h2');
    const guideHeading = drawer.querySelector('[data-testid="offer-negotiation-next-step"] h3');
    const inactiveStep = drawer.querySelector('li[data-active="false"]');
    const reference = document.createElement('span');
    reference.style.cssText = 'color:var(--op-ink);background:var(--op-surface);border-color:var(--surface-sunken)';
    document.body.append(reference);
    const expected = getComputedStyle(reference);
    const result = {
      heading: getComputedStyle(heading).color,
      guideHeading: getComputedStyle(guideHeading).color,
      owner: getComputedStyle(owner).backgroundColor,
      inactiveStep: getComputedStyle(inactiveStep).backgroundColor,
      ink: expected.color, surface: expected.backgroundColor, sunken: expected.borderTopColor,
      headingFits: heading.getBoundingClientRect().right <= owner.getBoundingClientRect().right,
    };
    reference.remove();
    return result;
  });
  assert.equal(colors.heading, colors.ink, 'Offer title must use the current theme ink');
  assert.equal(colors.guideHeading, colors.ink, 'Offer step heading must inherit the theme ink');
  assert.equal(colors.owner, colors.surface, 'task wrapper must use the current theme surface');
  assert.equal(colors.inactiveStep, colors.sunken, 'inactive Offer step must have a defined sunken surface');
  assert.equal(colors.headingFits, true, 'long Offer title must stay inside its task wrapper');
}

async function checkHaru(page) {
  const context = page.locator('#haru-chat-window [aria-label="当前上下文"]');
  await context.waitFor();
  const geometry = await context.evaluate(node => {
    const label = node.querySelector('span');
    const title = node.querySelector('b');
    const range = document.createRange();
    range.selectNodeContents(label);
    const labelBounds = label.getBoundingClientRect();
    const titleBounds = title.getBoundingClientRect();
    const bounds = node.getBoundingClientRect();
    return {
      labelLines: range.getClientRects().length, text: title.textContent, title: title.getAttribute('title'),
      ellipsis: getComputedStyle(title).textOverflow, titleWidth: titleBounds.width,
      clipped: title.scrollWidth > title.clientWidth,
      contained: labelBounds.left >= bounds.left && titleBounds.right <= bounds.right,
      overlap: labelBounds.right > titleBounds.left, height: bounds.height,
    };
  });
  assert.equal(geometry.labelLines, 1, 'context label must remain on one line');
  assert.equal(geometry.title, geometry.text, 'the full context and attachment count must remain available');
  assert.equal(geometry.ellipsis, 'ellipsis');
  assert.equal(geometry.clipped, true, 'fixture must exercise a genuinely truncated long context');
  assert.ok(geometry.titleWidth >= 100, 'long context must retain useful readable width');
  assert.equal(geometry.contained, true);
  assert.equal(geometry.overlap, false);
  assert.ok(geometry.height <= 44, 'context row must not grow into a vertical label');
  await page.getByRole('button', { name: '关闭 Haru 对话', exact: true }).click();
  await page.locator('#haru-chat-window').waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: '重新打开 Haru', exact: true }).click();
  await context.waitFor();
}

async function checkQuickPractice(page) {
  const panel = page.locator('[data-testid="quick-practice-panel"]');
  await panel.waitFor();
  await page.getByPlaceholder('例如：后端工程师', { exact: true }).fill('高级基础架构工程师 · SeniorDistributedInfrastructureReliabilityEngineer');
  await page.locator('#quick-readiness-jd').fill('负责跨区域协作团队的复杂平台与可靠性工程。'.repeat(6) + '\nDistributedInfrastructureWithoutWhitespace'.repeat(5));
  const checkbox = panel.getByRole('checkbox');
  await checkbox.check();
  assert.equal(await checkbox.isChecked(), true);
  await page.waitForFunction(() => {
    const inner = document.querySelector('[data-testid="quick-practice-panel"] .ant-checkbox-inner');
    return inner && getComputedStyle(inner, '::after').opacity === '1';
  });
  await panel.locator('.ant-select-selector').click();
  await page.locator('.ant-select-item-option').first().click();
  assert.equal(await panel.getByRole('button', { name: /进入快速练习/ }).isEnabled(), true);
  // Include hidden native inputs: generic field CSS previously changed their rendered bounds.
  const geometry = await panel.evaluate(node => {
    const rect = node.getBoundingClientRect();
    const side = document.querySelector('[aria-label="面试说明"]').getBoundingClientRect();
    const controls = [...node.querySelectorAll('.ant-input, .ant-select, .ant-select-selector, .ant-checkbox-wrapper, .ant-checkbox-input, .ant-checkbox-inner')];
    const overflow = controls.filter(control => {
      const box = control.getBoundingClientRect();
      return box.left < rect.left - 1 || box.right > rect.right + 1;
    }).map(control => control.className);
    const input = node.querySelector('.ant-checkbox-input').getBoundingClientRect();
    const inner = node.querySelector('.ant-checkbox-inner').getBoundingClientRect();
    const label = node.querySelector('.ant-checkbox-wrapper').getBoundingClientRect();
    const mark = getComputedStyle(node.querySelector('.ant-checkbox-inner'), '::after');
    return {
      overflow,
      overlap: rect.left < side.right && rect.right > side.left && rect.top < side.bottom && rect.bottom > side.top,
      markVisible: mark.opacity === '1' && mark.display !== 'none' && mark.content !== 'none' && parseFloat(mark.width) > 0,
      inputMatchesMark: Math.abs(input.width - inner.width) <= 2 && Math.abs(input.height - inner.height) <= 2,
      checkboxWithinLabel: inner.top >= label.top - 1 && inner.bottom <= label.bottom + 1,
      checkboxSize: { width: inner.width, height: inner.height },
    };
  });
  assert.deepEqual(geometry.overflow, [], 'quick-practice controls must stay inside the preparation panel');
  assert.equal(geometry.overlap, false, 'preparation and explanation panels must not overlap');
  assert.equal(geometry.markVisible, true, 'the checked Ant checkbox must render its mark');
  assert.equal(geometry.inputMatchesMark, true, 'hidden native checkbox must not retain generic field dimensions');
  assert.equal(geometry.checkboxWithinLabel, true);
  assert.ok(geometry.checkboxSize.width >= 14 && geometry.checkboxSize.width <= 22);
  assert.ok(geometry.checkboxSize.height >= 14 && geometry.checkboxSize.height <= 22);
}

async function checkResumes(page) {
  const cards = page.locator('.op-app-content .ant-card');
  await cards.nth(1).waitFor();
  assert.equal(await cards.count(), 2, 'base and derived resume cards required');
  const layout = await page.locator('.op-app-content').evaluate(main => {
    const bounds = main.getBoundingClientRect();
    return { width: main.clientWidth, scrollWidth: main.scrollWidth,
      cardsFit: [...main.querySelectorAll('.ant-card')].every(card => {
        const r = card.getBoundingClientRect();
        return r.left >= bounds.left && r.right <= bounds.right;
      }),
      contentFits: [...main.querySelectorAll('.ant-card, .ant-card-body')].every(card => card.scrollWidth <= card.clientWidth + 1),
      tagWraps: [...main.querySelectorAll('.ant-tag')].some(tag => tag.textContent.includes('基于') && getComputedStyle(tag).whiteSpace === 'normal'),
    };
  });
  assert.ok(layout.scrollWidth <= layout.width + 1, 'resume library must not horizontally scroll its main content');
  assert.equal(layout.cardsFit, true, 'both cards must fit the content column');
  assert.equal(layout.contentFits, true, 'long title and lineage must not overflow a card');
  assert.equal(layout.tagWraps, true, 'fixture must exercise a wrapping derived-resume lineage');
  for (const card of await cards.all()) {
    for (const button of await card.getByRole('button').all()) {
      if (await button.isDisabled()) continue;
      await button.scrollIntoViewIfNeeded();
      await button.click({ trial: true });
    }
  }
}

export async function runTaskPanelCases(browser, output, results) {
  for (const width of [900, 1008, 1280, 1440]) {
    for (const theme of ['dark', 'light']) {
      for (const surface of ['offer', 'haru', 'quick', 'resumes']) {
        for (const language of surface === 'haru' ? ['zh', 'en'] : ['zh']) {
          const name = `${width}x689-${surface}-${theme}-${language}`;
          const page = await browser.newPage({ viewport: { width, height: 689 }, colorScheme: theme });
          page.setDefaultTimeout(15_000);
          page.setDefaultNavigationTimeout(15_000);
          const unexpectedRequests = [];
          // Synthetic reads only: do not submit a draft, call AI, or create a practice case.
          await page.route('**/api/**', async route => {
            const request = route.request();
            const pathname = new URL(request.url()).pathname;
            const allowed = request.method() === 'GET' && (
              pathname === '/api/offers/7/negotiation/proposals'
              || pathname === '/api/offers/comparison-dimensions'
              || pathname === '/api/offers/7/comparison-values'
            );
            if (!allowed) unexpectedRequests.push(`${request.method()} ${pathname}`);
            await route.fulfill({ status: allowed ? 200 : 403, contentType: 'application/json', body: allowed ? '[]' : '{}' });
          });
          try {
            await page.goto(`http://127.0.0.1:5174/tests/desktop-layout/task-panels.html?surface=${surface}&theme=${theme}&language=${language}`);
            if (surface === 'offer') await checkOffer(page);
            else if (surface === 'haru') await checkHaru(page);
            else if (surface === 'quick') await checkQuickPractice(page);
            else await checkResumes(page);
            await page.locator('.op-app-content').evaluate(node => { node.scrollTop = 0; window.scrollTo(0, 0); });
            await page.screenshot({ path: path.join(output, `${name}.png`), animations: 'disabled' });
            if (surface === 'quick') {
              await page.getByRole('button', { name: /进入快速练习/ }).scrollIntoViewIfNeeded();
              await page.screenshot({ path: path.join(output, `${name}-controls.png`), animations: 'disabled' });
            }
            if (surface === 'resumes') {
              const cards = page.locator('.op-app-content .ant-card');
              for (let index = 0; index < await cards.count(); index++) {
                await cards.nth(index).getByRole('button', { name: '对比版本', exact: true }).scrollIntoViewIfNeeded();
                await page.screenshot({ path: path.join(output, `${name}-card-${index + 1}.png`), animations: 'disabled' });
              }
            }
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'page must not overflow horizontally');
            assert.deepEqual(unexpectedRequests, [], 'fixture must not call live services or mutate data');
            results.push({ name, status: 'passed' });
          } catch (error) {
            await page.screenshot({ path: path.join(output, `${name}-failed.png`), animations: 'disabled' });
            results.push({ name, status: 'failed', error: String(error) });
          } finally { await page.close(); }
        }
      }
    }
  }
}
