import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createCompanionFixture, initialSnapshot, ORIGIN } from './fixture-api.mjs';
import { classifyRequest } from './network-guard.mjs';

function event(api, name, overrides = {}) {
  const snapshot = api.snapshot();
  return { identity: snapshot.identity, expected_revision: snapshot.revision, command_id: randomUUID(),
    event: name, activity_id: snapshot.state.activity.id, ...overrides };
}
const post = (api, body) => api.handle('POST', `${ORIGIN}/api/companion/events`, body);
const unchanged = (api, initial) => {
  assert.deepEqual(api.snapshot().state, initial.state);
  assert.deepEqual(api.snapshot().shared_participation, initial.shared_participation);
  assert.equal(api.snapshot().revision, initial.revision);
};

test('reads, presence and lifecycle commands never complete the synthetic shared step', () => {
  const api = createCompanionFixture(), initial = api.snapshot();
  for (const name of ['app_open', 'user_interaction', 'chat_end', 'work_completed']) {
    const body = event(api, name), result = post(api, body);
    assert.equal(result.status, 200);
    assert.deepEqual(result.data.receipt, { command_id: body.command_id, revision: initial.revision });
    assert.equal(result.data.changed, false);
  }
  const result = api.handle('GET', `${ORIGIN}/api/companion`);
  result.data.state.activity.step_index = 99;
  unchanged(api, initial);
  assert.deepEqual(api.violations, []);
});

test('pause/resume and withdrawal preserve exact activity, step, prior completed records and paused state', () => {
  const api = createCompanionFixture(), initial = api.snapshot();
  for (const action of ['pause', 'resume', 'pause', 'withdraw_shared']) {
    api.permit(action);
    assert.equal(post(api, event(api, action)).status, 200);
  }
  const next = api.snapshot();
  assert.equal(next.state.activity.status, 'paused');
  assert.equal(next.state.activity.paused_from, 'active');
  assert.equal(next.state.activity.step_index, initial.state.activity.step_index);
  assert.equal(next.state.activity.id, initial.state.activity.id);
  assert.equal(next.shared_participation[1].status, 'abandoned');
  assert.equal(next.shared_participation[1].completed_at, null);
  assert.deepEqual(next.shared_participation[0], initial.shared_participation[0]);
  assert.equal(next.revision, initial.revision + 4);
});

test('lost response retry returns exact old receipt before CAS, with one mutation and no second permit', () => {
  const api = createCompanionFixture(); api.permit('pause'); api.dropNextResponse('pause');
  const body = event(api, 'pause'), first = post(api, body), retry = post(api, { ...body });
  assert.equal(first.dropResponse, true);
  assert.equal(retry.dropResponse, undefined);
  assert.equal(retry.replayed, true);
  assert.deepEqual(first.data, retry.data);
  assert.equal(api.snapshot().revision, initialSnapshot().revision + 1);
  assert.equal(api.receipts().length, 1);
  assert.equal(post(api, { ...body, event: 'resume' }).status, 409);
  assert.match(api.violations.at(-1), /command_id_conflict/);
});

test('stale window and wrong identity cannot overwrite the newer snapshot', () => {
  const api = createCompanionFixture(), stale = event(api, 'pause'); api.permit('pause');
  assert.equal(post(api, event(api, 'pause')).status, 200);
  const before = api.snapshot(); api.permit('pause'); api.permit('resume');
  assert.equal(post(api, stale).status, 409);
  assert.equal(post(api, event(api, 'resume', { identity: randomUUID() })).status, 409);
  assert.deepEqual(api.snapshot(), before);
  assert.deepEqual(api.violations, []);
});

test('explicit completion has a positive transition, not unconditional success', () => {
  const api = createCompanionFixture(); api.permit('complete_shared');
  const body = event(api, 'complete_shared');
  assert.equal(post(api, body).status, 200);
  assert.equal(api.snapshot().state.activity.step_index, 0);
  assert.deepEqual(api.snapshot().state.activity.participants, ['companion', 'user']);
  assert.equal(api.snapshot().shared_participation[1].status, 'completed');
  api.permit('complete_shared');
  assert.equal(post(api, event(api, 'complete_shared')).status, 422);
});

test('settings off preserves interests, activity and prior participation', () => {
  const api = createCompanionFixture(), before = api.snapshot(); api.permit('settings');
  const result = api.handle('PUT', `${ORIGIN}/api/companion/settings`, {
    identity: before.identity, expected_revision: before.revision, command_id: randomUUID(),
    settings: { enabled: false, hide_status: true, proactivity: 'off' },
  });
  assert.equal(result.status, 200);
  assert.deepEqual(api.snapshot().state, before.state);
  assert.deepEqual(api.snapshot().shared_participation, before.shared_participation);
});

test('presence is sequence-bound and blocks bids across visible clients without writes', () => {
  const api = createCompanionFixture(), before = api.snapshot();
  const presence = { identity: before.identity, client_id: randomUUID(), sequence: 1,
    visible: true, typing: true, work_running: false, awaiting_approval: false, mock_interview: false, quiet: false, failure: false };
  const send = value => api.handle('POST', `${ORIGIN}/api/companion/presence`, value);
  assert.equal(send(presence).data.blocked, true);
  assert.equal(send({ ...presence, typing: false }).data.blocked, true);
  const bid = () => api.handle('POST', `${ORIGIN}/api/companion/bid`, {
    identity: before.identity, expected_revision: before.revision, client_id: presence.client_id,
  }).data.item;
  assert.equal(bid(), null);
  assert.equal(send({ ...presence, sequence: 2, typing: false }).data.blocked, false);
  assert.equal(bid().source_revision, before.revision);
  assert.equal(bid(), null);
  unchanged(api, before);
});

