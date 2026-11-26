// Synthetic integration only. Uses the real API and copy-only UI, never mail/model services.
import assert from 'node:assert/strict';
import path from 'node:path';

const cases = [
  { key: 'singapore', timeZone: 'Asia/Singapore', locale: 'zh-CN',
    submitted: '2026-11-30T16:05:00', event: '2026-11-30T16:05:00Z', outcome: '2026-12-01T16:05:00',
    submissionDay: '2026-12-01', eventDay: '2026-12-01', outcomeDay: '2026-12-02', bodyDate: '2026年12月1日',
    details: '查看详情', facts: '投递事实与结果', generate: '生成草稿', copy: '复制完整内容',
    copied: '完整草稿已复制', thank: '面试感谢信', submissionEvidence: '投递时间', eventEvidence: '日程',
    outcomeEvidence: '结果', outcomeResult: '进入下一阶段', linkedEvent: '第 2 轮面试' },
  { key: 'new-york', timeZone: 'America/New_York', locale: 'en-US',
    submitted: '2026-07-15T04:30:00', event: '2026-07-16T04:30:00Z', outcome: '2026-07-17T04:30:00',
    submissionDay: '2026-07-15', eventDay: '2026-07-16', outcomeDay: '2026-07-17', bodyDate: '7/15/2026',
    details: 'View details', facts: 'Application facts and outcomes', generate: 'Generate draft', copy: 'Copy full content',
    copied: 'Full draft copied', thank: 'Interview thank-you', submissionEvidence: 'Applied', eventEvidence: 'Event',
    outcomeEvidence: 'Outcome', outcomeResult: 'Advanced to next stage', linkedEvent: 'Interview round 2' },
];

