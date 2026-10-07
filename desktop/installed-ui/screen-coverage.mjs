import assert from 'node:assert/strict';
import { ROOTS, WIDTHS } from './coverage-model.mjs';
import { selectVisibleOption as select } from './select-option.mjs';

const root = (view) => ROOTS.find((item) => item.view === view);
const exact = (name) => ({ name, exact: true });
const btn = (scope, name) => scope.getByRole('button', exact(name));
const dialog = (page, name) => page.getByRole('dialog', exact(name));
const region = (page, name) => page.getByRole('region', exact(name));
const prefix = `QA-20261007-${process.env.GITHUB_RUN_ID || 'local'}`;
const longChinese = `${prefix}-中文超长公司名称研发创新技术研究中心与跨区域协作团队`;
const longEnglish = `${prefix}-VeryLongUnbrokenEnglishCompanyNameForInstalledWindowsLayoutRegression`;
const jd = '合成岗位原文，仅供安装界面验收。负责本地软件测试、异常处理和团队协作；不包含真实个人资料。';
const applicationData = (index) => ({
  company_name: index === 0 ? longChinese : index === 1 ? longEnglish : `${prefix}-分页公司-${String(index + 1).padStart(2, '0')}`,
  position_name: index === 0 ? '超长中文岗位名称高级软件研发工程师与跨团队质量负责人' : index === 1 ? 'SeniorSoftwareEngineerWithLongUnbrokenEnglishPositionTitle' : `合成岗位 ${index + 1}`,
  notes: `${prefix} 仅供真实安装 UI 验收，不发送 AI，不访问外部网址。`,
});
async function ready(page) {
  await page.waitForFunction(() => ![...document.querySelectorAll('.ant-spin-spinning')].some((element) => element.getClientRects().length > 0));
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function command(page, name) {
  const exit = btn(page, '退出沉浸模式，返回原页面');
  if (await exit.isVisible()) await exit.click();
  await page.getByRole('button', { name: /^快速打开/ }).click();
  const input = page.getByRole('combobox').and(page.getByPlaceholder('快速打开页面、投递或助手…', { exact: true }));
  await input.fill(name);
  const option = page.getByRole('listbox', exact('命令结果')).getByRole('option').filter({ has: page.getByText(name, { exact: true }) });
  assert.equal(await option.count(), 1, 'command must resolve unambiguously');
  await option.click();
  await input.waitFor({ state: 'hidden' });
}
async function navigate(page, view) {
  const item = root(view);
  const exit = btn(page, '退出沉浸模式，返回原页面');
  if (await exit.isVisible()) await exit.click();
  if (view === 'pilot') await command(page, '打开 Pilot 工作区');
  else {
    const back = btn(page, '返回上一层');
    if (await back.count() === 1 && await back.isVisible()) await back.click();
    await page.getByRole('navigation', exact('主导航')).getByRole('button', exact(item.module)).click();
    if (item.tab) await page.getByRole('tab', exact(item.tab)).click();
  }
  await page.waitForURL((url) => url.searchParams.get('view') === view || (view === 'dashboard' && !url.searchParams.has('view') && url.pathname === '/'));
  await ready(page);
  if (view === 'pilot') await btn(page, '退出沉浸模式，返回原页面').waitFor();
  else {
    assert.equal(await page.getByRole('navigation', exact('主导航')).getByRole('button', exact(item.module)).getAttribute('aria-current'), 'page');
    if (item.tab) assert.equal(await page.getByRole('tab', exact(item.tab)).getAttribute('aria-selected'), 'true');
  }
  const markers = {
    'applications-list': () => region(page, '投递列表'), calendar: () => region(page, '月历'),
    reminders: () => page.getByPlaceholder('搜索流程行动', { exact: true }),
    questions: () => page.getByRole('heading', exact('题库刷题')), offers: () => page.locator('[data-offer-workspace-mode]'),
    resumes: () => page.getByPlaceholder('搜索简历', { exact: true }), reviews: () => page.getByTestId('experience-materials-view'),
    knowledge: () => page.getByPlaceholder('搜索资料内容（中文/英文关键词）', { exact: true }),
    settings: () => page.getByRole('heading', { name: '设置', exact: true, level: 2 }),
    interview: () => page.getByRole('tab', exact('即将进行')),
  };
  if (markers[view]) await markers[view]().waitFor();
  if (view === 'dashboard') assert.ok(await page.getByText('从第一条投递开始建立求职节奏', { exact: true }).isVisible()
    || await region(page, '未来 7 天日程').isVisible(), 'dashboard ready state required');
  if (view === 'board') assert.ok(await page.getByText('准备投递', { exact: true }).count() > 0, 'board lane required');
}
async function theme(page, value) {
  const exit = btn(page, '退出沉浸模式，返回原页面');
  if (await exit.isVisible()) await exit.click();
  if (await page.locator('html').getAttribute('data-theme') !== value) await btn(page, '切换明暗模式').click();
  await page.waitForFunction((mode) => document.documentElement.dataset.theme === mode, value);
}
async function closeDialog(page, title) {
  const surface = dialog(page, title);
  const cancel = surface.getByRole('button', { name: /^(取消|Cancel)$/ });
  if (await cancel.count() === 1) await cancel.click();
  else await surface.getByRole('button', { name: /^(关闭|Close)$/ }).click();
  await surface.waitFor({ state: 'hidden' });
}
async function responseFromUI(page, route, method, action) {
  const origin = new URL(page.url()).origin;
  const [response] = await Promise.all([page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.origin === origin && route.test(url.pathname) && response.request().method() === method;
  }), action()]);
  assert.ok(response.status() >= 200 && response.status() < 300, 'visible UI write failed');
  return response.json(); // Caller keeps only validated synthetic IDs, never raw response.
}
async function captureWidths(qa, page, name, extra = {}) {
  for (const width of WIDTHS) {
    await qa.size(width, 689);
    await ready(page);
    await qa.capture(`${name}-${width}x689`, extra);
  }
  await qa.size(1280);
}
async function openApplication(page, record) {
  await navigate(page, 'applications-list');
  const list = region(page, '投递列表');
  await list.getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill(record.company_name);
  const row = list.locator(`tr[data-row-key="${record.id}"]`);
  await row.waitFor();
  await row.click();
  await page.getByRole('heading', { level: 3, name: `${record.company_name} · ${record.position_name}`, exact: true }).waitFor();
}
async function createApplication(page, data) {
  await command(page, '添加投递');
  const form = dialog(page, '添加投递');
  await form.getByLabel('公司', { exact: true }).fill(data.company_name);
  await form.getByLabel('岗位', { exact: true }).fill(data.position_name);
  await form.getByLabel('备注', { exact: true }).fill(data.notes);
  await btn(form, '核对并检查重复').click();
  await form.getByText('未发现符合规则的重复记录', { exact: true }).waitFor();
  const value = await responseFromUI(page, /^\/api\/applications$/, 'POST', () => btn(form, '确认保存').click());
  assert.ok(Number.isSafeInteger(value.id) && value.id > 0);
  for (const [key, expected] of Object.entries(data)) assert.equal(value[key], expected);
  await form.waitFor({ state: 'hidden' });
  await page.getByRole('heading', { level: 3, name: `${data.company_name} · ${data.position_name}`, exact: true }).waitFor();
  return { id: value.id, ...data };
}

