import assert from 'node:assert/strict';
import { TASK_PANEL_ORIGIN, createTaskPanelFixture } from './task-panel-fixture.mjs';
const taskPanelFixtureResponse = createTaskPanelFixture().handle;
const persona = taskPanelFixtureResponse('GET', '/api/companion/persona');
assert.equal(persona?.status, 200, 'default persona must be an explicit synthetic read');
assert.equal(persona.data.spec.display_name, 'Haru');
assert.equal(persona.data.revision, 0);
assert.ok(Array.isArray(persona.data.presets));
const snapshot = taskPanelFixtureResponse('GET', '/api/companion');
assert.equal(snapshot?.status, 200, 'normal life read must return a valid synthetic snapshot');
assert.match(snapshot.data.identity, /^[0-9a-f-]{36}$/);
assert.equal(snapshot.data.revision, 8);
assert.deepEqual(snapshot.data.settings, { enabled: true, proactivity: 'off', hide_status: false });
assert.equal(snapshot.data.state.activity.status, 'waiting_shared');
assert.equal(snapshot.data.state.activity.template_id, 'paper_boat');
assert.equal(snapshot.data.state.wish, null);
assert.deepEqual(snapshot.data.recent_events, []);
assert.equal(snapshot.data.shared_participation.length, 2);
assert.deepEqual(snapshot.data.presence, { session_id: 'synthetic-session', session_quiet: false, active: true, blocked: false, blockers: [] });
const signals = { visible: true, typing: false, work_running: false, awaiting_approval: false, mock_interview: false, quiet: false, failure: false };
const presenceBody = { identity: snapshot.data.identity, client_id: '33333333-3333-4333-8333-333333333333', sequence: 1, ...signals };
const presence = () => taskPanelFixtureResponse('POST', '/api/companion/presence', presenceBody);
assert.deepEqual(presence(), { status: 200, data: { session_id: 'synthetic-session', session_quiet: false, active: true, blocked: false, blockers: [] } });
for (const signal of Object.keys(signals).filter(key => key !== 'visible')) {
  const result = taskPanelFixtureResponse('POST', '/api/companion/presence', { ...presenceBody, sequence: ++presenceBody.sequence, [signal]: true });
  assert.equal(result.status, 200);
  assert.equal(result.data.blocked, true);
  assert.deepEqual(result.data.blockers, [signal]);
}
assert.equal(taskPanelFixtureResponse('POST', '/api/companion/presence', { ...presenceBody, sequence: ++presenceBody.sequence, visible: false }).data.blocked, true);
const afterPresence = taskPanelFixtureResponse('GET', '/api/companion');
assert.deepEqual({ ...afterPresence.data, presence: snapshot.data.presence }, snapshot.data, 'presence must not progress life or modify business state');
for (const body of [null, [], {}, { ...presenceBody, identity: 'bad' }, { ...presenceBody, identity: presenceBody.client_id },
  { ...presenceBody, client_id: ['33333333-3333-4333-8333-333333333333'] }, { ...presenceBody, client_id: 'bad' },
  { ...presenceBody, sequence: 0 }, { ...presenceBody, sequence: -1 }, { ...presenceBody, sequence: 1.5 },
  { ...presenceBody, sequence: Number.MAX_SAFE_INTEGER + 1 }, { ...presenceBody, visible: 'true' },
  { ...presenceBody, typing: undefined }, { ...presenceBody, message: 'No content allowed' }]) {
  assert.equal(taskPanelFixtureResponse('POST', '/api/companion/presence', body), undefined);
}
for (const method of ['GET', 'PUT', 'PATCH', 'DELETE']) assert.equal(taskPanelFixtureResponse(method, '/api/companion/presence', presenceBody), undefined);
for (const route of ['/api/companion/presence/', '/api/companion/presence?event=complete_shared', '/api/companion/presence#fragment',
  'https://example.invalid/api/companion/presence', 'http://user:secret@127.0.0.1:5174/api/companion/presence', '/api/companion/events', '/api/companion/bid']) {
  assert.equal(taskPanelFixtureResponse('POST', route, presenceBody), undefined, route);
}
const wakeBody = { identity: snapshot.data.identity, expected_revision: snapshot.data.revision,
  command_id: '44444444-4444-4444-8444-444444444444', event: 'app_open', activity_id: snapshot.data.state.activity.id };
