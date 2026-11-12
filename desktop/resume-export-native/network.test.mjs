import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyRequest } from './network.mjs';
const owner = 'http://127.0.0.1:54321';
const record = { method: 'GET', origin: owner, pathname: '/api/chat/conversations' };
test('only exact same-origin GET conversations is the audited chat read', () => {
  assert.equal(classifyRequest(record, owner), 'allowed-local-conversation-read');
  for (const method of ['POST', 'DELETE', 'PATCH', 'HEAD']) assert.equal(classifyRequest({ ...record, method }, owner), 'rejected-chat-or-ai-route');
  for (const pathname of ['/api/chat/conversations/1', '/api/chat/image-capability', '/api/chat/stream', '/api/ai/config', '/api/chat', '/api/ai']) assert.equal(classifyRequest({ ...record, pathname }, owner), 'rejected-chat-or-ai-route');
});
test('other origins/ports remain denied, including the audited path and normal app paths', () => {
  for (const origin of ['http://127.0.0.1:54322', 'http://localhost:54321', 'https://example.test', 'https://127.0.0.1:54321']) {
    assert.equal(classifyRequest({ ...record, origin }, owner), 'rejected-external-origin');
    assert.equal(classifyRequest({ ...record, origin, pathname: '/api/resumes' }, owner), 'rejected-external-origin');
  }
  assert.equal(classifyRequest({ ...record, method: 'POST', pathname: '/api/resumes/1/export' }, owner), 'allowed-app-request');
});
