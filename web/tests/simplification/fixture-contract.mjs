/** Plain Node contract check; deliberately not *.test.* so Vitest does not collect node:test. */
import assert from 'node:assert/strict';
import { createFixtureApi } from '../localization/fixture-api.mjs';
import { createSimplificationFixture, knowledgeTitle, referenceTitle, frozenEvidence, allowedWrites, isDomainWriteAttempt, isContentFreePresenceRequest } from './fixture-api.mjs';
// /knowledge/notes has an envelope even when empty. A bare [] is an error,
// not an empty ExperienceMaterialsView (see the production service parser).
const localization = createFixtureApi();
assert.deepEqual(localization.handle('GET', '/api/knowledge/notes'), { status: 200, data: { items: [] } });
assert.deepEqual(localization.unexpected, []);
// Disabled companion life still reports content-free presence through AppShell.
// It must not widen the synthetic domain/model write allowlist.
const presenceApi = createSimplificationFixture();
const presenceBefore = presenceApi.snapshot();
const companionBefore = (await presenceApi.handle('GET', '/api/companion')).data;
const presenceBody = { identity: companionBefore.identity,
  client_id: '22222222-2222-4222-8222-222222222222', sequence: 1,
  visible: true, typing: false, work_running: false, awaiting_approval: false,
  mock_interview: false, quiet: false, failure: false };
const presence = await presenceApi.handle('POST', '/api/companion/presence', presenceBody);
assert.equal(presence.status, 200, 'content-free presence reaches the supported base fixture');
assert.deepEqual(presence.data, { session_id: 'fixture-session', session_quiet: false,
  active: true, blocked: false, blockers: [] });
for (const signal of ['typing', 'work_running', 'awaiting_approval', 'mock_interview', 'quiet', 'failure']) {
  const blocked = await presenceApi.handle('POST', '/api/companion/presence', { ...presenceBody, [signal]: true });
  assert.equal(blocked.status, 200); assert.equal(blocked.data.blocked, true);
}
assert.equal((await presenceApi.handle('POST', '/api/companion/presence', { ...presenceBody, visible: false })).data.blocked, true);
assert.deepEqual(presenceApi.snapshot(), presenceBefore, 'presence cannot mutate domain fixture state');
assert.deepEqual((await presenceApi.handle('GET', '/api/companion')).data, companionBefore, 'presence cannot progress companion life');
assert.deepEqual(presenceApi.unexpected, []);
assert.equal(presenceApi.requests.filter(isDomainWriteAttempt).length, 0, 'valid presence is not a domain write in browser assertions');
assert.equal(presenceApi.requests.filter(request => request.method !== 'GET').length, 8, 'presence remains in raw request evidence');
assert.equal(presenceApi.requests.filter(isContentFreePresenceRequest).length, 8, 'all excluded presence passes the full-request predicate');
await presenceApi.handle('PATCH', '/api/onboarding', { force_open: false });
assert.deepEqual(presenceApi.requests.filter(isDomainWriteAttempt).map(({ method, path }) => ({ method, path })),
  [{ method: 'PATCH', path: '/api/onboarding' }], 'domain writes remain counted beside valid presence');