test('malformed or unexpected companion commands fail closed rather than returning snapshots', () => {
  for (const transform of [
    value => ({ ...value, command_id: 'not-a-uuid' }),
    value => ({ ...value, expected_revision: '7' }),
    value => ({ ...value, unexpected: true }),
    value => ({ ...value, activity_id: randomUUID() }),
    value => ({ ...value, wish_id: randomUUID() }),
    value => ({ ...value, event: 'join' }),
    value => ({ ...value, event: 'reset' }),
    value => null,
  ]) {
    const api = createCompanionFixture(), before = api.snapshot();
    assert.ok(post(api, transform(event(api, 'pause'))).status >= 400);
    assert.ok(api.violations.length);
    assert.deepEqual(api.snapshot(), before);
  }
  const api = createCompanionFixture();
  assert.equal(post(api, event(api, 'pause')).status, 501, 'even known writes must be armed by the scenario');
});

test('no backend, model, reset, email, provider or foreign-origin fallback is permitted', () => {
  for (const [method, url] of [
    ['POST', `${ORIGIN}/api/chat`], ['POST', `${ORIGIN}/api/pilot/turns`],
    ['POST', `${ORIGIN}/api/job-mail/imports`], ['DELETE', `${ORIGIN}/api/companion`],
    ['GET', `${ORIGIN}/api/provider-secret`], ['POST', 'https://example.invalid/api/companion/events'],
  ]) {
    const api = createCompanionFixture();
    assert.equal(api.handle(method, url, {}).status, 501);
    assert.ok(api.violations.length);
  }
  const api = createCompanionFixture();
  assert.equal(api.handle('GET', `${ORIGIN}/api/settings`, undefined, { Authorization: 'synthetic' }).status, 501);
  assert.equal(api.handle('GET', `${ORIGIN}/api/applications`).status, 200);
});

test('asset routing rejects external network, non-GET assets and encoded path escapes', () => {
  const dist = '/tmp/synthetic-dist';
  assert.deepEqual(classifyRequest('GET', `${ORIGIN}/`, dist), { filename: `${dist}/index.html` });
  assert.deepEqual(classifyRequest('GET', `${ORIGIN}/api/companion`, dist), { api: true });
  for (const [method, url] of [
    ['GET', 'https://example.invalid/script.js'], ['POST', `${ORIGIN}/asset.js`],
    ['GET', `${ORIGIN}/assets/%2f../private`], ['GET', `${ORIGIN}/assets/%5cprivate`],
    ['GET', `${ORIGIN}/assets/%00file`], ['GET', `${ORIGIN}/assets/%zz`],
    ['GET', 'http://name:password@127.0.0.1:4175/'], ['GET', 'not-a-url'],
  ]) assert.ok(classifyRequest(method, url, dist).blocked, url);
});


test('an unexpected write cannot hide behind a stale CAS rejection', () => {
  const api = createCompanionFixture();
  assert.equal(post(api, event(api, 'pause', { expected_revision: 0 })).status, 501);
  assert.deepEqual(api.violations, ['unarmed_command:pause']);
  assert.equal(post(api, event(api, 'abandon', { expected_revision: 0 })).status, 501);
  assert.match(api.violations.at(-1), /forbidden_event:abandon/);
});

test('disabled life rejects otherwise armed activity changes', () => {
  const api = createCompanionFixture(), initial = api.snapshot(); api.permit('settings');
  assert.equal(api.handle('PUT', `${ORIGIN}/api/companion/settings`, {
    identity: initial.identity, expected_revision: initial.revision, command_id: randomUUID(),
    settings: { ...initial.settings, enabled: false },
  }).status, 200);
  api.permit('pause');
  assert.equal(post(api, event(api, 'pause')).status, 409);
  assert.deepEqual(api.snapshot().state, initial.state);
  assert.deepEqual(api.violations, []);
});


test('execution evidence records the state actually returned at each ordered read', () => {
  const api = createCompanionFixture();
  const read = () => api.handle('GET', `${ORIGIN}/api/chat/conversations/401/execution`);
  assert.equal(read().data.execution, null);
  const boundary = api.requests.at(-1).index;
  api.setWork('running');
  assert.equal(read().data.execution.state, 'running');
  const running = api.requests.at(-1);
  assert.ok(running.index > boundary);
  assert.equal(running.execution.state, 'running');
  api.setWork('idle'); read();
  assert.equal(api.requests.at(-1).execution, null);
  assert.equal(running.execution.state, 'running', 'later fixture changes cannot rewrite earlier execution evidence');
});


test('persona default read preserves life and does not permit persona writes or lookalikes', () => {
  const api = createCompanionFixture(), before = api.snapshot();
  const read = api.handle('GET', `${ORIGIN}/api/companion/persona`);
  assert.equal(read.status, 200);
  assert.equal(read.data.spec.display_name, 'Haru');
  read.data.spec.display_name = 'Mutated client copy';
  assert.equal(api.handle('GET', `${ORIGIN}/api/companion/persona`).data.spec.display_name, 'Haru');
  assert.deepEqual(api.snapshot(), before);
  assert.deepEqual(api.violations, []);
  for (const [method, path] of [['POST', '/api/companion/persona/mutations'],
    ['GET', '/api/companion/persona?unknown=1'], ['GET', '/api/companion/persona/unknown']]) {
    assert.notEqual(api.handle(method, `${ORIGIN}${path}`, {}).status, 200);
  }
  assert.deepEqual(api.snapshot(), before);
});
