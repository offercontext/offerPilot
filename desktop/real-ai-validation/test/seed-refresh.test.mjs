import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { refreshSyntheticProfile } from '../seed-refresh.mjs';
const fixture = { applicationId: 1, eventId: 2, resumeId: 3, offerId: 4 };
const paths = ['/api/applications', '/api/application-events', '/api/resumes', '/api/offers'];
function setup({ badRows = false, badStatus = false, failReload = false, paid = false, spendOnReload = false, noFrame = false, stallBody = false } = {}) {
  const events = []; let spent = paid; const frame = {};
  const broker = { snapshot: () => ({ sentRequests: spent ? 1 : 0, requests: spent ? [{}] : [], active: false, closed: false }) };
  const page = new EventEmitter();
  page.url = () => 'http://127.0.0.1:12345/'; page.mainFrame = () => frame;
  const waits = [];
  page.waitForResponse = predicate => new Promise((resolve, reject) => waits.push({ predicate, resolve, reject }));
  const emit = (path, index, method = 'GET') => {
    const request = { frame: () => frame, method: () => method, url: () => page.url().slice(0, -1) + path };
    const response = { request: () => request, url: request.url, status: () => badStatus ? 503 : 200,
      json: async () => stallBody ? new Promise(() => {}) : badRows ? [{ id: 99 }] : [{ id: index + 1 }] };
    page.emit('request', request);
    for (const wait of waits) if (wait.predicate(response)) { events.push(path); wait.resolve(response); }
  };
  page.reload = async () => {
    events.push('reload');
    paths.forEach((p, i) => emit(p, i)); // Same-origin stale responses must not pass.
    assert.equal(events.length, 1);
    if (failReload) { for (const wait of waits) wait.reject(new Error('private-error')); throw new Error('private-error'); }
    page.emit('framenavigated', frame);
    if (noFrame) page.emit('request', { frame() { throw new Error('private-no-frame'); } });
    paths.forEach((p, i) => emit(p, i, 'POST')); assert.equal(events.length, 1);
    paths.forEach((p, i) => emit(p, i)); spent = spendOnReload;
  };
  page.getByRole = (role, options) => { assert.equal(role, 'navigation'); assert.equal(options.name, '主导航');
    return { waitFor: async () => events.push('navigation-visible') }; };
  return { page, broker, events };
}
test('one pre-arm normal reload verifies all four fresh product GETs; stale and POST cannot satisfy', async () => {
  const { page, broker, events } = setup();
  await refreshSyntheticProfile(page, broker, fixture, Date.now() + 60000);
  assert.deepEqual(events, ['reload', ...paths, 'navigation-visible']);
  assert.equal(page.listenerCount('request'), 0); assert.equal(page.listenerCount('framenavigated'), 0);
});
for (const options of [{ badRows: true }, { badStatus: true }, { failReload: true }, { spendOnReload: true }, { noFrame: true }]) {
  test(`fresh profile reload fails closed ${Object.keys(options)[0]}`, async () => {
    const { page, broker } = setup(options);
    await assert.rejects(() => refreshSyntheticProfile(page, broker, fixture, Date.now() + 60000),
      error => ['SYNTHETIC_SETUP_FAILED', 'SYNTHETIC_PROFILE_INVALID'].includes(error.safeCode) && !error.message.includes('private'));
    assert.equal(page.listenerCount('request'), 0); assert.equal(page.listenerCount('framenavigated'), 0);
  });
}
test('no reload after any provider admission or on invalid identity/deadline', async () => {
  for (const options of [{ paid: true }, {}, {}]) {
    const { page, broker, events } = setup(options);
    if (!options.paid) options.bad = true;
    await assert.rejects(() => refreshSyntheticProfile(page, broker, options.bad ? { ...fixture, eventId: 0 } : fixture, Date.now() - 1));
    assert.deepEqual(events, []);
  }
});

test('stalled response body is bounded by the same suite deadline and removed listeners', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const { page, broker } = setup({ stallBody: true });
  const pending = refreshSyntheticProfile(page, broker, fixture, Date.now() + 100); pending.catch(() => {});
  await new Promise(setImmediate); t.mock.timers.tick(100);
  await assert.rejects(pending, { safeCode: 'SYNTHETIC_SETUP_FAILED' });
  assert.equal(page.listenerCount('request'), 0);
});
test('late navigation visibility gets only remaining phase time, never another full timeout', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const { page, broker } = setup(); const reload = page.reload;
  page.reload = async (...args) => { await reload(...args); t.mock.timers.tick(90); };
  let givenTimeout;
  page.getByRole = () => ({ waitFor: ({ timeout }) => { givenTimeout = timeout; return new Promise(() => {}); } });
  const pending = refreshSyntheticProfile(page, broker, fixture, Date.now() + 100); pending.catch(() => {});
  await new Promise(setImmediate); assert.equal(givenTimeout, 10); t.mock.timers.tick(10);
  await assert.rejects(pending, { safeCode: 'SYNTHETIC_SETUP_FAILED' });
  assert.equal(page.listenerCount('framenavigated'), 0);
});

test('an invalid request observed during final navigation wait cannot pass refresh', async () => {
  const { page, broker } = setup();
  page.getByRole = () => ({ waitFor: async () => { page.emit('request', { frame() { throw new Error('private-late-frame'); } }); } });
  await assert.rejects(() => refreshSyntheticProfile(page, broker, fixture, Date.now() + 1000),
    { safeCode: 'SYNTHETIC_SETUP_FAILED' });
  assert.equal(page.listenerCount('request'), 0);
});
