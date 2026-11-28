// The Mail workflow's loopback-only fixture also exercises the next journey step.
import assert from 'node:assert/strict';
import { waitForMailState } from './job-mail-browser-wait.mjs';

export async function verifyExactDebrief({ context, base, api, pageErrors }) {
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  const company_name = '合成同公司复盘';
  const addApp = position_name => api('/applications', { method: 'POST', data: { company_name, position_name } });
  const a = await addApp('复盘夹具岗位 A');
  const b = await addApp('复盘夹具岗位 B');
  const seedNow = Date.now();
  const addEvent = (app, round, hoursAgo) => api('/application-events', { method: 'POST', data: {
    application_id: app.id, event_type: 'interview', status: 'done', round, duration_minutes: 45,
    scheduled_at: new Date(seedNow - hoursAgo * 3600_000).toISOString(),
  } });
  const a1 = await addEvent(a, 1, 4);
  const b1 = await addEvent(b, 1, 3);
  const b2 = await addEvent(b, 2, 2);
  const notePath = `/applications/${b.id}/notes`;
  const writes = [];
  const unexpected = [];
  const reflection = '仅保存 B 岗位第 2 轮的合成复盘。';
  let saveArmed = false;
  await page.route('**/*', route => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.origin === new URL(base).origin && url.pathname.startsWith('/api/')
      && !['GET', 'HEAD', 'OPTIONS'].includes(method)
      && !(method === 'POST' && url.pathname === '/api/companion/presence')) {
      const write = `${method} ${url.pathname}`;
      if (!saveArmed || writes.length || method !== 'POST' || url.pathname !== '/api' + notePath
        || request.postDataJSON()?.application_event_id !== b2.id) {
        unexpected.push(write);
        return route.abort();
      }
      writes.push(write);
    }
    return route.fallback();
  });
  const ids = items => items.filter(item => item.reason_code === 'review'
    && [a.id, b.id].includes(item.application_id)).map(item => item.event_id).sort((x, y) => x - y);
  const listPath = '/reminders/candidates?timezone=Asia%2FSingapore';
  const button = (app, round) => page.getByRole('button').filter({
    hasText: `${app.position_name} · 第 ${round} 轮 · 面试复盘提醒`,
  });
  try {
    assert.deepEqual(ids((await api(listPath)).items), [a1.id, b1.id, b2.id]);
    await page.goto(base + '/?view=reminders');
    await Promise.all([button(a, 1), button(b, 1), button(b, 2)].map(card => card.waitFor({ timeout: 15000 })));
    await button(b, 2).click();
    await page.getByRole('button', { name: '记录这场面试的复盘', exact: true }).click();
    const form = page.getByRole('region', { name: '新建面试复盘' });
    await form.locator('#self_reflection').fill(reflection);
    assert.match(await form.innerText(), /第 2 轮/);
    assert.deepEqual(writes, []);
    assert.deepEqual(unexpected, []);

    let acceptedSave;
    const receipt = page.waitForResponse(response => {
      if (response.url() !== base + '/api' + notePath || response.request().method() !== 'POST') return false;
      acceptedSave = response.ok() ? response : undefined;
      return true;
    }, { timeout: 10000 });
    const followupReads = new Set();
    page.on('request', request => {
      const url = new URL(request.url());
      if (acceptedSave && request.method() === 'GET' && url.origin === new URL(base).origin
        && url.pathname === '/api/reminders/candidates') followupReads.add(request);
    });
    const refresh = page.waitForResponse(response => followupReads.has(response.request()) && response.ok(), { timeout: 10000 });
    const saveDeadline = performance.now() + 10_000;
    saveArmed = true;
    const [saved, refreshed] = await Promise.all([
      receipt, refresh, form.getByRole('button', { name: '保存复盘', exact: true }).click({ timeout: 10000 }),
    ]);
    assert.ok(saved.ok(), `Exact review POST failed with ${saved.status()}`);
    assert.deepEqual(ids((await refreshed.json()).items), [a1.id, b1.id]);

    // Exact review uses the canonical application pane on Board and unmounts
    // Reminders. Global AppShell already refetched before we return to the page.
    // Navigate inside the same document: do not let a reload prove the refresh.
    const marker = 'same-document-exact-debrief';
    await page.evaluate(value => { document.documentElement.dataset.debriefProbe = value; }, marker);
    assert.equal(new URL(page.url()).searchParams.get('view'), 'board');
    await page.goBack({ timeout: Math.max(1, saveDeadline - performance.now()) });
    assert.equal(new URL(page.url()).searchParams.get('view'), 'reminders');
    assert.equal(await page.evaluate(() => document.documentElement.dataset.debriefProbe), marker);
    await waitForMailState(async () => ({
      a: await button(a, 1).isVisible(), b1: await button(b, 1).isVisible(), b2: await button(b, 2).isVisible(),
      loading: await page.getByText('正在加载提醒', { exact: true }).isVisible(),
      error: await page.getByText('提醒加载失败，请重试', { exact: true }).isVisible(),
    }), row => row.a && row.b1 && !row.b2 && !row.loading && !row.error,
    { timeoutMs: Math.max(1, saveDeadline - performance.now()) });
    const [notesA, notesB, persisted] = await Promise.all([api(`/applications/${a.id}/notes`), api(notePath), api(listPath)]);
    assert.deepEqual(notesA, []);
    assert.deepEqual(notesB.map(note => [note.application_event_id, note.self_reflection]), [[b2.id, reflection]]);
    assert.deepEqual(ids(persisted.items), [a1.id, b1.id]);
    assert.deepEqual(writes, ['POST /api' + notePath]);
    assert.deepEqual(unexpected, []);
    return { a: a1.id, b1: b1.id, b2: b2.id, noteId: notesB[0].id, retained: ids(persisted.items), writes: writes.length, saveTriggeredRefresh: true };
  } finally {
    await page.close();
  }
}
