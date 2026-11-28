// Synthetic app, exact archived identities; no model, mailbox or external calls.
import assert from 'node:assert/strict';

export async function verifyApplicationStrategy({ browser, base }) {
  const origin = new URL(base).origin;
  assert.equal(new URL(origin).hostname, '127.0.0.1');
  const context = await browser.newContext({ locale: 'zh-CN', timezoneId: 'Asia/Singapore', viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  const writes = [];
  const unexpected = [];
  const errors = [];
  const allowed = new Set();
  page.on('pageerror', error => errors.push(error.message));
  await context.addInitScript(() => localStorage.setItem('offerpilot.locale', 'zh-CN'));
  await context.route('**/*', route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) { unexpected.push(url.origin); return route.abort(); }
    const method = request.method();
    if (!url.pathname.startsWith('/api/') || ['GET', 'HEAD', 'OPTIONS'].includes(method)
      || (method === 'POST' && url.pathname === '/api/companion/presence')) return route.continue();
    if (method === 'POST' && allowed.has(url.pathname)) {
      writes.push({ path: url.pathname, input: request.postDataJSON() });
      return route.continue();
    }
    unexpected.push(method + ' ' + url.pathname);
    return route.abort();
  });
  const api = async (endpoint, data) => {
    const response = await context.request.fetch(origin + '/api' + endpoint, data ? { method: 'POST', data } : undefined);
    assert.ok(response.ok(), endpoint + ': ' + response.status() + ' ' + await response.text());
    return response.json();
  };
  const fixture = 'Synthetic Strategy ';
  const iso = (day, hour) => `2025-08-${day}T${hour}:00:00+08:00`;
  async function seed(name, resume) {
    const key = name.replace(/ /g, '-');
    const app = await api('/applications', { company_name: fixture + name, position_name: 'Fixture Engineer' });
    const appPath = '/applications/' + app.id;
    const jd = await api(appPath + '/job-description/versions', {
      jd_text: 'Strategy fixture only', expected_current_version_id: null, idempotency_key: 'strategy-jd-version-' + key,
    });
    return {
      app, appPath,
      archive: (day, hour) => api(appPath + '/submission-snapshots', {
        resume_id: resume.id, jd_version_id: jd.id, material_kit_id: null, note: '',
        submitted_at: iso(day, hour), idempotency_key: `strategy-archive-${key}-${day}-${hour}`,
      }),
      record: (archive, day, hour, result, suffix) => api(appPath + '/outcomes', {
        submission_snapshot_id: archive.id, stage: 'screening', result, feedback_tags: [],
        occurred_at: iso(day, hour), idempotency_key: `strategy-outcome-${key}-${suffix}`,
      }),
    };
  }
  const option = label => page.locator('.ant-select-dropdown:visible').getByText(label, { exact: true });
  async function select(control, label) {
    await control.click();
    await option(label).click();
  }
  async function openFacts(companyName) {
    await page.goto(origin + '/?view=board');
    const card = page.locator('[data-kanban-card]').filter({ hasText: companyName });
    await card.getByTitle('查看详情', { exact: true }).click();
    await page.locator('#application-preparation-tab').click();
    await page.locator('#application-preparation-panel').getByRole('button', { name: '投递事实与结果', exact: true }).click();
    const drawer = page.getByRole('dialog').filter({ hasText: 'APPLICATION EVIDENCE ARCHIVE' });
    await drawer.getByText('历史与来源风险', { exact: true }).waitFor();
    return drawer;
  }
  try {
    const resumeName = 'Synthetic Strategy Resume';
    const resume = await api('/resumes/from-sample', { sample_id: 'backend', title: resumeName });
    const [a, b, c] = await Promise.all(['A Origin', 'B Target', 'C Other'].map(name => seed(name, resume)));
    const aArchive = await a.archive('12', '09');
    const aResult = await a.record(aArchive, '12', '12', 'rejected', 'origin');
    const [b1, b2, other] = await Promise.all([b.archive('13', '10'), b.archive('13', '15'), c.archive('13', '11')]);
    await b.record(b1, '14', '12', 'advanced', 'wrong-archive');
    await c.record(other, '14', '13', 'advanced', 'wrong-job');
    const createEvent = (round, hour) => api('/application-events', { application_id: b.app.id, event_type: 'interview',
      scheduled_at: iso('15', hour), duration_minutes: 45, round, status: 'done' });
    const [first, second] = await Promise.all([createEvent(1, '10'), createEvent(2, '15')]);
    const archiveLabel = (archive, time) => `2025-08-13 ${time} · ${resumeName} · JD v1 · 档案 #${archive.id}`;
    const rounds = [`2025-08-15 10:00 · 第 1 轮面试 · 日程 #${first.id}`,
      `2025-08-15 15:00 · 第 2 轮面试 · 日程 #${second.id}`];
    const bOutcomePath = b.appPath + '/outcomes';
    const strategyPath = a.appPath + '/outcomes/' + aResult.id + '/strategy';
    const beforeA = await api(a.appPath + '/outcomes');
    const beforeC = await api(c.appPath + '/outcomes');
    const archivesBefore = await api(b.appPath + '/submission-snapshots');
    allowed.add('/api' + bOutcomePath);
    allowed.add('/api' + strategyPath);
    const bFacts = await openFacts(b.app.company_name);
    await select(bFacts.getByLabel('关联投递档案', { exact: true }), archiveLabel(b1, '10:00'));
    await select(bFacts.getByLabel('关联投递档案', { exact: true }), archiveLabel(b2, '15:00'));
    const eventControl = bFacts.getByLabel('关联日程（可选）', { exact: true });
    await eventControl.click();
    await option(rounds[0]).waitFor();
    await option(rounds[1]).click();
    const observed = page.waitForResponse(response => response.url().endsWith('/api' + bOutcomePath) && response.request().method() === 'POST');
    await bFacts.getByRole('button', { name: '直接记录结果' }).click();
    const interviewResponse = await observed;
    assert.ok(interviewResponse.ok());
    const interview = await interviewResponse.json();
    assert.equal(interview.application_event_id, second.id);
    assert.equal(interview.submission_snapshot_id, b2.id);
    assert.ok((await api(bOutcomePath)).some(item => item.id === interview.id && item.application_event_id === second.id && item.submission_snapshot_id === b2.id));
    await bFacts.locator('article').filter({ hasText: rounds[1] }).getByText(archiveLabel(b2, '15:00'), { exact: true }).waitFor();

    const aFacts = await openFacts(a.app.company_name);
    await aFacts.getByRole('button', { name: '把改法带到下一次投递' }).click();
    const panel = aFacts.getByRole('region', { name: '投递改法跟进' });
    await panel.getByRole('textbox', { name: '下一次的具体改法' }).fill('Add a verifiable project metric');
    await panel.getByRole('button', { name: '保存改法' }).click();
    await select(panel.getByRole('combobox', { name: '使用改法的岗位' }), `${b.app.company_name} · Fixture Engineer · #${b.app.id}`);
    await select(panel.getByRole('combobox', { name: '实际使用的投递档案' }), archiveLabel(b2, '15:00'));
    await panel.getByRole('textbox', { name: '实际用上的改动' }).fill('Added an independently verifiable count');
    await panel.getByRole('button', { name: '确认已用在这份档案' }).click();
    await panel.getByText('等待这份档案的初筛结果', { exact: true }).waitFor();
    assert.deepEqual((await api(strategyPath)).comparable_outcomes, []);
    const bResult = await b.record(b2, '16', '12', 'advanced', 'exact');
    await panel.getByRole('button', { name: '刷新结果' }).click();
    await select(panel.getByRole('combobox', { name: '用于比较的初筛结果' }), `2025-08-16 12:00 · 初筛通过 · 结果 #${bResult.id}`);
    await select(panel.getByRole('combobox', { name: '下次是否沿用改法' }), '保留改法');
    await panel.getByRole('button', { name: '确认本次决定' }).click();
    await panel.getByText('这一次的决定：保留改法', { exact: true }).waitFor();
    const saved = await api(strategyPath);
    assert.equal(saved.target.id, b2.id);
    assert.equal(saved.change.decided_outcome_id, bResult.id);
    assert.deepEqual(writes.map(item => [item.path, item.input.action ?? 'outcome']), [
      ['/api' + bOutcomePath, 'outcome'], ['/api' + strategyPath, 'propose'],
      ['/api' + strategyPath, 'confirm_applied'], ['/api' + strategyPath, 'decide'],
    ]);
    assert.deepEqual(await api(a.appPath + '/outcomes'), beforeA);
    assert.deepEqual(await api(c.appPath + '/outcomes'), beforeC);
    assert.deepEqual(await api(b.appPath + '/submission-snapshots'), archivesBefore);
    assert.deepEqual(unexpected, []);
    assert.deepEqual(errors, []);
    return { archive: b2.id, separatedArchive: b1.id, event: second.id, recorded: interview.id, compared: bResult.id, writes: writes.length };
  } finally {
    await context.close();
  }
}
