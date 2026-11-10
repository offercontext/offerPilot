import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
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

const choose = choice_id => ({ rubric_version: 'idempotency_retry.v1', question_id: 'retry_timeout.v1', choice_id });
test('six historical scenarios are recommended once, withdrawal does not reset exhausted coverage', () => {
  const api = createTrainingFixture(); post(api, record());
  const visited = [];
  for (let i = 0; i < 6; i++) {
    const next = api.state().recommendation.scenario_id;
    assert.ok(!visited.includes(next)); visited.push(next);
    assert.equal(post(api, { ...record(i + 2), expected_revision: i + 1, scenario_id: next, answer: 'Synthetic pool answer' }).status, 200);
  }
  assert.equal(api.state().pool.exhausted, true); assert.equal(api.state().recommendation, null);
  assert.equal(post(api, { action: 'withdraw', expected_revision: 7, idempotency_key: key(8), record_id: 2 }).status, 200);
  assert.equal(api.state().pool.exhausted, true); assert.equal(api.state().pool.practiced_scenario_ids.length, 6);
  assert.equal(post(api, { ...record(9), expected_revision: 8, scenario_id: visited[0], answer: 'Synthetic explicit retest' }).status, 200);
});
test('opposite choices change selected-option feedback, never assess identical text', () => {
  const api = createTrainingFixture();
  const command = { ...record(), scenario_id: 'retry_timeout', answer: 'Definitely wrong free text, ignored by the option rule.', decision_answer: choose('new_key_retry') };
  assert.equal(post(api, command).data.records[0].feedback.outcome, 'contradicted');
  const incorrectStep = api.state().records[0].feedback.next_step;
  const corrected = post(api, { action: 'correct', expected_revision: 1, idempotency_key: key(2), record_id: 1, answer: command.answer, assistance: 'unknown', decision_answer: choose('query_same_key') });
  assert.equal(corrected.data.records[0].feedback.outcome, 'supported');
  assert.equal(corrected.data.records[0].feedback.text_assessment, 'not_evaluated');
  assert.notEqual(corrected.data.records[0].feedback.next_step, incorrectStep);
  const cleared = post(api, { action: 'correct', expected_revision: 2, idempotency_key: key(3), record_id: 1, answer: command.answer, assistance: 'unknown', decision_answer: null });
  assert.equal(cleared.data.records[0].feedback.outcome, 'abstain');
  assert.equal(cleared.data.records[0].decision_answer, null);
});
test('missing and uncertain choices abstain; invalid rubric/question/choice never writes', () => {
  for (const decision_answer of [undefined, null, choose('uncertain')]) {
    const api = createTrainingFixture(); const result = post(api, { ...record(), scenario_id: 'retry_timeout', answer: 'Synthetic free text', ...(decision_answer === undefined ? {} : { decision_answer }) });
    assert.equal(result.status, 200); assert.equal(result.data.records[0].feedback.outcome, 'abstain');
  }
  for (const decision_answer of [{ ...choose('query_same_key'), rubric_version: 'v999' }, { ...choose('query_same_key'), question_id: 'concurrent_retry.v1' }, choose('bogus')]) {
    const api = createTrainingFixture(); assert.equal(post(api, { ...record(), scenario_id: 'retry_timeout', decision_answer }).status, 422); assert.equal(api.state().revision, 0);
  }
  assert.equal(post(createTrainingFixture(), { ...record(), decision_answer: choose('query_same_key') }).status, 422);
});
test('structured unknown-result replay remains identical and withdrawal clears choice without resurrection', () => {
  const api = createTrainingFixture(), command = { ...record(), scenario_id: 'retry_timeout', decision_answer: choose('new_key_retry') };
  api.dropNextResponse(); assert.equal(post(api, command).dropResponse, true);
  assert.equal(post(api, command).data.records.length, 1);
  assert.equal(post(api, { ...command, decision_answer: choose('query_same_key') }).status, 409);
  assert.equal(post(api, { action: 'withdraw', expected_revision: 1, idempotency_key: key(2), record_id: 1 }).status, 200);
  const replay = post(api, command).data.records[0];
  assert.equal(replay.decision_answer, null); assert.equal(replay.answer, ''); assert.equal(replay.feedback.outcome, 'abstain');
});
test('legacy omitted decision and explicit null have the same idempotency fingerprint', () => {
  const api = createTrainingFixture(), command = record(); post(api, command);
  assert.equal(post(api, { ...command, decision_answer: null }).status, 200);
  assert.equal(api.state().revision, 1);
});

test('synthetic questions are pinned to the backend scenario definition', () => {
  const snapshot = JSON.parse(readFileSync(new URL('./questions.json', import.meta.url), 'utf8'));
  const source = readFileSync(new URL('../../../src/offerpilot/training_evidence/scenarios.py', import.meta.url));
  assert.equal(snapshot.source_sha256, createHash('sha256').update(source).digest('hex'));
});