assert.throws(() => presenceApi.holdNext('POST', '/api/companion/presence'), /unknown write/);
for (const [method, pathname, body] of [
  ['PUT', '/api/companion/presence', presenceBody],
  ['POST', '/api/companion/presence/', presenceBody],
  ['POST', '/api/companion/presence?event=complete_shared', presenceBody],
  ['POST', '/api/companion/presence/events', presenceBody],
  ['POST', '/api/companion/events', { event: 'complete_shared' }],
  ['POST', '/api/companion/bid', {}], ['PUT', '/api/companion/settings', { enabled: true }],
  ['DELETE', '/api/companion', {}], ['POST', '/api/chat', { message: 'No model allowed' }],
  ['POST', '/api/chat/confirm', { approved: true }], ['POST', '/api/pilot/turns', {}],
  ['POST', '/api/proactive/jobs', {}], ['POST', '/api/job-mail/imports', {}],
  ['POST', '/api/companion/presence', { ...presenceBody, message: 'Unexpected content' }],
  ['POST', '/api/companion/presence', { ...presenceBody, identity: 'not-a-uuid' }],
  ['POST', '/api/companion/presence', { ...presenceBody, identity: [presenceBody.identity] }],
  ['POST', '/api/companion/presence', { ...presenceBody, client_id: 'not-a-uuid' }],
  ['POST', '/api/companion/presence', { ...presenceBody, client_id: [presenceBody.client_id] }],
  ['POST', '/api/companion/presence', { ...presenceBody, sequence: 0 }],
  ['POST', '/api/companion/presence', { ...presenceBody, visible: 'true' }],
  ['POST', '/api/companion/presence', { ...presenceBody, typing: undefined }],
  ['POST', '/api/companion/presence', null],
]) {
  const rejected = createSimplificationFixture(), before = rejected.snapshot();
  assert.ok((await rejected.handle(method, pathname, body)).status >= 400, `must reject ${method} ${pathname}`);
  assert.deepEqual(rejected.unexpected, [{ method, path: new URL(pathname, 'http://127.0.0.1').pathname }]);
  assert.deepEqual(rejected.requests.filter(isDomainWriteAttempt), rejected.requests, 'invalid presence and forbidden writes remain counted');
  assert.equal(rejected.requests.some(isContentFreePresenceRequest), false, 'invalid presence never passes the shared full-request guard');
  assert.equal(rejected.requests.every(request => allowedWrites.has(`${request.method} ${request.path}`)
    || isContentFreePresenceRequest(request)), false, 'the browser full-request guard rejects every negative probe');
  assert.deepEqual(rejected.snapshot(), before);
}
const api = createSimplificationFixture({ firstUse: true, materials: true });
const call = (method, pathname, body) => api.handle(method, `http://127.0.0.1${pathname}`, body);
assert.deepEqual((await call('GET', '/api/knowledge/notes')).data, { items: [] });
assert.deepEqual((await call('GET', '/api/knowledge/sources')).data, []);
const defaults = createSimplificationFixture();
assert.deepEqual((await defaults.handle('GET', '/api/knowledge/notes')).data, { items: [] });
assert.deepEqual((await defaults.handle('GET', '/api/knowledge/sources')).data, []);
const populated = createSimplificationFixture({ materials: true });
const captures = (await populated.handle('GET', '/api/knowledge/notes')).data.items;
assert.equal(captures.length, 1);
const capture = captures[0];
assert.equal(capture.title, knowledgeTitle);
assert.equal(capture.origin_kind, 'confirmed_interview_capture');
assert.equal(capture.source_status, 'source_changed');
assert.ok(Number.isSafeInteger(capture.version_id) && capture.version_id > 0);
assert.deepEqual(capture.capture_metadata, {
  source_id: capture.source_id, origin_note_id: 41, application_event_id: 201,
  note_fingerprint: 'synthetic-confirmed-capture', capture_schema_version: 'interview-note-capture-v1',
  captured_at: capture.captured_at,
});
assert.equal(capture.content.blocks[0].evidence_refs[0].excerpt, frozenEvidence);
assert.equal(capture.evidence[0].excerpt, frozenEvidence);
assert.deepEqual((await populated.handle('GET', '/api/knowledge/note-management')).data.items, captures);
const references = (await populated.handle('GET', '/api/knowledge/sources')).data;
assert.equal(references.length, 1);
assert.equal(references[0].title, referenceTitle);
assert.equal(references[0].source_kind, 'markdown');
assert.notEqual(references[0].id, capture.source_id);
assert.deepEqual((await populated.handle('GET', '/api/interview-stories')).data, []);
assert.deepEqual(populated.unexpected, []);
assert.equal((await call('GET', '/api/settings')).data.has_api_key, false);
await call('PATCH', '/api/onboarding', { force_open: false });
await call('PUT', '/api/settings', { model: 'draft', chat_auto_approve_writes: false });
assert.equal((await call('GET', '/api/onboarding')).data.dismissed, true);
const create = { idempotency_key: 'fixture-request', company_name: 'Synthetic', position_name: 'Tester', initial_jd: null };
assert.equal((await call('POST', '/api/applications', create)).data.id, 102);
assert.equal((await call('POST', '/api/applications', create)).data.id, 102);
assert.equal(api.snapshot().applications.length, 1);
const release = api.holdNext('POST', '/api/knowledge/notes/7/lifecycle');
let settled = false;
const pending = call('POST', '/api/knowledge/notes/7/lifecycle', { mutation_id: 'x', confirmed: true, action: 'revise', title: 'new', blocks: [] }).then(value => { settled = true; return value; });
await Promise.resolve(); assert.equal(settled, false);
release({ status: 503, data: { error_code: 'synthetic_uncertain' } });
assert.equal((await pending).status, 503); assert.equal(api.snapshot().note.version_id, 21);
assert.throws(() => api.holdNext('POST', '/api/chat'));
assert.equal((await call('POST', '/api/chat', { message: 'No model allowed' })).status, 501);
assert.equal((await call('GET', '/api/not-defined')).status, 501);
assert.equal(api.unexpected.length, 2);
api.releaseAll();
console.log('PASS strict synthetic fixture contracts; this is not a browser result.');