export async function rootSweep(qa, page, state) {
  if (!qa.canProceed()) {
    for (const item of ROOTS) await qa.disposition(item.id, `${state}-${item.view}`, 'BLOCKED', 'previous-ui-write-still-pending');
    return;
  }
  for (const mode of ['dark', 'light']) {
    if (!qa.canProceed()) return;
    await theme(page, mode);
    for (const width of mode === 'dark' ? WIDTHS : [1280]) {
      if (!qa.canProceed()) return;
      await qa.size(width);
      for (const item of ROOTS) await qa.run(item.id, `${state}-${mode}-${width}-${item.view}`,
        item.view === 'pilot' ? ['快速打开', '打开 Pilot 工作区'] : [item.module, item.tab].filter(Boolean), async () => {
          await navigate(page, item.view);
          if (item.view === 'applications-list') {
            await region(page, '投递列表').getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill('');
            await ready(page);
          }
          qa.observed('visible root content and selected module/tab match navigation');
          await qa.capture(`${state}-${mode}-${width}-${item.view}`, { population: state });
          if (['dashboard', 'settings'].includes(item.view)) {
            const last = item.view === 'settings' ? page.locator('summary').filter({ hasText: '查看运行日志与诊断' }) : region(page, '本周进度');
            if (await last.isVisible()) {
              await last.scrollIntoViewIfNeeded();
              await qa.capture(`${state}-${mode}-${width}-${item.view}-lower`, { population: state });
            }
          }
        }, 'visual');
    }
  }
  if (qa.canProceed()) {
    await theme(page, 'dark');
    await qa.size(1280);
    await navigate(page, 'dashboard');
  }
}

export async function extendedFlows(qa, page, initialRecord) {
  const apps = [];
  qa.fixture('application', initialRecord.id);
  await theme(page, 'dark');
  await qa.size(1280);

  await qa.run('S01', 'command-palette', ['快速打开'], async () => {
    await navigate(page, 'dashboard');
    await page.getByRole('button', { name: /^快速打开/ }).click();
    const input = page.getByPlaceholder('快速打开页面、投递或助手…', { exact: true });
    assert.equal(await input.inputValue(), '');
    await qa.capture('command-all');
    await input.fill('QA-no-such-command');
    assert.equal(await page.getByRole('listbox', exact('命令结果')).getByRole('option').count(), 0);
    await qa.capture('command-no-match');
    await page.keyboard.press('Escape');
    await input.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: /^快速打开/ }).click();
    assert.equal(await input.inputValue(), '');
    await input.fill('打开投递列表');
    await input.press('ArrowDown');
    await input.press('ArrowUp');
    await input.press('Enter');
    await region(page, '投递列表').waitFor();
    await input.waitFor({ state: 'hidden' });
    qa.observed('no match, Escape, cleared query on reopen, keyboard navigation and Enter destination');
  });

  await qa.run('S02', 'application-validation-cancel', ['快速打开', '添加投递'], async () => {
    await navigate(page, 'dashboard');
    await command(page, '添加投递');
    const form = dialog(page, '添加投递');
    await btn(form, '核对并检查重复').click();
    await form.getByText('请输入公司名称', { exact: true }).waitFor();
    await form.getByText('请输入岗位名称', { exact: true }).waitFor();
    await qa.capture('application-required-validation');
    await form.getByLabel('公司', { exact: true }).fill(`${prefix}-CANCELLED`);
    await captureWidths(qa, page, 'application-draft');
    await btn(form, '取消').click();
    await form.waitFor({ state: 'hidden' });
    await command(page, '添加投递');
    assert.equal(await form.getByLabel('公司', { exact: true }).inputValue(), '');
    await btn(form, '取消').click();
    await navigate(page, 'applications-list');
    const list = region(page, '投递列表');
    await list.getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill(`${prefix}-CANCELLED`);
    await ready(page);
    assert.equal(await list.locator('tr[data-row-key]').count(), 0);
    qa.observed('required validation; cancel and reopen reset draft; cancelled company absent from list');
  });

  await qa.run('R05', 'long-application-create-and-pagination', ['快速打开', '添加投递', '投递', '列表'], async () => {
    await navigate(page, 'dashboard');
    for (let index = 0; index < 11; index++) {
      const saved = await createApplication(page, applicationData(index));
      apps.push(saved); qa.fixture('application', saved.id);
      if (index === 0) {
        await navigate(page, 'applications-list');
        const list = region(page, '投递列表');
        await list.getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill(saved.company_name);
        await list.locator(`tr[data-row-key="${saved.id}"]`).waitFor();
        await captureWidths(qa, page, 'list-one-long-chinese-row', { expectedFilteredRows: 1 });
        qa.observed('UI-created long Chinese application persisted with POST identity and visible row');
      }
    }
    await navigate(page, 'applications-list');
    const list = region(page, '投递列表');
    await list.getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill('');
    await ready(page);
    await captureWidths(qa, page, 'list-twelve-records', { expectedTotalRecords: 12 });
    const next = list.getByTitle('下一页', { exact: true });
    assert.ok(await next.count() === 1, 'pagination next must be available for twelve fixtures');
    const firstIds = await list.locator('tr[data-row-key]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-row-key')));
    await next.click();
    await ready(page);
    const nextIds = await list.locator('tr[data-row-key]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-row-key')));
    assert.ok(nextIds.length > 0 && nextIds.some((id) => !firstIds.includes(id)));
    await qa.capture('list-next-page');
    await list.getByTitle('上一页', { exact: true }).click();
    await ready(page);
    assert.deepEqual(await list.locator('tr[data-row-key]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-row-key'))), firstIds);
    await list.getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill(longEnglish);
    const english = list.locator(`tr[data-row-key="${apps[1].id}"]`);
    await english.waitFor();
    const box = await english.boundingBox(); assert.ok(box && box.height <= 100, 'long row must remain readable and bounded');
    await qa.capture('list-long-unbroken-english');
    await theme(page, 'light'); await qa.capture('list-long-english-light'); await theme(page, 'dark');
    qa.observed('twelve real records; next/previous page changes row IDs; English search and bounded row height');
  });

  const primary = apps[0] || initialRecord;
  await qa.run('R05', 'installed-table-horizontal-keyboard', ['投递', '列表', '横向滚动'], async () => {
    await navigate(page, 'applications-list');
    const list = region(page, '投递列表');
    await list.getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill(primary.company_name);
    await list.locator(`tr[data-row-key="${primary.id}"]`).waitFor();
    await qa.size(900, 689);
    const table = region(page, '投递表格，可横向滚动');
    await table.waitFor();
    const content = table.locator('.ant-table-content');
    const range = await content.evaluate((element) => element.scrollWidth - element.clientWidth);
    assert.ok(range > 0, 'narrow native window must expose a real horizontal table range');
    await page.getByText('左右滚动查看其余列；聚焦表格后也可按 ← →', { exact: true }).waitFor();
    await table.focus();
    for (let i = 0; i < 8; i++) await table.press('ArrowLeft');
    assert.equal(await content.evaluate((element) => element.scrollLeft), 0);
    for (let i = 0; i < 8; i++) await table.press('ArrowRight');
    assert.ok(await content.evaluate((element) => element.scrollLeft) >= range - 1);
    await qa.capture('installed-list-scrolled-right-keyboard');
    const pilot = list.locator(`tr[data-row-key="${primary.id}"]`).getByRole('button', exact('问 Pilot'));
    await pilot.click({ trial: true });
    for (let i = 0; i < 8; i++) await table.press('ArrowLeft');
    assert.equal(await content.evaluate((element) => element.scrollLeft), 0);
    await qa.capture('installed-list-scrolled-left-keyboard');
    await qa.size(1280);
    qa.observed('real keyboard horizontal scroll reaches both ends; scroll hint visible; right-hand Pilot target actionable');
  });
  await applicationDetailFlows(qa, page, primary);
  await questionFlows(qa, page);
  await resumeFlows(qa, page);
  await interviewFlows(qa, page);
  await knowledgeFlows(qa, page);
  await storyFlows(qa, page);
  await offerFlows(qa, page, apps.length >= 2 ? apps.slice(0, 2) : [initialRecord]);
  await pilotSettingsFlows(qa, page, primary);
  await rootSweep(qa, page, 'populated-to-supported-extent');
  await qa.disposition('S10', 'ai-interview-studio', 'BLOCKED', 'real session, generated questions and feedback require unapproved AI; no hidden-state injection', ['面试', '面试练习']);
  await qa.disposition('S28', 'backup-download-restore', 'BLOCKED', 'desktop download policy remains intact; no backup restore or user-data deletion attempted', ['设置', '数据与备份']);
  await qa.disposition('S30', 'live-voice-and-model-download', 'BLOCKED', 'microphone and model download not authorized; visible settings only', ['设置', '语音']);
  await qa.disposition('S31', 'raw-diagnostic-log-content', 'NOT RUN', 'no raw logs exported or captured; bounded runtime classifications are recorded separately', ['设置', '查看运行日志与诊断']);
  await qa.disposition('N01', 'help-and-knowledge-brief', 'N/A', 'no dedicated Help page or enabled Knowledge Brief in this build');
  await qa.run('RUNTIME', 'runtime-health', ['renderer diagnostics'], async () => {
    const runtime = qa.runtimeSnapshot();
    assert.equal(runtime.classifications?.['unexpected-page-error'] || 0, 0, 'unexpected renderer page error');
    assert.equal(runtime.ownCriticalFailureCount, 0, 'own-origin server/resource/transport failure, including events beyond evidence cap');
    await qa.capture('runtime-health-final-screen');
    if (Object.values(runtime.classifications).some((count) => count > 0)) qa.blocked('CSP-Haru-graphics-or-console-error-observed-see-safe-classifications');
    qa.observed('no unexpected renderer page errors or own-origin server/asset failures recorded');
  });
  await qa.finish();
  if (qa.canProceed()) await navigate(page, 'applications-list');
}

