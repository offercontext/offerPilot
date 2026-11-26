// Same-company decoys exercise real UI ownership with a loopback-only fixture.
import assert from 'node:assert/strict';
import path from 'node:path';

const archivedJd = '合成旧岗位说明\n' + '旧版 JD 完整内容。'.repeat(600) + '\n归档末行：完整词尾';
const jd = '合成岗位资料：维护 Python API 和 SQL 服务。';
const submittedAt = '2026-01-15T04:30:00.000Z';
const submittedDay = '2026-01-15';
const snapshotNote = 'Synthetic B v1 archive.';

function visibleOption(page, label) {
  return page.locator('.ant-select-dropdown:visible').getByText(label, { exact: true });
}

async function openPreparation(page, base, app) {
  await page.goto(base + '/?view=board');
  const card = page.locator('[data-kanban-card]').filter({ hasText: app.position_name });
  await card.getByTitle('查看详情', { exact: true }).click();
  await page.locator('#application-preparation-tab').click();
  return page.locator('#application-preparation-panel');
}

async function openFacts(preparation, page) {
  await preparation.getByRole('button', { name: '投递事实与结果', exact: true }).click();
  const drawer = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: / · 投递事实与结果$/ }) });
  await drawer.waitFor();
  return drawer;
}

function responseFor(base, endpoint, method, page) {
  return page.waitForResponse(response => response.url() === base + '/api' + endpoint
    && response.request().method() === method);
}

async function assertOk(response) {
  assert.ok(response.ok(), `${response.request().method()} ${new URL(response.url()).pathname}: ${response.status()} ${await response.text()}`);
  return response.json();
}

async function readCase(api, id) {
  const appPath = `/applications/${id}`;
  const [application, currentJd, snapshots, outcomes, events, proposals] = await Promise.all([
    api(appPath), api(appPath + '/job-description'), api(appPath + '/submission-snapshots'),
    api(appPath + '/outcomes'), api(`/application-events?application_id=${id}`),
    api(appPath + '/interview-preparation-proposals'),
  ]);
  return { application, currentJd, snapshots, outcomes, events, proposals };
}

