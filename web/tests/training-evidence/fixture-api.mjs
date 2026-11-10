/** Strict synthetic UI fixture, not a substitute for backend persistence tests. */
import { readFileSync } from 'node:fs';
import { createFixtureApi, sampleApplication } from '../localization/fixture-api.mjs';
export const ORIGIN = 'http://127.0.0.1:4175';
export const EVIDENCE_PATH = '/api/interview-practice/plans/808/training-evidence';
export const ORIGINAL_ANSWER = 'SYNTHETIC: use a unique request key and check the saved receipt before retrying.';
export const PLAN = {
  id: 808, origin_contract: 'confirmed_readiness_signal_v1', application_id: 101,
  application_event_id: 201, target_application_event_id: 202, readiness_signal_version_id: 901,
  interview_note_id: 701, proposal_id: 702, focus_id: 'synthetic-idempotency',
  company_name: sampleApplication.company_name, position_name: sampleApplication.position_name,
  drill_kind: 'difficulty_breakdown', title: 'SYNTHETIC · Idempotency practice', observation: 'Synthetic source observation',
  reason: 'Synthetic confirmed practice focus', prompt: 'Explain safe retries.', source_path: '/difficulty_points',
  source_excerpt: 'SYNTHETIC: retry behavior needed clarification.', source_fingerprint: `sha256:${'a'.repeat(64)}`,
  target_fingerprint: `sha256:${'b'.repeat(64)}`, source_status: null, practice_state: 'completed', status: 'completed',
  revision: 2, response_text: ORIGINAL_ANSWER, reflection_text: '', self_assessment: 'clearer',
  created_at: '2026-11-09T00:00:00Z', completed_at: '2026-11-09T00:01:00Z',
};
const canonical = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
const copy = value => structuredClone(value);
const validKeys = (body, names) => body && typeof body === 'object' && !Array.isArray(body)
  && Object.keys(body).sort().join(',') === [...names].sort().join(',');
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const { SCENARIOS: scenarios, POOL_IDS, RULES, GUIDANCE } = JSON.parse(readFileSync(new URL('./questions.json', import.meta.url), 'utf8'));
const assistance = new Set(['unknown', 'independent', 'hinted', 'reference']);
export function createTrainingFixture() {
  const base = createFixtureApi(), requests = [], violations = [], receipts = new Map();
  let revision = 0, available = true, drop = false, nextId = 1;
  const records = [];
  function state() {
    const active = records.filter(record => !record.withdrawn), latest = active.at(-1);
    const practiced = POOL_IDS.filter(id => records.some(record => record.scenario_id === id));
    const remaining = POOL_IDS.filter(id => !practiced.includes(id));
    const pool = { version: 'idempotency_retry_v1', total: 6, practiced_scenario_ids: practiced, remaining_scenario_ids: remaining, exhausted: remaining.length === 0 };
    const previous = active.at(-2);
    const labels = { unknown: '帮助情况未知', independent: '独立作答', hinted: '提示后作答', reference: '看过参考答案后作答' };
    const comparison = previous && latest ? `记录 #${previous.id} 标注为“${labels[previous.assistance]}”，记录 #${latest.id} 标注为“${labels[latest.assistance]}”。这是用户标注的作答条件对照，不是能力评分。` : '目前只有一条可用记录，尚不能对照作答条件。';
    return copy({ revision, skill_id: 'idempotency_retry', available, reason: available ? 'current' : 'source_changed', records: records.map(record => ({ ...record, feedback: feedback(record) })), scenarios, pool,
      recommendation: available && latest && remaining.length ? { scenario_id: remaining[0], prompt: 'Synthetic prompt (UI localizes by scenario)', reason: 'Synthetic reason', comparison, evidence_record_ids: active.slice(-2).map(record => record.id) } : null });
  }
  function feedback(record) {
    const decision = record.decision_answer;
    const result = { scope: 'selected_option_only', text_assessment: 'not_evaluated', outcome: 'abstain', record_id: record.id,
      rubric_version: decision?.rubric_version ?? null, question_id: decision?.question_id ?? null, choice_id: decision?.choice_id ?? null,
      rule_id: null, reason: '未提供可评估的方案选择；自由文本未评估。', next_step: null };
    if (!available || record.withdrawn) return { ...result, reason: '来源不可用或记录已撤回，暂停反馈。' };
    if (!decision) return result;
    if (decision.choice_id === 'uncertain') return { ...result, reason: '你选择了不确定，尚不判断方案是否符合本题规则。', next_step: '先核对本题前提和参考依据，再比较各方案的恢复后果。' };
    const rule = RULES[record.scenario_id];
    return { ...result, outcome: decision.choice_id === rule.supported ? 'supported' : 'contradicted', rule_id: `${rule.rule_id}:${decision.choice_id}`, ...GUIDANCE[record.scenario_id][decision.choice_id] };
  }
  const answer = (data, status = 200) => ({ status, data: copy(data) });
  const reject = (reason, status = 422, violation = false) => {
    if (violation) violations.push(reason);
    return answer({ error_code: reason }, status);
  };
  function handle(method, rawUrl, body, headers = {}) {
    const url = new URL(rawUrl, ORIGIN);
    requests.push({ method, path: url.pathname, body: copy(body) });
    if (url.origin !== ORIGIN || url.username || url.password) return reject('external_origin', 501, true);
    if (Object.keys(headers).some(key => ['authorization', 'proxy-authorization', 'cookie'].includes(key.toLowerCase()))) return reject('unexpected_credentials', 501, true);
    if (method === 'GET' && url.pathname === '/api/applications/101/events/201/readiness-feedback') return answer({ schema_version: 1, application_id: 101, event_id: 201, items: [] });
    if (method === 'GET' && url.pathname === '/api/interview-practice/plans') return answer([PLAN]);
    if (url.pathname !== EVIDENCE_PATH) {
      // The shell may emit content-free local presence; no other writes are allowed.
      if (method === 'POST' && url.pathname === '/api/companion/presence') {
        const fields = ['visible', 'typing', 'work_running', 'awaiting_approval', 'mock_interview', 'quiet', 'failure'];
        if (!body || Object.keys(body).some(key => ![...fields, 'identity', 'client_id', 'sequence'].includes(key))
          || fields.some(key => key in body && typeof body[key] !== 'boolean')
          || ('client_id' in body && typeof body.client_id !== 'string') || ('identity' in body && typeof body.identity !== 'string') || ('sequence' in body && (!Number.isSafeInteger(body.sequence) || body.sequence < 0))) return reject('unexpected_presence_payload', 501, true);
        return base.handle(method, url.href, body);
      }
      if (method !== 'GET') return reject(`forbidden_write:${method}:${url.pathname}`, 501, true);
      const result = base.handle(method, url.href, body);
      if (result.status === 501) violations.push(`unmocked_read:${url.pathname}`);
      return result;
    }
    if (url.search) return reject('unexpected_evidence_query', 501, true);
    if (method === 'GET') return answer(state());
    if (method !== 'POST') return reject('unexpected_evidence_method', 501, true);
    const fields = { record: ['action', 'expected_revision', 'idempotency_key', 'skill_id', 'scenario_id', 'answer', 'assistance'], correct: ['action', 'expected_revision', 'idempotency_key', 'record_id', 'answer', 'assistance'], withdraw: ['action', 'expected_revision', 'idempotency_key', 'record_id'] };
    if (!body || !Object.hasOwn(fields, body.action) || !validKeys(body, [...fields[body.action], ...((body.action === 'record' || body.action === 'correct') && Object.hasOwn(body, 'decision_answer') ? ['decision_answer'] : [])]) || !UUID.test(body.idempotency_key ?? '') || !Number.isInteger(body.expected_revision) || body.expected_revision < 0) return reject('training_evidence_invalid');
    const fingerprint = canonical(Object.fromEntries(Object.entries(body).filter(([key, value]) => key !== 'decision_answer' || value !== null)));
    if (receipts.has(body.idempotency_key)) return receipts.get(body.idempotency_key) === fingerprint ? answer(state()) : reject('training_evidence_idempotency_conflict', 409);
    if (body.expected_revision !== revision) return reject('training_evidence_conflict', 409);
    if (body.action !== 'withdraw' && !available) return reject('training_evidence_source_unavailable', 409);
    let record;
    if (body.action === 'record') {
      if (body.skill_id !== 'idempotency_retry' || !scenarios.some(item => item.id === body.scenario_id)) return reject('training_evidence_invalid');
      if (records.filter(item => !item.withdrawn).length >= 12) return reject('training_evidence_conflict', 409);
      if (body.scenario_id === 'original' && records.some(item => item.scenario_id === 'original' && !item.withdrawn)) return reject('training_evidence_conflict', 409);
      record = { id: nextId, scenario_id: body.scenario_id, withdrawn: false };
    } else {
      record = records.find(item => item.id === body.record_id && !item.withdrawn);
      if (!record) return reject('training_evidence_not_found', 404);
    }
    if (body.action !== 'withdraw' && (typeof body.answer !== 'string' || !body.answer.trim() || body.answer.length > 8000 || !assistance.has(body.assistance)
      || (record.scenario_id === 'original' && body.answer !== ORIGINAL_ANSWER))) return reject('training_evidence_invalid');
    const decision = body.decision_answer ?? null;
    if (decision !== null) {
      const question = scenarios.find(item => item.id === record.scenario_id)?.decision_question;
      if (!validKeys(decision, ['rubric_version', 'question_id', 'choice_id']) || !question || decision.rubric_version !== question.rubric_version
        || decision.question_id !== question.question_id || !question.choices.some(choice => choice.id === decision.choice_id)) return reject('training_evidence_invalid');
    }
    revision += 1;
    const time = `2026-11-09T00:${String(revision + 1).padStart(2, '0')}:00Z`;
    if (body.action === 'record') { records.push(record); nextId += 1; record.created_at = time; }
    Object.assign(record, { decision_answer: body.action === 'withdraw' ? null : copy(decision), updated_at: time, answer: body.action === 'withdraw' ? '' : body.answer, assistance: body.action === 'withdraw' ? 'unknown' : body.assistance, withdrawn: body.action === 'withdraw' });
    receipts.set(body.idempotency_key, fingerprint);
    const result = answer(state());
    if (drop) { drop = false; result.dropResponse = true; }
    return result;
  }
  return { handle, requests, violations, state, pauseSource() { available = false; }, dropNextResponse() { drop = true; } };
}