async function applicationDetailFlows(qa, page, record) {
  await qa.run('S03', 'application-detail-tabs-back', ['投递', '列表', '投递详情'], async () => {
    await openApplication(page, record);
    const tabs = page.getByRole('tablist', exact('投递详情分段'));
    for (const name of ['概览', '准备', '进展']) {
      await tabs.getByRole('tab', exact(name)).click();
      const panel = page.getByRole('tabpanel', exact(name));
      await panel.waitFor();
      assert.equal(await tabs.getByRole('tab', exact(name)).getAttribute('aria-selected'), 'true');
      if (name === '概览') await panel.getByText(record.notes, { exact: true }).waitFor();
      if (name === '准备') await captureWidths(qa, page, 'application-preparation');
      else await qa.capture(`application-${name === '概览' ? 'overview' : 'progress'}`);
    }
    await theme(page, 'light');
    await tabs.getByRole('tab', exact('准备')).click();
    await qa.capture('application-preparation-light');
    await theme(page, 'dark');
    await tabs.getByRole('tab', exact('概览')).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await tabs.getByRole('tab', exact('准备')).getAttribute('aria-selected'), 'true');
    await btn(page, '返回上一层').click();
    await region(page, '投递列表').locator(`tr[data-row-key="${record.id}"]`).waitFor();
    qa.observed('three real detail panels; notes readback; keyboard tab navigation; Back restores searched record');
  });
  await qa.run('S04', 'application-more-menu', ['投递详情', '更多操作'], async () => {
    await openApplication(page, record);
    await page.getByTestId('application-more-actions').click();
    await page.getByRole('menuitem', exact('安排日程')).waitFor();
    await qa.capture('application-more-menu');
    await page.keyboard.press('Escape');
    await page.getByRole('menuitem', exact('安排日程')).waitFor({ state: 'hidden' });
    await page.getByTestId('application-more-actions').click();
    assert.equal(await page.getByRole('menuitem', exact('安排日程')).count(), 1);
    await page.keyboard.press('Escape');
    qa.observed('menu exposes supported actions; Escape and reopen do not duplicate overlay');
  });
  await qa.run('S05', 'jd-save-history', ['投递详情', '更多操作', '岗位资料'], async () => {
    await openApplication(page, record);
    const tabs = page.getByRole('tablist', exact('投递详情分段'));
    await tabs.getByRole('tab', exact('准备')).click();
    for (let version = 1; version <= 2; version++) {
      await page.getByTestId('application-more-actions').click();
      await page.getByRole('menuitem', { name: /^(添加|编辑)岗位资料$/ }).click();
      const form = dialog(page, '投递岗位资料');
      await form.getByPlaceholder('粘贴岗位描述', { exact: true }).fill(`${jd}\n版本 ${version}`);
      await form.getByPlaceholder('来源 URL（仅展示，不会访问）', { exact: true }).fill('https://example.invalid/qa-local-only');
      await qa.capture(`jd-editor-v${version}`);
      await responseFromUI(page, new RegExp(`^/api/applications/${record.id}/job-description/versions$`), 'POST', () => btn(form, '保存岗位资料').click());
      await form.waitFor({ state: 'hidden' });
      await page.getByText(`${jd}\n版本 ${version}`, { exact: true }).waitFor();
    }
    await btn(page, '查看历史').click();
    await dialog(page, '岗位资料历史').waitFor();
    await qa.capture('jd-history-two-versions');
    await closeDialog(page, '岗位资料历史');
    qa.observed('two UI-created JD versions; current text readback; visible history; no source URL visit');
  });
  await qa.run('S07', 'application-material-entry', ['投递详情', '准备', '继续准备'], async () => {
    await openApplication(page, record);
    await page.getByRole('tablist', exact('投递详情分段')).getByRole('tab', exact('准备')).click();
    const entry = page.locator('section[aria-labelledby="application-linked-materials-heading"]').getByRole('button', { name: /继续准备|查看投递材料/ });
    await entry.click();
    await region(page, '投递准备').waitFor();
    await ready(page);
    await qa.capture('application-material-entry');
    assert.ok(await page.getByText(record.company_name, { exact: true }).count() > 0);
    qa.observed('application-bound material surface opens with synthetic owner; no AI action');
    await navigate(page, 'applications-list');
  });
  let event;
  await qa.run('S06', 'schedule-create-edit-cancel', ['投递详情', '准备', '安排日程'], async () => {
    await openApplication(page, record);
    await page.getByRole('tablist', exact('投递详情分段')).getByRole('tab', exact('准备')).click();
    await page.getByTestId('application-schedule-create').click();
    let form = page.getByTestId('schedule-event-form');
    await form.waitFor();
    await qa.capture('schedule-empty-draft');
    await form.getByLabel('地点', { exact: true }).fill(`${prefix}-合成会议室`);
    await form.getByLabel('备注', { exact: true }).fill(`${prefix}-面试准备事项`);
    await captureWidths(qa, page, 'schedule-filled');
    event = await responseFromUI(page, /^\/api\/application-events$/, 'POST', () => btn(form, '创建').click());
    assert.equal(event.application_id, record.id);
    assert.equal(event.event_type, 'interview');
    qa.fixture('application-event', event.id);
    await form.waitFor({ state: 'hidden' });
    await navigate(page, 'calendar');
    await page.locator(`[data-calendar-event="${event.id}"]`).click();
    await page.locator(`[data-selected-event="${event.id}"]`).waitFor();
    await captureWidths(qa, page, 'calendar-selected-event');
    const detail = region(page, '日期详情');
    await detail.getByText(`${prefix}-面试准备事项`, { exact: true }).waitFor();
    await btn(detail, '调整时间').click();
    form = page.getByTestId('schedule-event-form');
    await form.getByLabel('备注', { exact: true }).fill(`${prefix}-编辑取消不保存`);
    await qa.capture('schedule-edited-before-cancel');
    await btn(form, '取消').click();
    await form.waitFor({ state: 'hidden' });
    await page.locator(`[data-calendar-event="${event.id}"]`).click();
    await region(page, '日期详情').getByText(`${prefix}-面试准备事项`, { exact: true }).waitFor();
    await btn(region(page, '日期详情'), '调整时间').click();
    form = page.getByTestId('schedule-event-form');
    assert.equal(await form.getByLabel('备注', { exact: true }).inputValue(), `${prefix}-面试准备事项`);
    await form.getByLabel('备注', { exact: true }).fill(`${prefix}-面试准备事项已编辑`);
    await responseFromUI(page, new RegExp(`^/api/application-events/${event.id}$`), 'PUT', () => btn(form, '保存').click());
    await form.waitFor({ state: 'hidden' });
    await page.locator(`[data-calendar-event="${event.id}"]`).click();
    await region(page, '日期详情').getByText(`${prefix}-面试准备事项已编辑`, { exact: true }).waitFor();
    await qa.capture('schedule-saved-readback');
    qa.observed('event POST owner/type verified; calendar selects same event; cancel preserves value; edit/save readback');
  });
  await qa.run('R03', 'calendar-month-and-day-navigation', ['今日', '日历'], async () => {
    await navigate(page, 'calendar');
    const month = region(page, '月历').getByRole('heading', { level: 2 });
    const old = await month.innerText();
    await btn(page, '下一个月').click(); assert.notEqual(await month.innerText(), old);
    await btn(page, '上一个月').click(); assert.equal(await month.innerText(), old);
    await btn(page, '今天').click();
    await qa.capture('calendar-month-navigation');
    qa.observed('next month and previous month change and restore displayed month; Today action');
  });
  if (event) await qa.run('S08', 'locked-interview-readiness', ['面试', '即将进行', '准备'], async () => {
    await navigate(page, 'interview');
    const card = page.getByTestId(`interview-event-card-${event.id}`);
    await card.waitFor();
    assert.equal(await card.getAttribute('data-interview-card-bucket'), 'upcoming');
    const prepare = card.getByRole('button', { name: /准备/ });
    assert.equal(await prepare.count(), 1);
    await prepare.click();
    const readiness = page.getByTestId('interview-readiness-center');
    await readiness.waitFor();
    assert.equal(await readiness.getAttribute('data-readiness-mode'), 'real');
    await readiness.getByRole('heading', exact(`${record.company_name} · ${record.position_name}`)).waitFor();
    await qa.capture('locked-interview-readiness');
    qa.observed('actual event card opens readiness surface; live model start not invoked');
  });
  await qa.run('S11', 'completed-interview-manual-review', ['投递详情', '安排日程', '选择面试并开始复盘'], async () => {
    await openApplication(page, record);
    await page.getByRole('tablist', exact('投递详情分段')).getByRole('tab', exact('准备')).click();
    await page.getByTestId('application-schedule-create').click();
    const schedule = page.getByTestId('schedule-event-form');
    const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
    const date = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, '0')}-${String(yesterday.getDate()).padStart(2, '0')}`;
    await schedule.getByLabel('时间', { exact: true }).fill(`${date} 09:00`);
    await schedule.getByLabel('时间', { exact: true }).press('Enter');
    await select(page, schedule.getByLabel('状态', { exact: true }), '已完成');
    await schedule.getByLabel('备注', { exact: true }).fill(`${prefix}-已完成合成面试`);
    const completed = await responseFromUI(page, /^\/api\/application-events$/, 'POST', () => btn(schedule, '创建').click());
    assert.equal(completed.status, 'done'); assert.equal(completed.application_id, record.id);
    assert.ok(new Date(completed.scheduled_at).getTime() < Date.now());
    qa.fixture('application-event', completed.id);
    await schedule.waitFor({ state: 'hidden' }); await ready(page);
    await btn(page, '选择面试并开始复盘').click();
    const choices = dialog(page, '选择要复盘的面试').locator('button.ant-btn');
    assert.equal(await choices.count(), 1, 'only the real completed synthetic event may be reviewed');
    await choices.click();
    const form = region(page, '新建面试复盘'); await form.waitFor();
    await form.getByLabel('面试问题', { exact: true }).fill(`${prefix} 如何确认保存没有丢失？`);
    await form.getByLabel('自我反思', { exact: true }).fill('先核对记录身份，再关闭并重新打开检查。');
    await form.getByLabel('难点/薄弱点', { exact: true }).fill('需要覆盖取消和返回路径。');
    await captureWidths(qa, page, 'interview-manual-review');
    const note = await responseFromUI(page, new RegExp(`^/api/applications/${record.id}/notes$`), 'POST', () => btn(form, '保存复盘').click());
    assert.equal(note.application_event_id, completed.id); qa.fixture('interview-note', note.id);
    await form.waitFor({ state: 'hidden' });
    await openApplication(page, record);
    await page.getByRole('tablist', exact('投递详情分段')).getByRole('tab', exact('准备')).click();
    await page.getByText(`问题：${prefix} 如何确认保存没有丢失？`, { exact: true }).waitFor();
    await qa.capture('interview-manual-review-readback');
    qa.observed('completed event created through actual date/status controls; manual review bound to event; saved text readback');
  });
  await qa.disposition('S11', 'interview-review-ai-followups', 'BLOCKED', 'AI proposals, generated source capture and generated practice require separate authorization');
}

async function questionFlows(qa, page) {
  await qa.run('S13', 'question-manual-save-edit', ['面试', '刷题', '手动添加'], async () => {
    await navigate(page, 'questions');
    const bank = region(page, '题库模式');
    await btn(bank, '手动添加').click();
    let form = dialog(page, '手动添加题目');
    await btn(form, '保存').click();
    await form.getByText('请输入题目', { exact: true }).waitFor();
    await qa.capture('question-required-validation');
    await form.getByLabel('题目', { exact: true }).fill(`${prefix} 如何确认本地数据保存成功？`);
    await form.getByLabel('分类', { exact: true }).fill('安装界面验收');
    await form.getByLabel('参考答案', { exact: true }).fill('通过界面保存，返回并重新打开，检查内容和所属记录。');
    await captureWidths(qa, page, 'question-filled');
    const question = await responseFromUI(page, /^\/api\/questions$/, 'POST', () => btn(form, '保存').click());
    qa.fixture('question', question.id);
    await form.waitFor({ state: 'hidden' });
    await bank.getByPlaceholder('搜索题目 / 分类 / 标签', { exact: true }).fill(prefix);
    await bank.getByText(`${prefix} 如何确认本地数据保存成功？`, { exact: true }).waitFor();
    await btn(bank, '编辑题目').click();
    form = dialog(page, '编辑题目');
    assert.equal(await form.getByLabel('分类', { exact: true }).inputValue(), '安装界面验收');
    await qa.capture('question-edit-readback');
    await btn(form, '取消').click();
    await bank.getByPlaceholder('搜索题目 / 分类 / 标签', { exact: true }).fill('QA-no-matching-question');
    await ready(page);
    assert.equal(await btn(bank, '编辑题目').count(), 0);
    await bank.getByPlaceholder('搜索题目 / 分类 / 标签', { exact: true }).fill('');
    qa.observed('required validation; real manual question create; edit readback; cancel and empty search');
  });
  await qa.run('R07', 'question-review-and-ai-guard', ['面试', '刷题'], async () => {
    await navigate(page, 'questions');
    await page.getByRole('radio', exact('今日复习')).check();
    await region(page, '今日复习模式').waitFor();
    await qa.capture('questions-today-review');
    const reveal = region(page, '今日复习模式').getByRole('button', { name: /^显示答案/ });
    if (await reveal.isVisible()) { await reveal.click(); await qa.capture('questions-answer-revealed'); }
    await page.getByRole('radio', exact('题库')).check();
    await btn(region(page, '题库模式'), 'AI 生成题目').click();
    await region(page, 'AI 生成题目').waitFor();
    await qa.capture('questions-ai-preflight-only');
    qa.observed('review mode and AI input surface reachable; no generation or rating submitted');
  });
  await qa.disposition('S13', 'question-ai-generation', 'BLOCKED', 'unapproved provider invocation deliberately not performed');
}

async function resumeFlows(qa, page) {
  const title = `${prefix}-中文 English 合成简历`;
  let saved;
  await qa.run('S14', 'resume-upload-cancel', ['素材库', '简历', '上传现有简历'], async () => {
    await navigate(page, 'resumes');
    await page.locator('[aria-label="创建基础简历入口"]').getByRole('button', exact('上传现有简历')).click();
    const form = dialog(page, '上传简历');
    assert.equal(await btn(form, '上传').isDisabled(), true);
    await captureWidths(qa, page, 'resume-upload-empty');
    await btn(form, '取消').click();
    await form.waitFor({ state: 'hidden' });
    qa.observed('real PDF picker surface; upload disabled without file; cancel returns to library');
  });
  await qa.run('S15', 'resume-editor-create-save-reopen', ['素材库', '简历', '和 Haru 创建初稿'], async () => {
    await navigate(page, 'resumes');
    // Source creates BLANK_RESUME_CONTENT locally; no model call behind this button.
    saved = await responseFromUI(page, /^\/api\/resumes$/, 'POST', () => page.locator('[aria-label="创建基础简历入口"]').getByRole('button', exact('和 Haru 创建初稿')).click());
    qa.fixture('resume', saved.id);
    const editor = region(page, '编辑简历');
    await editor.waitFor();
    await editor.getByPlaceholder('简历标题', { exact: true }).fill(title);
    const sections = editor.getByRole('navigation', exact('简历章节'));
    for (const [index, name] of ['求职意向', '基本信息', '教育经历', '工作经历', '项目经历', '技能', '其他'].entries()) {
      await btn(sections, name).click();
      if (name === '求职意向') {
        // Current product labels are adjacent, without htmlFor. Use the real label's own container.
        await editor.locator('label').filter({ hasText: /^目标岗位$/ }).locator('..').locator('input').fill('软件测试工程师');
      }
      if (name === '基本信息') await editor.locator('label').filter({ hasText: /^姓名$/ }).locator('..').locator('input').fill('合成候选人 QA');
      if (name === '技能') await editor.getByPlaceholder('每行一个技能', { exact: true }).fill('本地自动化\n界面验收');
      await qa.capture(`resume-section-${index + 1}`);
    }
    await captureWidths(qa, page, 'resume-editor');
    await theme(page, 'light'); await qa.capture('resume-editor-light'); await theme(page, 'dark');
    await btn(editor, '高级 JSON').click();
    const advanced = region(page, '高级 JSON 编辑').getByRole('textbox');
    const original = await advanced.inputValue();
    await advanced.fill('{ invalid synthetic json');
    await btn(editor, '高级 JSON').click();
    assert.equal(await advanced.isVisible(), true, 'invalid JSON must not exit into structured editor');
    await qa.capture('resume-invalid-json-guard');
    await advanced.fill(original);
    await btn(editor, '高级 JSON').click();
    await region(page, '高级 JSON 编辑').waitFor({ state: 'hidden' });
    await responseFromUI(page, new RegExp(`^/api/resumes/${saved.id}$`), 'PATCH', () => btn(editor, '保存').click());
    await editor.waitFor({ state: 'hidden' });
    const card = page.locator('.ant-card').filter({ has: page.getByText(title, { exact: true }) });
    assert.equal(await card.count(), 1);
    await btn(card, '编辑').click();
    assert.equal(await editor.getByPlaceholder('简历标题', { exact: true }).inputValue(), title);
    await btn(sections, '基本信息').click();
    assert.equal(await editor.locator('label').filter({ hasText: /^姓名$/ }).locator('..').locator('input').inputValue(), '合成候选人 QA');
    await editor.getByPlaceholder('简历标题', { exact: true }).fill(`${title}-未保存`);
    await btn(editor, '取消').click();
    const guard = dialog(page, '有未保存的更改');
    await guard.waitFor();
    await qa.capture('resume-dirty-close-guard');
    await btn(guard, '继续编辑').click();
    assert.equal(await editor.isVisible(), true);
    await editor.getByPlaceholder('简历标题', { exact: true }).fill(title);
    await btn(editor, '取消').click();
    await editor.waitFor({ state: 'hidden' });
    qa.observed('seven structured sections; invalid JSON stays in editor; save and reopen readback; dirty-close keep editing');
  });
  if (saved) await qa.run('S16', 'resume-copy-compare', ['简历库', '复制', '对比版本'], async () => {
    await navigate(page, 'resumes');
    const card = page.locator('.ant-card').filter({ has: page.getByText(title, { exact: true }) });
    const copy = await responseFromUI(page, new RegExp(`^/api/resumes/${saved.id}/copy$`), 'POST', () => btn(card, '复制').click());
    qa.fixture('resume', copy.id);
    const editor = region(page, '编辑简历');
    await editor.waitFor();
    await editor.getByPlaceholder('简历标题', { exact: true }).fill(`${title}-对比版`);
    await btn(editor.getByRole('navigation', exact('简历章节')), '技能').click();
    await editor.getByPlaceholder('每行一个技能', { exact: true }).fill('本地自动化\n界面验收\n对比版本新增技能');
    await responseFromUI(page, new RegExp(`^/api/resumes/${copy.id}$`), 'PATCH', () => btn(editor, '保存').click());
    await editor.waitFor({ state: 'hidden' });
    const copiedCard = page.locator('.ant-card').filter({ has: page.getByText(`${title}-对比版`, { exact: true }) });
    await btn(copiedCard, '对比版本').click();
    const compare = dialog(page, '简历版本对比');
    await compare.getByRole('combobox', exact('基准版本')).selectOption(String(saved.id));
    await compare.locator('[aria-label="差异摘要"]').waitFor();
    await captureWidths(qa, page, 'resume-comparison');
    await btn(compare, '关闭版本对比').click();
    await compare.waitFor({ state: 'hidden' });
    await page.getByPlaceholder('搜索简历', { exact: true }).fill('QA-no-match-resume');
    await page.getByText('没有匹配的简历', { exact: true }).waitFor();
    await qa.capture('resume-search-no-match');
    await page.getByPlaceholder('搜索简历', { exact: true }).fill('');
    qa.observed('real copied version; edited and saved skill; explicit baseline selected; compare close and search reset');
  });
}

async function interviewFlows(qa, page) {
  await qa.run('R06', 'interview-nested-tabs', ['面试'], async () => {
    await navigate(page, 'interview');
    for (const [index, label] of ['即将进行', '已完成', '面试练习'].entries()) {
      const tab = page.getByRole('tab', exact(label));
      await tab.click(); assert.equal(await tab.getAttribute('aria-selected'), 'true');
      await qa.capture(`interview-tab-${index + 1}`);
    }
    qa.observed('all three nested interview tabs select and render');
  });
  await qa.run('S09', 'quick-and-review-practice-readiness', ['面试', '面试练习', '开始面试练习'], async () => {
    await navigate(page, 'interview');
    await page.getByRole('tab', exact('面试练习')).click();
    await btn(page, '开始面试练习').click();
    const surface = page.getByTestId('interview-practice-surface');
    await surface.waitFor();
    const quick = page.getByTestId('interview-readiness-center');
    assert.equal(await quick.getByRole('button', { name: /^进入快速练习/ }).isDisabled(), true);
    await qa.capture('quick-practice-missing-prerequisites');
    await quick.getByPlaceholder('例如：后端工程师', { exact: true }).fill(`${prefix} 合成测试工程师`);
    await quick.getByPlaceholder('粘贴你已核对的岗位描述原文，不抓取 URL。', { exact: true }).fill(jd);
    await quick.getByRole('checkbox', exact('已核对，本次按此岗位资料练习')).check();
    if (qa.report.fixtures.some((item) => item.kind === 'resume')) {
      const escapedTitle = `${prefix}-中文 English 合成简历`;
      await select(page, quick.locator('#quick-readiness-resume'), new RegExp(`^${escapedTitle} · `));
      assert.equal(await quick.getByRole('button', { name: /^进入快速练习/ }).isDisabled(), false);
      qa.observed('explicit saved resume chosen; filled quick-practice start becomes enabled without starting');
    }
    await captureWidths(qa, page, 'quick-practice-filled-preflight');
    await surface.getByRole('radio', exact('复盘重点练习')).check();
    await qa.capture('review-focused-practice-prerequisites');
    await surface.getByRole('radio', exact('快速模拟')).check();
    qa.observed('missing prerequisite disables start; synthetic position/JD draft; review-focused selector and return; no AI start');
  });
  await qa.run('S12', 'voice-growth-empty-return', ['面试', '已完成', '表达成长'], async () => {
    await navigate(page, 'interview');
    await page.getByRole('tab', exact('已完成')).click();
    await btn(page, '表达成长').click();
    await btn(page, '返回面试').waitFor();
    await qa.capture('voice-growth-empty');
    await btn(page, '返回面试').click();
    await page.getByRole('tab', exact('即将进行')).waitFor();
    qa.observed('growth surface opens and returns to interview; no microphone or generated metrics');
  });
}

async function knowledgeFlows(qa, page) {
  for (const [label, title, key] of [['上传 Markdown / Text', '上传 Markdown / Text 资料', 'text'], ['上传图文资料', '上传图文资料', 'bundle'], ['粘贴正文', '粘贴正文', 'paste']]) {
    await qa.run('S18', `knowledge-${key}-cancel`, ['素材库', '参考资料', label], async () => {
      await navigate(page, 'knowledge'); await btn(page, label).click();
      await dialog(page, title).waitFor();
      await captureWidths(qa, page, `knowledge-${key}-input`);
      await closeDialog(page, title);
      await page.getByPlaceholder('搜索资料内容（中文/英文关键词）', { exact: true }).waitFor();
      qa.observed('real input dialog opens at all native widths; cancel returns to sources');
    });
  }
  await qa.run('S19', 'knowledge-local-text-detail', ['参考资料', '粘贴正文'], async () => {
    await navigate(page, 'knowledge'); await btn(page, '粘贴正文').click();
    const form = dialog(page, '粘贴正文');
    await btn(form, '开始导入').click();
    await page.getByText('请粘贴正文内容', { exact: true }).waitFor();
    await qa.capture('knowledge-paste-validation');
    await form.getByPlaceholder('在此粘贴 Markdown 正文（系统会作为虚拟 main.md 进入同一 Pipeline）', { exact: true }).fill(`# ${prefix} 本地验收资料\n\n## 保存检查\n\n通过界面创建合成记录，返回列表并重新打开，核对内容和归属。\n\n## 边界\n\n这些资料完全虚构，不调用模型或外部网址。`);
    await form.getByPlaceholder('可选：展示标题（不填则用首个 # 标题或首段内容）', { exact: true }).fill(`${prefix} 本地验收资料`);
    await form.getByPlaceholder('可选：来源 URL（仅作为 provenance 保存，系统不会发起网络请求）', { exact: true }).fill('https://example.invalid/qa-knowledge');
    // api.py constructs ExtractionWorker/KnowledgeJobRunner without the Brief callback.
    const value = await responseFromUI(page, /^\/api\/knowledge\/sources$/, 'POST', () => btn(form, '开始导入').click());
    qa.fixture('knowledge-source', value.source.id);
    assert.equal(value.source.brief_status, 'not_started');
    await form.waitFor({ state: 'hidden' });
    for (const [index, name] of ['处理记录', '来源依据', '资料正文', '处理状态'].entries()) {
      const tab = page.getByRole('tab', { name: name === '来源依据' ? /^来源依据(?: \(\d+\))?$/ : name, exact: name !== '来源依据' });
      await tab.click(); await ready(page);
      assert.equal(await tab.getAttribute('aria-selected'), 'true');
      if (name === '资料正文') await page.locator('.knowledge-original-markdown').getByText('这些资料完全虚构，不调用模型或外部网址。', { exact: true }).waitFor();
      await qa.capture(`knowledge-detail-tab-${index + 1}`);
    }
    await btn(page, '编辑标题').click();
    const edit = dialog(page, '编辑展示标题');
    await qa.capture('knowledge-edit-title-cancel');
    await closeDialog(page, '编辑展示标题');
    await btn(page, '永久删除该资料').click();
    await dialog(page, '永久删除该资料').waitFor();
    await qa.capture('knowledge-delete-confirmation-cancel-only');
    await closeDialog(page, '永久删除该资料');
    qa.observed('empty validation; local text import keeps Brief not started; four detail tabs; title/delete dialogs cancelled');
  });
}