export async function saveAndUpdateJourneyMaterials({ context, base, api, output, pageErrors }) {
  // The selected clock only affects the archive-saving page. The main mail
  // page still runs with live time, so its future event stays eligible.
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  const company_name = '合成星河科技';
  const appA = await api('/applications', { method: 'POST', data: { company_name, position_name: 'A 端数据工程师' } });
  const app = await api('/applications', { method: 'POST', data: { company_name, position_name: 'B 端后端工程师' } });
  assert.equal(appA.company_name, app.company_name);
  assert.notEqual(appA.id, app.id);
  assert.notEqual(appA.position_name, app.position_name);
  const pathA = `/applications/${appA.id}`;
  const pathB = `/applications/${app.id}`;
  const archivePath = pathB + '/submission-snapshots';
  const jdPath = pathB + '/job-description';
  const resumeA = await api('/resumes/from-sample', { method: 'POST', data: { sample_id: 'backend', title: '合成 A 独有简历' } });
  const jdA = await api(pathA + '/job-description/versions', { method: 'POST', data: {
    jd_text: '合成 A 独有数据岗位说明。', expected_current_version_id: null, idempotency_key: 'synthetic-journey-jd-a',
  } });
  const archiveA = await api(pathA + '/submission-snapshots', { method: 'POST', data: {
    resume_id: resumeA.id, jd_version_id: jdA.id, material_kit_id: null, note: '合成 A 专属档案',
    submitted_at: '2026-01-12T04:30:00Z', idempotency_key: 'synthetic-journey-archive-a',
  } });
  const frozenResume = {
    experience: [{ company: '合成历史公司', highlights: ['归档的实际结构化成绩'] }], raw_text: '合成提取原文',
  };
  const archivedResume = await api('/resumes', { method: 'POST', data: {
    title: '合成 B 归档简历', text: frozenResume.raw_text, content_json: frozenResume,
  } });
  const archivedVersion = await api(jdPath + '/versions', { method: 'POST', data: {
    jd_text: archivedJd, expected_current_version_id: null, idempotency_key: 'synthetic-journey-jd-b-v1',
  } });
  const aBefore = await readCase(api, appA.id);
  const materialFixtureBefore = await api('/job-mail/fixture-safety');
  const expectedWrites = [`POST /api${archivePath}`, `POST /api${jdPath}/versions`];
  const observedWrites = [];
  const rejectedWrites = [];
  await page.route('**/*', route => {
    const { origin, pathname } = new URL(route.request().url());
    const method = route.request().method();
    if (origin === new URL(base).origin && pathname.startsWith('/api/')
      && !['GET', 'HEAD', 'OPTIONS'].includes(method)
      && !(method === 'POST' && pathname === '/api/companion/presence')) {
      const write = `${method} ${pathname}`;
      if (write !== expectedWrites[observedWrites.length]) { rejectedWrites.push(write); return route.abort(); }
      observedWrites.push(write);
    }
    return route.fallback();
  });
  try {
    await page.clock.setFixedTime(new Date(submittedAt));
    assert.deepEqual(await api(archivePath), []);
    const preparation = await openPreparation(page, base, app);
    await preparation.getByText('当前 JD · 版本 1', { exact: true }).waitFor();
    const drawer = await openFacts(preparation, page);
    // Ant's selected label covers the read-only search input; use its real keyboard control.
    await drawer.getByRole('combobox', { name: '实际提交的简历' }).press('ArrowDown');
    await visibleOption(page, archivedResume.title).click();
    const note = drawer.getByRole('textbox', { name: '投递备注' });
    await note.pressSequentially(snapshotNote);
    assert.equal(await note.inputValue(), snapshotNote);
    const [saveResponse] = await Promise.all([
      responseFor(base, archivePath, 'POST', page),
      drawer.getByRole('button').filter({ hasText: '直接保存档案' }).click(),
    ]);
    const archivedRecord = await assertOk(saveResponse);
    const body = saveResponse.request().postDataJSON();
    assert.equal(body.resume_id, archivedResume.id);
    assert.equal(body.jd_version_id, archivedVersion.id);
    assert.equal(body.note, snapshotNote, 'The real form must submit the typed optional note');
    assert.equal(Date.parse(body.submitted_at), new Date(submittedAt).getTime());
    assert.equal(archivedRecord.application_id, app.id);
    assert.notEqual(archivedRecord.id, archiveA.id);
    assert.equal(archivedRecord.jd_version_id, archivedVersion.id);
    assert.equal(archivedRecord.jd_version_number, 1);
    assert.equal(archivedRecord.resume_id, archivedResume.id);
    assert.equal(archivedRecord.resume_title, archivedResume.title);
    assert.deepEqual(archivedRecord.resume_snapshot, frozenResume);
    assert.equal(archivedRecord.jd_snapshot, archivedJd);
    assert.equal(archivedRecord.note, snapshotNote);
    assert.equal(archivedRecord.source_kind, 'ui');
    assert.equal(archivedRecord.submitted_at, '2026-01-15T04:30:00');
    assert.equal(archivedRecord.source_states.jd, 'current');
    assert.deepEqual(await api(archivePath), [archivedRecord]);
    await drawer.getByText(new RegExp(`^2026-01-15 \\d{2}:\\d{2} · ${archivedResume.title}$`)).waitFor();
    await drawer.screenshot({ path: path.join(output, 'same-case-ui-save-v1.png') });
    console.log('Same-case journey: UI archive saved and read back for B v1');

    const reopened = await openPreparation(page, base, app);
    await reopened.getByRole('button', { name: '更新 JD', exact: true }).click();
    const editor = page.getByRole('dialog', { name: '投递岗位资料' });
    const jdInput = editor.getByPlaceholder('粘贴岗位描述', { exact: true });
    assert.equal(await jdInput.inputValue(), archivedJd);
    await jdInput.fill(jd);
    const [jdResponse] = await Promise.all([
      responseFor(base, jdPath + '/versions', 'POST', page),
      editor.getByRole('button').filter({ hasText: '保存岗位资料' }).click(),
    ]);
    const jdVersion = await assertOk(jdResponse);
    assert.equal(jdResponse.request().postDataJSON().expected_current_version_id, archivedVersion.id);
    assert.equal(jdVersion.application_id, app.id);
    assert.equal(jdVersion.version_number, 2);
    assert.notEqual(jdVersion.id, archivedVersion.id);
    assert.equal((await api(jdPath)).current.id, jdVersion.id);
    assert.equal((await api(jdPath)).current.jd_text, jd);
    await reopened.getByText('当前 JD · 版本 2', { exact: true }).waitFor();
    const archiveBefore = await api(archivePath);
    assert.deepEqual(archiveBefore, [{ ...archivedRecord, source_states: { ...archivedRecord.source_states, jd: 'changed' } }]);
    assert.deepEqual(await readCase(api, appA.id), aBefore);
    assert.deepEqual(await api('/job-mail/fixture-safety'), materialFixtureBefore);
    assert.deepEqual(observedWrites, expectedWrites);
    assert.deepEqual(rejectedWrites, []);
    console.log('Same-case journey: normal JD edit changed only B current version; archived B v1 stays frozen');
    return { app, appA, archiveA, aBefore, archivedJd, jd, jdVersion, archivePath, archivedRecord, archiveBefore };
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'same-case-materials-failure.png') }).catch(() => undefined);
    throw error;
  } finally {
    await page.close();
  }
}