export async function verifyCommunicationDates({ browser, base, output }) {
  const results = [];
  for (const scenario of cases) {
    const origin = new URL(base).origin;
    assert.equal(new URL(origin).hostname, '127.0.0.1');
    const context = await browser.newContext({ locale: scenario.locale, timezoneId: scenario.timeZone, viewport: { width: 1440, height: 1100 } });
    const external = [];
    const mutations = [];
    const errors = [];
    await context.route('**/*', route => {
      const { origin: target, pathname } = new URL(route.request().url());
      const method = route.request().method();
      if (target !== origin) { external.push(target); return route.abort(); }
      if (pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(method)
        && !(method === 'POST' && pathname === '/api/companion/presence')) {
        mutations.push(method + ' ' + pathname);
        return route.abort();
      }
      return route.continue();
    });
    await context.addInitScript(({ locale }) => {
      localStorage.setItem('offerpilot.locale', locale);
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async text => { window.__syntheticDraftClipboard = text; } },
      });
    }, { locale: scenario.locale });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    async function api(endpoint, data) {
      // Browser-context request seeding is test setup, not an action from draft UI.
      const response = await context.request.fetch(origin + '/api' + endpoint,
        data ? { method: 'POST', data } : undefined);
      assert.ok(response.ok(), endpoint + ': ' + response.status() + ' ' + await response.text());
      return response.json();
    }
    try {
      const companyName = 'Synthetic Communication ' + scenario.key;
      const app = await api('/applications', { company_name: companyName, position_name: 'Fixture Engineer' });
      const appPath = '/applications/' + app.id;
      const resume = await api('/resumes/from-sample', { sample_id: 'backend', title: 'Synthetic calendar résumé' });
      const jd = await api(appPath + '/job-description/versions', {
        jd_text: 'Synthetic calendar acceptance only', expected_current_version_id: null,
        idempotency_key: 'synthetic-draft-dates-jd-' + scenario.key,
      });
      const snapshot = await api(appPath + '/submission-snapshots', {
        resume_id: resume.id, jd_version_id: jd.id, material_kit_id: null, note: '',
        submitted_at: scenario.submitted + 'Z', idempotency_key: 'synthetic-draft-dates-archive-' + scenario.key,
      });
      const event = await api('/application-events', { application_id: app.id, event_type: 'interview',
        scheduled_at: scenario.event, duration_minutes: 45, round: 2, status: 'done' });
      const outcome = await api(appPath + '/outcomes', { submission_snapshot_id: snapshot.id,
        application_event_id: event.id, stage: 'interview', result: 'advanced', feedback_tags: [],
        occurred_at: scenario.outcome + 'Z', idempotency_key: 'synthetic-draft-dates-outcome-' + scenario.key });
      const archivesBefore = await api(appPath + '/submission-snapshots');
      const outcomesBefore = await api(appPath + '/outcomes');
      const eventBefore = await api('/application-events/' + event.id);
      const appBefore = await api(appPath);
      const fixtureSafetyBefore = await api('/job-mail/fixture-safety');
      assert.deepEqual(archivesBefore.map(x => [x.id, x.submitted_at]), [[snapshot.id, scenario.submitted]]);
      assert.deepEqual(outcomesBefore.map(x => [x.id, x.application_event_id, x.occurred_at]), [[outcome.id, event.id, scenario.outcome]]);
      assert.equal(eventBefore.scheduled_at, scenario.event);

      await page.clock.setFixedTime(new Date('2026-01-15T12:00:00Z'));
      await page.goto(origin + '/?view=board');
      assert.deepEqual(await page.evaluate(() => [Intl.DateTimeFormat().resolvedOptions().timeZone, new Date().getTimezoneOffset()]),
        [scenario.timeZone, scenario.key === 'singapore' ? -480 : 300]);
      const card = page.locator('[data-kanban-card]').filter({ hasText: companyName });
      await card.getByTitle(scenario.details, { exact: true }).click();
      await page.locator('#application-preparation-tab').click();
      await page.locator('#application-preparation-panel').getByRole('button', { name: scenario.facts, exact: true }).click();
      const panel = page.locator('section[aria-labelledby="communication-draft-title"]');
      await panel.waitFor();
      const panelButton = label => panel.getByRole('button').filter({ hasText: label });
      await panelButton(scenario.generate).click();
      const body = panel.locator('#communication-body');
      const subject = panel.locator('#communication-subject');
      assert.ok((await body.inputValue()).includes(scenario.bodyDate), scenario.key + ': follow-up body uses the projected calendar day');
      await panel.getByText(scenario.submissionEvidence + ' · ' + scenario.submissionDay, { exact: true }).waitFor();
      const editedSubject = 'Synthetic edited subject';
      const editedBody = await body.inputValue() + '\nSynthetic manual addition.';
      await subject.fill(editedSubject);
      await body.fill(editedBody);
      await panelButton(scenario.copy).click();
      await panel.getByText(scenario.copied, { exact: true }).waitFor({ state: 'attached' });
      assert.equal(await page.evaluate(() => window.__syntheticDraftClipboard), editedSubject + '\n\n' + editedBody);

      console.log('Communication dates: edited follow-up copied in ' + scenario.timeZone);
      await panelButton(scenario.thank).click();
      await panel.locator('#communication-outcome').click();
      const outcomeOption = page.locator('.ant-select-dropdown:visible').getByText(scenario.outcomeDay + ' · ' + scenario.outcomeResult, { exact: true });
      await outcomeOption.click();
      await panel.getByText(scenario.linkedEvent + ' · ' + scenario.eventDay, { exact: true }).waitFor();
      await panelButton(scenario.generate).click();
      await panel.getByText(scenario.submissionEvidence + ' · ' + scenario.submissionDay, { exact: true }).waitFor();
      assert.equal(await panel.getByText(new RegExp('^' + scenario.outcomeEvidence + ' #' + outcome.id + ' ·')).count(), 1);
      const eventEvidence = panel.getByText(new RegExp('^' + scenario.eventEvidence + ' #' + event.id + ' ·.* · ' + scenario.eventDay + '$'));
      await eventEvidence.waitFor();
      console.log('Communication dates: linked outcome and event verified in ' + scenario.timeZone);
      await panel.screenshot({ path: path.join(output, 'communication-dates-' + scenario.key + '.png') });
      assert.deepEqual(await api(appPath + '/submission-snapshots'), archivesBefore);
      assert.deepEqual(await api(appPath + '/outcomes'), outcomesBefore);
      assert.deepEqual(await api('/application-events/' + event.id), eventBefore);
      assert.deepEqual(await api(appPath), appBefore);
      assert.deepEqual(await api('/job-mail/fixture-safety'), fixtureSafetyBefore);
      assert.deepEqual(mutations, []);
      assert.deepEqual(external, []);
      assert.deepEqual(errors, []);
      results.push({ timeZone: scenario.timeZone, locale: scenario.locale, submissionDay: scenario.submissionDay,
        eventDay: scenario.eventDay, outcomeDay: scenario.outcomeDay, clipboardEditsPreserved: true, unexpectedApiWrites: mutations });
    } catch (error) {
      await page.screenshot({ path: path.join(output, 'communication-dates-' + scenario.key + '-failure.png') }).catch(() => undefined);
      throw error;
    } finally {
      await context.close();
    }
  }
  return results;
}