async function storyFlows(qa, page) {
  await qa.run('S17', 'manual-story-save-and-reopen', ['素材库', '经历素材', '新建故事'], async () => {
    await navigate(page, 'reviews'); await btn(page, '新建故事').click();
    const form = dialog(page, '整理面试故事');
    await form.waitFor(); await qa.capture('story-empty-source-picker');
    const text = `${prefix} 合成故事：我在虚构项目中检查本地保存路径，发现界面遗漏并补齐测试。`;
    await form.getByRole('textbox', exact('用户明确原始陈述')).fill(text);
    await btn(form, '加入').click();
    await btn(page.locator('[aria-label="整理故事操作"]'), '手动编写并保存').click();
    await form.getByRole('textbox', exact('手动故事标题')).fill(`${prefix} 本地验收经历`);
    await form.getByRole('textbox', exact('手动故事情境')).fill(text);
    await form.getByRole('textbox', exact('手动故事行动')).fill(text);
    const evidenceInputs = form.locator('select[aria-label^="手动证据："]');
    for (const input of await evidenceInputs.all()) {
      const options = await input.locator('option').evaluateAll((items) => items.map((item) => item.value).filter(Boolean));
      assert.equal(options.length, 1, 'only the explicit synthetic assertion can be bound');
      await input.selectOption(options[0]);
    }
    await captureWidths(qa, page, 'story-manual-draft');
    const story = await responseFromUI(page, /^\/api\/interview-stories$/, 'POST', () => btn(form, '确认手动保存故事版本').click());
    qa.fixture('interview-story', story.id);
    await form.waitFor({ state: 'hidden' });
    await page.getByRole('textbox', exact('搜索面试故事')).fill(`${prefix} 本地验收经历`);
    await btn(page, '查看版本').click();
    await region(page, '故事版本历史').waitFor();
    await qa.capture('story-version-readback');
    await btn(page, '关闭历史').click();
    qa.observed('explicit synthetic assertion; manual authoring with each evidence binding; real save; version-history readback');
  });
}

