import assert from 'node:assert/strict';
import test from 'node:test';
import { createTrainingFixture, EVIDENCE_PATH, ORIGINAL_ANSWER, ORIGIN } from './fixture-api.mjs';
import { classifyRequest } from './network-guard.mjs';
const key = n => `11111111-1111-4111-8111-${String(n).padStart(12, '0')}`;
const record = (n = 1) => ({ action: 'record', expected_revision: 0, idempotency_key: key(n), skill_id: 'idempotency_retry', scenario_id: 'original', answer: ORIGINAL_ANSWER, assistance: 'unknown' });
const post = (api, command) => api.handle('POST', `${ORIGIN}${EVIDENCE_PATH}`, command);

test('reading and opening a plan do not create evidence', () => {
  const api = createTrainingFixture();
  assert.equal(api.handle('GET', `${ORIGIN}/api/interview-practice/plans`).status, 200);
  assert.equal(api.handle('GET', `${ORIGIN}${EVIDENCE_PATH}`).status, 200);
  assert.deepEqual(api.state().records, []);
  assert.equal(api.state().recommendation, null);
});
test('unknown mutations and credentials are denied before delegating to the shared fixture', () => {
  for (const [method, path, body, headers] of [['POST', '/api/chat', {}, {}], ['POST', '/api/job-mail/imports', {}, {}], ['GET', '/api/settings', undefined, { authorization: 'synthetic-not-a-secret' }], ['POST', '/api/companion/presence', { answer: 'must not leave' }, {}]]) {
    const api = createTrainingFixture();
    assert.equal(api.handle(method, `${ORIGIN}${path}`, body, headers).status, 501);
    assert.equal(api.violations.length, 1);
  }
});
test('strict record schema and source answer cannot be forged', () => {
  for (const body of [null, { ...record(), action: '__proto__' }, { ...record(), surprise: 'x' }, { ...record(), skill_id: 'other' }, { ...record(), scenario_id: 'other' }, { ...record(), answer: 'not the original' }, { ...record(), assistance: 'mastered' }, { ...record(), expected_revision: true }]) {
    const api = createTrainingFixture(); assert.equal(post(api, body).status, 422); assert.deepEqual(api.state().records, []);
  }
});
test('a lost response is retried with one receipt, not a duplicate record', () => {
  const api = createTrainingFixture(), command = record(); api.dropNextResponse();
  assert.equal(post(api, command).dropResponse, true);
  assert.equal(post(api, command).status, 200);
  assert.equal(api.state().records.length, 1); assert.equal(api.state().revision, 1);
  assert.equal(post(api, { ...command, assistance: 'hinted' }).status, 409);
});
test('correction updates only the selected record and revision guard rejects stale commands', () => {
  const api = createTrainingFixture(); post(api, record());
  const command = { action: 'correct', expected_revision: 1, idempotency_key: key(2), record_id: 1, answer: ORIGINAL_ANSWER, assistance: 'hinted' };
  assert.equal(post(api, command).status, 200); assert.equal(api.state().records[0].assistance, 'hinted');
  assert.equal(post(api, { ...command, idempotency_key: key(3) }).status, 409);
  assert.equal(api.state().revision, 2);
});
test('retest produces citations but never an automatic mastery assertion', () => {
  const api = createTrainingFixture(); post(api, record());
  assert.equal(post(api, { ...record(2), expected_revision: 1, scenario_id: 'retry_timeout', answer: 'SYNTHETIC retest answer', assistance: 'independent' }).status, 200);
  assert.deepEqual(api.state().recommendation.evidence_record_ids, [1, 2]);
  assert.equal(api.state().recommendation.scenario_id, 'concurrent_retry');
  assert.equal('mastered' in api.state().recommendation, false);
});
test('source pause blocks new content but withdrawal erases evidence and old replay cannot resurrect it', () => {
  const api = createTrainingFixture(), command = record(); post(api, command); api.pauseSource();
  assert.equal(api.state().recommendation, null);
  assert.equal(post(api, { ...record(2), expected_revision: 1 }).status, 409);
  assert.equal(post(api, { action: 'withdraw', expected_revision: 1, idempotency_key: key(3), record_id: 1 }).status, 200);
  assert.equal(api.state().records[0].answer, ''); assert.equal(api.state().records[0].withdrawn, true);
  assert.equal(post(api, command).data.records[0].answer, '');
  assert.equal(api.handle('GET', `${ORIGIN}/api/interview-practice/plans`).data[0].response_text, ORIGINAL_ANSWER);
});
test('browser router blocks external URLs, credentialed URLs, unsafe assets and non-GET assets', () => {
  const dist = '/tmp/synthetic-dist';
  for (const [method, url] of [['GET', 'https://example.invalid/api/settings'], ['GET', 'http://user:pass@127.0.0.1:4175/'], ['GET', `${ORIGIN}/%2e%2e%2fsecret`], ['GET', `${ORIGIN}/%5csecret`], ['POST', `${ORIGIN}/index.html`]]) assert.ok(classifyRequest(method, url, dist).blocked);
  assert.deepEqual(classifyRequest('GET', `${ORIGIN}${EVIDENCE_PATH}`, dist), { api: true });
  assert.equal(classifyRequest('GET', `${ORIGIN}/?view=interview`, dist).filename, `${dist}/index.html`);
});

test('a second active original record conflicts; explicit withdrawal allows a new one', () => {
  const api = createTrainingFixture(); post(api, record());
  assert.equal(post(api, { ...record(2), expected_revision: 1 }).status, 409);
  assert.equal(api.state().records.length, 1);
  assert.equal(post(api, { action: 'withdraw', expected_revision: 1, idempotency_key: key(3), record_id: 1 }).status, 200);
  assert.equal(post(api, { ...record(4), expected_revision: 2 }).status, 200);
  assert.equal(api.state().records.filter(item => !item.withdrawn).length, 1);
});

test('12 active slots allow correction and withdrawal releases capacity without erasing receipts', () => {
  const api = createTrainingFixture();
  for (let i = 1; i <= 12; i++) assert.equal(post(api, { ...record(i), expected_revision: i - 1, scenario_id: 'retry_timeout', answer: 'Synthetic answer' }).status, 200);
  assert.equal(post(api, { ...record(13), expected_revision: 12 }).status, 409);
  assert.equal(post(api, { action: 'correct', expected_revision: 12, idempotency_key: key(14), record_id: 1, answer: 'Corrected synthetic answer', assistance: 'hinted' }).status, 200);
  assert.equal(post(api, { action: 'withdraw', expected_revision: 13, idempotency_key: key(15), record_id: 1 }).status, 200);
  assert.equal(post(api, { ...record(16), expected_revision: 14 }).status, 200);
  assert.equal(api.state().records.length, 13);
  assert.equal(api.state().records.filter(item => !item.withdrawn).length, 12);
  const replay = post(api, { ...record(1), expected_revision: 0, scenario_id: 'retry_timeout', answer: 'Synthetic answer' });
  assert.equal(replay.status, 200);
  assert.equal(replay.data.records[0].withdrawn, true);
  assert.equal(replay.data.records[0].answer, '');
});

test('matching assistance is a neutral conditions comparison, not a change claim', () => {
  const api = createTrainingFixture(); post(api, record());
  post(api, { ...record(2), expected_revision: 1, scenario_id: 'retry_timeout', answer: 'Synthetic comparison answer' });
  assert.match(api.state().recommendation.comparison, /作答条件对照/);
  assert.doesNotMatch(api.state().recommendation.comparison, /条件变化/);
  assert.deepEqual(api.state().recommendation.evidence_record_ids, [1, 2]);
});