export async function copyJourneyFollowUp({ page, base, api, output, journey, eventB, taskKey }) {
  const { app, appA, archiveA, aBefore, archivedRecord, archiveBefore, jdVersion } = journey;
  const bBefore = await readCase(api, app.id);
  const fixtureBefore = await api('/job-mail/fixture-safety');
  assert.deepEqual(await readCase(api, appA.id), aBefore);
  assert.deepEqual(bBefore.snapshots, archiveBefore);
  assert.deepEqual(bBefore.outcomes, [], 'Ordinary follow-up must not fabricate an outcome');
  assert.equal(bBefore.currentJd.current.id, jdVersion.id);
  assert.ok(bBefore.events.some(event => event.id === eventB.id && event.application_id === app.id));
  try {
    const preparation = await openPreparation(page, base, app);
    const drawer = await openFacts(preparation, page);
    const panel = drawer.locator('section[aria-labelledby="communication-draft-title"]');
    await panel.waitFor();
    const button = label => panel.getByRole('button').filter({ hasText: label });
    await button('投递跟进信').click();
    const picker = panel.getByRole('combobox', { name: '投递档案' });
    await picker.press('ArrowDown');
    await visibleOption(page, '2026-01-12 · 合成 A 独有简历 · JD v1').waitFor({ state: 'hidden' });
    const option = `${submittedDay} · ${archivedRecord.resume_title} · JD v1`;
    await visibleOption(page, option).click();
    await panel.getByText(`档案 #${archivedRecord.id}`, { exact: true }).waitFor();
    await panel.getByText('UI 保存', { exact: true }).waitFor();
    await panel.getByText('JD v1', { exact: true }).waitFor();
    assert.equal(await panel.getByText(`档案 #${archiveA.id}`, { exact: true }).count(), 0);
    await button('生成草稿').click();
    await panel.getByText(`投递档案 #${archivedRecord.id} · ${archivedRecord.resume_title} · JD v1`, { exact: true }).waitFor();
    await panel.getByText(`投递时间 · ${submittedDay}`, { exact: true }).waitFor();
    await panel.getByText(`当前投递信息 · ${app.company_name} · ${app.position_name}`, { exact: true }).waitFor();
    assert.equal(await panel.getByText(/^(结果|日程) #/).count(), 0);
    const subject = panel.locator('#communication-subject');
    const body = panel.locator('#communication-body');
    const generatedBody = await body.inputValue();
    assert.ok((await subject.inputValue()).includes(app.position_name));
    assert.ok(generatedBody.includes(app.position_name));
    assert.ok(generatedBody.includes('2026年1月15日'));
    assert.ok(!generatedBody.includes(appA.position_name));
    assert.ok(!generatedBody.includes('2026年1月12日'));
    const editedSubject = '手动编辑：' + await subject.inputValue();
    const editedBody = generatedBody + '\n合成手动补充，仅复制不发送。';
    await subject.fill(editedSubject);
    await body.fill(editedBody);
    await button('复制完整内容').click();
    await panel.getByText('完整草稿已复制', { exact: true }).waitFor({ state: 'attached' });
    assert.equal(await page.evaluate(() => window.__sameCaseClipboard), editedSubject + '\n\n' + editedBody);
    await panel.screenshot({ path: path.join(output, 'same-case-follow-up-copy.png') });
    assert.deepEqual(await readCase(api, app.id), bBefore);
    assert.deepEqual(await readCase(api, appA.id), aBefore);
    assert.deepEqual(await api('/job-mail/fixture-safety'), fixtureBefore);
    return { applicationAId: appA.id, applicationBId: app.id, archiveAId: archiveA.id, archiveBId: archivedRecord.id,
      archiveSource: archivedRecord.source_kind, frozenJdVersionId: archivedRecord.jd_version_id, currentJdVersionId: jdVersion.id,
      submissionDay: submittedDay, confirmedEventId: eventB.id, exactTaskKey: taskKey, clipboardEditsPreserved: true,
      applicationAUnchanged: true, readOnlyCopyPreservedB: true };
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'same-case-follow-up-failure.png') }).catch(() => undefined);
    throw error;
  }
}