async function offerFlows(qa, page, applications) {
  const offers = [];
  await qa.run('S20', 'offer-forms-create-readback', ['Offer', '录入 Offer'], async () => {
    assert.equal(applications.length, 2, 'two UI-created synthetic applications are required');
    await navigate(page, 'offers');
    for (const [index, application] of applications.entries()) {
      const name = index === 0 ? '录入第一份 Offer' : '录入另一份 Offer';
      await btn(page, name).click();
      const form = dialog(page, '录入 Offer');
      const save = form.getByRole('button', { name: /^(确定|OK)$/ });
      await save.click();
      await form.getByText('请选择所属投递', { exact: true }).waitFor();
      await qa.capture(`offer-${index + 1}-validation`);
      await select(page, form.getByLabel('关联投递', { exact: true }), `#${application.id} ${application.company_name} - ${application.position_name}`);
      assert.equal(await form.getByLabel('公司', { exact: true }).inputValue(), application.company_name);
      await form.getByLabel('月薪（元）', { exact: true }).fill(index === 0 ? '20000' : '22000');
      await form.getByLabel('薪数（如 12/13/16）', { exact: true }).fill(index === 0 ? '13' : '12');
      await form.getByLabel('签字费（元）', { exact: true }).fill(index === 0 ? '10000' : '0');
      await form.getByLabel('备注', { exact: true }).fill(`${prefix} 合成薪酬数据，不是实际 Offer。`);
      await captureWidths(qa, page, `offer-${index + 1}-filled`);
      const value = await responseFromUI(page, /^\/api\/offers$/, 'POST', () => save.click());
      assert.equal(value.application_id, application.id);
      assert.equal(value.base_monthly, index === 0 ? 20000 : 22000);
      offers.push({ id: value.id, application }); qa.fixture('offer', value.id);
      await form.waitFor({ state: 'hidden' });
      await ready(page);
      await qa.capture(`offer-${index + 1}-saved`);
    }
    const comparison = page.getByRole('button', { name: /^开始比较/ });
    assert.equal(await comparison.isDisabled(), true);
    qa.observed('two Offers created by UI with exact application binding and salary input; compare disabled before selection');
  });
  if (offers.length !== 2) {
    for (const id of ['S21', 'S22', 'S23']) await qa.disposition(id, `${id}-missing-offers`, 'BLOCKED', 'two real UI-created offers were not established');
    return;
  }
  const openCompare = async () => {
    const back = btn(page, '返回 Offer 中心');
    if (await back.isVisible()) await back.click();
    await navigate(page, 'offers');
    for (const { application } of offers) await page.getByRole('checkbox', exact(`选择 Offer：${application.company_name}｜${application.position_name}`)).check();
    await page.getByRole('button', { name: /^开始比较/ }).click();
    await region(page, 'Offer 横向对比').waitFor();
  };
  await qa.run('S21', 'offer-comparison-math-and-differences', ['Offer', '选择两份', '开始比较'], async () => {
    await openCompare();
    const comparison = region(page, 'Offer 横向对比');
    await ready(page);
    assert.deepEqual(await comparison.locator('tr[data-field="annual"] td').allTextContents(), ['26.0 万元', '26.4 万元']);
    assert.deepEqual(await comparison.locator('tr[data-field="first-year"] td').allTextContents(), ['27.0 万元', '26.4 万元']);
    assert.ok(await comparison.locator('[data-missing="true"]').count() > 0, 'missing optional facts remain marked missing');
    await captureWidths(qa, page, 'offer-comparison-full');
    await theme(page, 'light'); await qa.capture('offer-comparison-light'); await theme(page, 'dark');
    await comparison.getByRole('switch', exact('只看差异')).click();
    assert.equal(await comparison.getByRole('switch', exact('只看差异')).getAttribute('aria-checked'), 'true');
    await qa.capture('offer-comparison-differences');
    await btn(comparison, '返回 Offer 中心').click();
    await page.locator('[data-offer-workspace-mode="selection"]').waitFor();
    qa.observed('annual 260000/264000 and first-year 270000/264000; unknown facts stay missing; differences toggle and return');
  });
  await qa.run('S22', 'offer-comparison-fields', ['Offer', '开始比较', '调整对比项'], async () => {
    await openCompare();
    await btn(region(page, 'Offer 横向对比'), '调整对比项').click();
    const settings = dialog(page, '调整对比项');
    await settings.waitFor(); await ready(page);
    const check = settings.getByRole('checkbox', exact('期权'));
    await check.uncheck(); assert.equal(await check.isChecked(), false);
    await qa.capture('offer-comparison-fields-hidden');
    await btn(settings, '恢复全部明细').click(); assert.equal(await check.isChecked(), true);
    await region(page, '自定义比较维度').waitFor();
    await qa.capture('offer-custom-dimensions');
    await closeDialog(page, '调整对比项');
    qa.observed('display field toggle and restore; custom-dimension controls visible; no invented persisted dimension');
  });
  await qa.run('S23', 'offer-negotiation-preflight-only', ['Offer', '准备谈薪'], async () => {
    await openCompare();
    await page.getByTestId(`offer-comparison-header-${offers[0].id}`).getByRole('button', exact('准备谈薪')).click();
    const form = region(page, '谈薪准备');
    await form.waitFor();
    await form.getByLabel('本次沟通目标', { exact: true }).fill('合成演练：确认薪酬构成');
    await form.getByLabel('本次顾虑', { exact: true }).fill('尚未核对福利细节');
    await form.getByLabel('沟通场景', { exact: true }).fill('仅供本地界面验收');
    await captureWidths(qa, page, 'offer-negotiation-unsent-draft');
    await btn(form, '关闭').click();
    await form.waitFor({ state: 'hidden' });
    qa.observed('bound Offer input facts and editable preflight; close returns without provider submission');
  });
  await qa.disposition('S23', 'offer-ai-negotiation-output', 'BLOCKED', 'provider generation and generated history require separate authorization');
}