const beforeWake = taskPanelFixtureResponse('GET', '/api/companion');
const wake = taskPanelFixtureResponse('POST', '/api/companion/events', wakeBody);
assert.equal(wake.status, 200);
assert.equal(wake.data.changed, false);
assert.equal(wake.data.receipt.command_id, wakeBody.command_id);
assert.equal(taskPanelFixtureResponse('POST', '/api/companion/events', wakeBody).replayed, true, 'wake retry uses existing synthetic receipt');
assert.equal(taskPanelFixtureResponse('POST', '/api/companion/events', { ...wakeBody, event: 'chat_end' }).status, 409, 'different wake cannot reuse command id');
assert.equal(taskPanelFixtureResponse('POST', '/api/companion/events', { ...wakeBody, event: 'chat_end', command_id: '55555555-5555-4555-8555-555555555555' }).status, 200);
assert.equal(taskPanelFixtureResponse('POST', '/api/companion/events', { ...wakeBody, expected_revision: 0, command_id: '66666666-6666-4666-8666-666666666666' }).status, 409, 'valid stale wake follows synthetic CAS, not a business write');
assert.deepEqual(taskPanelFixtureResponse('GET', '/api/companion'), beforeWake, 'layout wakes must not progress the waiting shared step');
for (const body of [null, [], {}, { ...wakeBody, event: 'complete_shared' }, { ...wakeBody, event: 'work_completed' },
  { ...wakeBody, event: 'user_interaction' }, { ...wakeBody, event: 'pause' }, { ...wakeBody, event: ['app_open'] },
  { ...wakeBody, command_id: ['44444444-4444-4444-8444-444444444444'] }, { ...wakeBody, command_id: 'bad' },
  { ...wakeBody, identity: '33333333-3333-4333-8333-333333333333' }, { ...wakeBody, expected_revision: -1 },
  { ...wakeBody, expected_revision: 1.5 }, { ...wakeBody, expected_revision: Number.MAX_SAFE_INTEGER + 1 },
  { ...wakeBody, activity_id: undefined }, { ...wakeBody, activity_id: 'wrong' },
  { ...wakeBody, wish_id: 'unexpected' }, { ...wakeBody, content: 'No content allowed' }]) {
  assert.equal(taskPanelFixtureResponse('POST', '/api/companion/events', body), undefined);
}
for (const method of ['GET', 'PUT', 'PATCH', 'DELETE']) assert.equal(taskPanelFixtureResponse(method, '/api/companion/events', wakeBody), undefined);
for (const route of ['/api/companion/events/', '/api/companion/events?event=app_open', '/api/companion/events#fragment',
  'https://example.invalid/api/companion/events', 'http://user:secret@127.0.0.1:5174/api/companion/events', '/api/chat']) {
  assert.equal(taskPanelFixtureResponse('POST', route, wakeBody), undefined, route);
}
assert.equal(createTaskPanelFixture().handle('POST', '/api/companion/events', wakeBody).replayed, undefined, 'each viewport owns a fresh lifecycle receipt ledger');
assert.equal(taskPanelFixtureResponse('PUT', '/api/companion/settings', {
  identity: snapshot.data.identity, expected_revision: snapshot.data.revision,
  command_id: '77777777-7777-4777-8777-777777777777', settings: { enabled: true, proactivity: 'default', hide_status: false },
}), undefined, 'internal setup must not expose browser settings writes');
assert.equal(taskPanelFixtureResponse('POST', '/api/companion/bid', {
  identity: snapshot.data.identity, expected_revision: snapshot.data.revision, client_id: presenceBody.client_id,
}), undefined, 'layout transport never grants invitation claims');
assert.equal(taskPanelFixtureResponse('GET', '/api/companion').data.settings.proactivity, 'off');
for (const route of ['/api/companion', '/api/companion/persona']) {
  assert.deepEqual(taskPanelFixtureResponse('GET', TASK_PANEL_ORIGIN + route), taskPanelFixtureResponse('GET', route));
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']) assert.equal(taskPanelFixtureResponse(method, route), undefined, `${method} ${route}`);
  for (const alias of [`${route}/`, `${route}?mutate=true`, `${route}#fragment`, `https://example.invalid${route}`, `http://user:secret@127.0.0.1:5174${route}`]) {
    assert.equal(taskPanelFixtureResponse('GET', alias), undefined, alias);
  }
}
for (const [method, route] of [['POST', '/api/companion/presence'], ['POST', '/api/companion/events'], ['POST', '/api/companion/bid'], ['POST', '/api/companion/persona/mutations'], ['GET', '/api/companion/persona/revisions'], ['POST', '/api/chat'], ['GET', '/api/provider-secret']]) {
  assert.equal(taskPanelFixtureResponse(method, route), undefined, `${method} ${route}`);
}
for (const route of ['/api/offers/7/negotiation/proposals', '/api/offers/comparison-dimensions', '/api/offers/7/comparison-values']) {
  assert.deepEqual(taskPanelFixtureResponse('GET', route), { status: 200, data: [] });
  assert.equal(taskPanelFixtureResponse('POST', route), undefined);
}
persona.data.spec.display_name = 'changed only in this response';
assert.equal(taskPanelFixtureResponse('GET', '/api/companion/persona').data.spec.display_name, 'Haru', 'responses must not share mutable state');
console.log('Desktop layout synthetic read, presence, and explicit lifecycle wake contract passed.');
