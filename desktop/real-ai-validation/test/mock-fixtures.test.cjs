'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { fixtureFor, SYNTHETIC, MODEL, CASE_IDS } = require('../mock-fixtures.cjs');
const root = path.resolve(__dirname, '../../..');
const content = (caseId) => JSON.parse(JSON.parse(fixtureFor(caseId, { model: MODEL }).body).choices[0].message.content);
const source = (relative) => fs.readFileSync(path.join(root, relative), 'utf8');
const keys = (value, expected) => assert.deepEqual(Object.keys(value).sort(), [...expected].sort());

test('interview fixture matches exact V1/V2 keys, unique IDs and literal frozen evidence', () => {
  const schema = source('src/offerpilot/ai/interview_preparation_proposals.py');
  const fields = ['preparation_directions', 'story_prompts', 'review_points', 'interviewer_questions', 'items_to_clarify'];
  const payload = content('interview-preparation'); keys(payload, fields);
  const ids = new Set();
  for (const field of fields) {
    assert.ok(schema.includes(`"${field}"`)); assert.ok(Array.isArray(payload[field])); assert.ok(payload[field].length <= 8);
    for (const item of payload[field]) {
      keys(item, ['id', 'text', 'evidence_refs']); assert.match(item.id, /^[\x21-\x7e]{1,64}$/);
      assert.equal(ids.has(item.id), false); ids.add(item.id); assert.ok(item.text.length > 0 && item.text.length <= 1000);
      assert.ok(item.evidence_refs.length > 0 && item.evidence_refs.length <= 5);
      for (const ref of item.evidence_refs) {
        keys(ref, ['source', 'path', 'excerpt']);
        assert.equal(ref.path, ref.source === 'jd' ? '/jd/text' : '/raw_text');
        assert.ok(['jd', 'resume'].includes(ref.source));
        assert.ok((ref.source === 'jd' ? SYNTHETIC.jd : SYNTHETIC.rawResume).includes(ref.excerpt));
        assert.ok(ref.excerpt.length > 0);
      }
    }
  }
  assert.ok(ids.size >= 1, 'UI must receive a nonempty validated proposal');
});

test('resume fields satisfy actual dotted paths, contiguous indexes and unmodified source evidence', () => {
  const schema = source('src/offerpilot/resume_structured_import.py');
  assert.match(schema, /def decode_structured_output/); assert.match(schema, /_validate_contiguous_indexes\(fields\)/);
  const payload = content('resume-structure'); keys(payload, ['fields']); assert.ok(payload.fields.length > 0);
  const indexes = new Map(); const seen = new Set();
  for (const field of payload.fields) {
    keys(field, ['path', 'value', 'evidence']); assert.equal(seen.has(field.path), false); seen.add(field.path);
    assert.match(field.path, /^(contact\.name|career_intent\.target_roles\.\d+|skills\.\d+)$/);
    assert.ok(SYNTHETIC.rawResume.includes(field.evidence)); assert.ok(field.evidence.includes(field.value));
    assert.ok(field.value.trim() && field.evidence.trim());
    const indexed = field.path.match(/^(.*)\.(\d+)$/);
    if (indexed) { const values = indexes.get(indexed[1]) || []; values.push(Number(indexed[2])); indexes.set(indexed[1], values); }
  }
  for (const values of indexes.values()) assert.deepEqual(values, values.map((_value, index) => index));
});

test('offer JSON selects genuine closed template catalog entries without provider-authored text', () => {
  const payload = content('offer-negotiation');
  const arrays = ['communication_goals', 'clarification_questions', 'talking_points', 'preparation_checks'];
  keys(payload, ['proposal_status', ...arrays]); assert.equal(payload.proposal_status, 'normal');
  const catalog = source('src/offerpilot/ai/offer_negotiation_templates.py');
  const schema = source('src/offerpilot/ai/offer_negotiation.py'); assert.match(schema, /validate_offer_negotiation/);
  const ids = new Set();
  for (const field of arrays) {
    assert.ok(payload[field].length > 0 && payload[field].length <= 3);
    for (const item of payload[field]) {
      keys(item, ['id', 'template_id', 'evidence_ref_ids']);
      assert.equal(ids.has(item.id), false); ids.add(item.id);
      const start = catalog.indexOf(`"${item.template_id}"`); assert.ok(start >= 0);
      const definition = catalog.slice(start, catalog.indexOf('\n    ),', start));
      assert.ok(definition.includes(`"${field}"`));
      for (const evidenceId of item.evidence_ref_ids) assert.ok(definition.includes(`"${evidenceId}"`));
      assert.ok(item.evidence_ref_ids.length >= 1 && item.evidence_ref_ids.length <= 4);
    }
  }
});

test('mock fixture inventory is exact and stream/case/tool mismatch is fail-closed', () => {
  assert.deepEqual(CASE_IDS, ['connection', 'pilot-stream', 'pilot-hitl-reject', 'interview-preparation', 'resume-structure', 'offer-negotiation', 'pilot-cancel']);
  for (const [caseId, body] of [['unknown', { model: MODEL }], ['connection', { model: 'other' }],
    ['pilot-stream', { model: MODEL }], ['connection', { model: MODEL, stream: true }],
    ['pilot-hitl-reject', { model: MODEL, stream: true }]]) assert.throws(() => fixtureFor(caseId, body), /MOCK_FIXTURE_DENIED/);
  const cancel = fixtureFor('pilot-cancel', { model: MODEL, stream: true });
  assert.ok(cancel.repeatFrame); assert.equal(cancel.frames.some((event) => event.includes('[DONE]') || event.includes('"usage"')), false);
  assert.match(source('src/offerpilot/ai/tool_specs/applications.py'), /"create_application"[\s\S]*?confirmation_policy="required"/);
});