async function pilotSettingsFlows(qa, page, record) {
  await qa.run('S24', 'pilot-context-popup-and-page', ['投递', '列表', '问 Pilot'], async () => {
    await navigate(page, 'applications-list');
    const list = region(page, '投递列表');
    await list.getByPlaceholder('搜索公司、岗位、备注', { exact: true }).fill(record.company_name);
    const row = list.locator(`tr[data-row-key="${record.id}"]`);
    await row.waitFor(); await btn(row, '问 Pilot').click();
    await btn(page, '上下文面板').waitFor();
    await page.locator('[aria-label="本次请求上下文"]').filter({ hasText: record.company_name }).waitFor();
    await qa.capture('pilot-contextual-surface');
    await btn(page, '上下文面板').click();
    await qa.capture('pilot-context-panel');
    await btn(page, '打开 Pilot tab').click();
    await btn(page, '退出沉浸模式，返回原页面').waitFor();
    await captureWidths(qa, page, 'pilot-full-workspace');
    const composer = page.locator('textarea').filter({ visible: true });
    assert.equal(await composer.count(), 1);
    await composer.fill(`${prefix} 仅为未发送草稿`);
    await qa.capture('pilot-unsent-draft');
    await composer.fill('');
    await btn(page, '退出沉浸模式，返回原页面').click();
    await region(page, '投递列表').waitFor();
    qa.observed('row-owned contextual Pilot; context panel; full workspace expansion; unsent draft clear; return to list');
  });
  await qa.run('S25', 'haru-runtime-and-context-menu', ['Haru 助手'], async () => {
    await navigate(page, 'applications-list');
    const mascot = page.getByRole('complementary', exact('Haru 助手'));
    await mascot.waitFor();
    const failed = await mascot.getAttribute('data-load-failed') === 'true';
    const box = await mascot.boundingBox();
    assert.ok(box);
    if (failed) {
      assert.ok(box.width <= 157 && box.height <= 49, 'natural Haru fallback footprint must be a small dock');
      await qa.disposition('S25', 'haru-live2d-runtime', 'BLOCKED', 'genuine installed runtime failure observed; fallback usability is a separate interaction check');
    }
    await qa.capture(failed ? 'haru-natural-runtime-failure' : 'haru-normal-runtime', { haruRuntime: failed ? 'failed-naturally' : 'visible-no-failure-flag', haruBounds: box });
    await mascot.getByRole('button').click({ button: 'right' });
    await page.getByRole('menuitem', exact('恢复默认大小')).waitFor();
    await qa.capture('haru-context-menu');
    await page.keyboard.press('Escape');
    await page.getByRole('menuitem', exact('恢复默认大小')).waitFor({ state: 'hidden' });
    qa.observed('natural Haru runtime/fallback recorded; real context menu and Escape; no runtime stub or CSP change');
  });
  await qa.run('S29', 'settings-haru-appearance-restore', ['设置', 'Haru'], async () => {
    await navigate(page, 'settings');
    const visible = page.getByRole('switch', exact('显示 Haru'));
    const wasVisible = await visible.getAttribute('aria-checked') === 'true';
    if (wasVisible) await visible.click();
    await page.getByRole('complementary', exact('Haru 助手')).waitFor({ state: 'hidden' });
    await qa.capture('settings-haru-hidden');
    await visible.click();
    await page.getByRole('complementary', exact('Haru 助手')).waitFor();
    await select(page, page.getByRole('combobox', exact('Haru 角色大小')), '80%');
    await select(page, page.getByRole('combobox', exact('Haru 动画级别')), '关闭');
    await captureWidths(qa, page, 'settings-haru-small-static');
    await select(page, page.getByRole('combobox', exact('Haru 角色大小')), '100%');
    await select(page, page.getByRole('combobox', exact('Haru 动画级别')), '完整');
    await btn(page, '重置 Haru 位置').click();
    if (!wasVisible) await visible.click();
    await theme(page, 'light'); await qa.capture('settings-light-theme'); await theme(page, 'dark');
    qa.observed('hide removes live hitbox; restore; size/animation selections; original fresh-profile appearance restored');
  });
  await qa.run('S26', 'settings-ai-readonly-and-return', ['设置', '配置 AI'], async () => {
    await navigate(page, 'settings');
    await page.locator('summary').filter({ hasText: /^高级运行信息$/ }).click();
    await qa.capture('settings-runtime-details');
    await page.locator('summary').filter({ hasText: /^高级运行信息$/ }).click();
    await btn(page, '配置 AI').click();
    await region(page, 'AI 设置').waitFor();
    // Only the new synthetic profile is used. Never touch key/access/provider/HITL controls.
    assert.equal(await region(page, 'AI 设置').locator('input[type="password"]').inputValue(), '');
    await captureWidths(qa, page, 'settings-ai-unconfigured');
    await btn(region(page, 'AI 设置'), '返回设置').click();
    await btn(page, '配置 AI').waitFor();
    qa.observed('advanced runtime expand/collapse; fresh blank key confirmed; AI settings opens and returns without save');
  });
  await qa.run('S27', 'settings-preference-cancel', ['设置', '添加偏好'], async () => {
    await navigate(page, 'settings');
    await btn(page, '添加偏好').click();
    const form = dialog(page, '确认个人偏好');
    await form.waitFor();
    assert.equal(await btn(form, '确认保存').isDisabled(), true);
    await qa.capture('settings-preference-empty-confirmation');
    await btn(form, '取消').click(); await form.waitFor({ state: 'hidden' });
    qa.observed('empty preference confirmation disabled; cancel; no preference/proactive/access settings changed');
  });
  await qa.run('R13', 'settings-safe-sections', ['设置'], async () => {
    await navigate(page, 'settings');
    for (const [index, id] of ['data-backup-settings-title', 'pilot-mascot-settings-title', 'voice-settings-title'].entries()) {
      const heading = page.locator(`#${id}`); await heading.scrollIntoViewIfNeeded();
      await qa.capture(`settings-section-${index + 1}`);
    }
    qa.observed('data/backup, Haru and voice sections visible without restricted operations');
  }, 'visual');
  await qa.run('R01', 'native-browser-history-back-forward', ['今日', '投递', 'Back', 'Forward'], async () => {
    await navigate(page, 'dashboard'); await navigate(page, 'applications-list');
    await page.goBack();
    await page.waitForURL((url) => url.searchParams.get('view') === 'board');
    await page.goBack();
    await page.waitForURL((url) => url.searchParams.get('view') === 'dashboard');
    await page.goForward();
    await page.waitForURL((url) => url.searchParams.get('view') === 'board');
    await page.goForward();
    await region(page, '投递列表').waitFor();
    await qa.capture('history-forward-restored-list');
    qa.observed('actual history/popstate navigation restores dashboard, board and list without synthetic routes');
  });
}

// Exported for isolated helper preflight; these functions still drive only the public UI.
export { navigate, createApplication };
