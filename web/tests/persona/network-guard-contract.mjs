import assert from 'node:assert/strict';
import { classifyRequest, ORIGIN } from './network-guard.mjs';
const id = '11111111-1111-4111-8111-111111111111';
const spec = { display_name: 'Nova', traits: [], speaking_style: '', verbosity: 'balanced', interests_text: '', fictional_background: '', additional_style_notes: '', enabled_activity_templates: [] };
const command = { operation: 'update', profile_id: id, expected_revision: 0, command_id: id, confirmed: true, spec };
const classify = (method, suffix, body) => classifyRequest(method, `${ORIGIN}${suffix}`, body, '/tmp/persona-dist');
assert.equal(classify('POST', '/api/companion/persona/mutations', command).mutation, true);
assert.equal(classify('POST', '/api/companion/persona/mutations', { ...command, confirmed: false }).blocked, 'invalid_persona_command');
assert.ok(classify('POST', '/api/companion/persona/mutations', { ...command, extra: true }).blocked);
assert.ok(classify('POST', '/api/companion/persona/mutations?bypass=true', command).blocked);
for (const suffix of ['/api/settings', '/api/settings/providers/test', '/api/chat', '/api/companion/settings', '/api/companion', '/api/not-real']) assert.ok(classify('POST', suffix, {}).blocked, suffix);
for (const event of ['pause', 'resume', 'abandon', 'complete_shared', 'withdraw_shared', 'quiet_session']) assert.ok(classify('POST', '/api/companion/events', { identity: id, command_id: id, expected_revision: 0, event }).blocked, event);
assert.equal(classify('POST', '/api/companion/events', { identity: id, command_id: id, expected_revision: 0, event: 'app_open' }).wake, true);
assert.ok(classify('POST', '/api/companion/events', { identity: id, command_id: id, expected_revision: 0, event: 'app_open', activity_id: id }).blocked);
assert.ok(classifyRequest('GET', `http://secret@127.0.0.1:18763/`, null, '/tmp/persona-dist').blocked);
for (const url of ['https://example.invalid/', 'ws://127.0.0.1:18763/', 'http://localhost:18763/', 'file:///tmp/example']) assert.ok(classifyRequest('GET', url, null, '/tmp/persona-dist').blocked, url);
assert.ok(classify('GET', '/api/unknown').blocked);
assert.equal(classify('GET', '/api/companion/persona').api, true);
assert.equal(classify('GET', '/assets/app.js').filename, '/tmp/persona-dist/assets/app.js');
assert.ok(classify('GET', '/assets/%2e%2e%2foutside').blocked);
assert.ok(classify('GET', '/%00').blocked);
for (const invalid of [
  { ...spec, display_name: 'x'.repeat(25) }, { ...spec, speaking_style: 'x'.repeat(201) },
  { ...spec, interests_text: 'x'.repeat(301) }, { ...spec, fictional_background: 'x'.repeat(501) },
  { ...spec, additional_style_notes: 'x'.repeat(501) }, { ...spec, traits: Array(6).fill('x') },
  { ...spec, traits: ['x', ' x '] }, { ...spec, enabled_activity_templates: ['paper_boat', 'paper_boat'] },
  { ...spec, enabled_activity_templates: ['external_tool'] }, { ...spec, display_name: '\u202efake' },
  { ...spec, fictional_background: '\u0000' }, { ...spec, traits: ['line\nbreak'] },
  { ...spec, enabled_activity_templates: ['paper_boat', 'cloud_catalog', 'paper_stars'], display_name: 'x'.repeat(24), speaking_style: '\n'.repeat(200), interests_text: 'x'.repeat(300), fictional_background: 'x'.repeat(500), additional_style_notes: 'x'.repeat(500), traits: Array.from({length:5}, (_, index) => `${index}${'x'.repeat(39)}`) },
]) assert.ok(classify('POST', '/api/companion/persona/mutations', { ...command, spec: invalid }).blocked, JSON.stringify(invalid));
const byteHeavy = { ...spec, speaking_style: '😀'.repeat(200), interests_text: '😀'.repeat(300), fictional_background: '😀'.repeat(500), additional_style_notes: '😀'.repeat(500), traits: ['😀'.repeat(40)] };
assert.ok(classify('POST', '/api/companion/persona/mutations', { ...command, spec: byteHeavy }).blocked);
const presence = { identity: id, client_id: id, sequence: 1, visible: true, typing: false, work_running: false, awaiting_approval: false, mock_interview: false, quiet: false, failure: false };
assert.equal(classify('POST', '/api/companion/presence', presence).contentFree, true);
assert.ok(classify('POST', '/api/companion/presence', { ...presence, message: 'not content-free' }).blocked);
assert.ok(classify('POST', '/api/companion/bid', { identity: id, client_id: id, expected_revision: 0, extra: true }).blocked);
assert.equal(classify('POST', '/api/companion/persona/mutations', { operation: 'restore', profile_id: id, command_id: id, expected_revision: 1, confirmed: true, target_revision: 0 }).mutation, true);
console.log('persona network guard: strict positive and negative contracts passed');
