'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { POLICY, CASES, Ledger, envelope, usageCost, UsageParser } = require('../broker-core.cjs');
const usage = (p = 100, c = 10, h = 0) => ({ prompt_tokens: p, completion_tokens: c,
  prompt_cache_hit_tokens: h, prompt_cache_miss_tokens: p - h, total_tokens: p + c });
const payload = { model: 'deepseek-flash', messages: [{ role: 'user', content: 'FAKE_PRIVATE_PROMPT' }] };
const response = (u = usage(), extra = {}) => ({ model: 'deepseek-flash', choices: [{ index: 0,
  finish_reason: 'stop', message: { content: 'FAKE_PRIVATE_RESPONSE' } }], usage: u, ...extra });
function claim(ledger, id) { ledger.armCase(id); const t = ledger.claim(); ledger.reserve(t, t.maxTokens, 'INSERTED'); return t; }
const throwsCode = (fn, code) => assert.throws(fn, (error) => error.code === code && error.message === code);

test('fixed policy proof: worst accepted context and output costs less than 3 CNY', () => {
  assert.equal(POLICY.maxRequests, 8); assert.equal(POLICY.runMs, 600000);
  assert.equal(POLICY.contextTokens, 1048576);
  assert.ok(2 * POLICY.contextTokens + 8 * Math.max(...Object.values(CASES).map((x) => x.maxTokens)) < POLICY.reserveMicroCny);
  assert.equal(POLICY.budgetMicroCny, 10000000);
});
test('integer peak settlement is cache-aware and rounds upward without float loss', () => {
  assert.equal(usageCost(usage(), 64).micro, 280);
  assert.equal(usageCost(usage(26, 1, 26), 64).micro, 10);
  assert.equal(usageCost(usage(POLICY.contextTokens - 8192, 8192), 8192).micro, 2146304);
});
test('all malformed/overflow/contradictory usage remains untrusted', () => {
  for (const mutation of [undefined, {}, { ...usage(), completion_tokens: 65 },
    { ...usage(), prompt_tokens: -1 }, { ...usage(), total_tokens: 111 },
    { ...usage(), prompt_cache_miss_tokens: 99 }, { ...usage(), prompt_tokens: 1e300 },
    { ...usage(), prompt_tokens: '100' }, { ...usage(), prompt_cache_hit_tokens: NaN },
    { ...usage(), completion_tokens: 0.1 }, { ...usage(), total_tokens: Number.MAX_SAFE_INTEGER },
    { ...usage(), completion_tokens_details: { reasoning_tokens: 11 } },
    { ...usage(), prompt_tokens_details: { cached_tokens: 1 } }]) throwsCode(() => usageCost(mutation, 64), 'USAGE');
});
test('envelope changes only max_tokens and streaming usage visibility', () => {
  const original = { ...payload, stream: true, tools: [{ type: 'function', function: { name: 'read' } }], thinking: { type: 'disabled' } };
  const prepared = envelope(original, 4096);
  assert.equal(prepared.envelope, 'INSERTED'); assert.equal(prepared.body.max_tokens, 4096);
  assert.deepEqual(prepared.body.stream_options, { include_usage: true });
  const { max_tokens, stream_options, ...rest } = prepared.body;
  assert.deepEqual(rest, original); assert.equal(Object.hasOwn(original, 'max_tokens'), false);
  assert.equal(envelope({ ...payload, max_tokens: 10000 }, 64).maxTokens, 64);
  assert.equal(envelope({ ...payload, max_tokens: 20 }, 64).envelope, 'UNCHANGED');
});
test('negative request mutations reject alternate models/endpoints/limits/unknown fields', () => {
  for (const mutation of [{ model: 'deepseek-v4-pro' }, { model: 'openai/deepseek-flash' },
    { max_completion_tokens: 20000 }, { max_tokens: 0 }, { max_tokens: -1 }, { max_tokens: 1.2 },
    { max_tokens: '8192' }, { max_tokens: 1e300 }, { n: 2 }, { n: 0 }, { best_of: 2 },
    { api_base: 'https://example.com' }, { base_url: 'https://example.com' }, { retry: 3 },
    { fallback: true }, { extra_body: { model: 'other' } }, { stream: 'true' },
    { stream_options: { include_usage: true } }, { stream: true, stream_options: { include_usage: true, other: 1 } },
    { temperature: 3 }, { top_p: Infinity }, { tools: 'bad' },
    { tools: [{ type: 'web_search' }] }, { tools: [{ type: 'function', function: { name: 'read', hosted: true } }] },
    { response_format: { type: 'unknown' } }, { tool_choice: { type: 'web_search' } },
    { messages: [{ role: 'user', content: [{ type: 'image_url', image_url: { url: 'https://example.com' } }] }] },
    { thinking: { type: 'enabled', budget_tokens: 9999999 } }]) assert.throws(() => envelope({ ...payload, ...mutation }, 64));
});
test('atomic single arm/claim prevents concurrent, repeated and unknown cases', () => {
  const ledger = new Ledger(); throwsCode(() => ledger.claim(), 'UNARMED');
  throwsCode(() => ledger.armCase('unreviewed'), 'CASE');
  ledger.armCase('connection'); throwsCode(() => ledger.armCase('pilot-stream'), 'BUSY');
  const ticket = ledger.claim(); throwsCode(() => ledger.claim(), 'BUSY');
  ledger.reserve(ticket, 64, 'INSERTED'); throwsCode(() => ledger.reserve(ticket, 64, 'INSERTED'), 'CASE');
  ledger.finish(ticket, 'SETTLED', usage()); throwsCode(() => ledger.armCase('connection'), 'CASE');
  assert.equal(ledger.snapshot().sentRequests, 1); assert.equal(ledger.snapshot().settledMicroCny, 280);
});
test('missing usage, abort, timeout and late usage retain full reserves; fourth request refused', () => {
  const ledger = new Ledger();
  for (const [id, reason] of [['connection', 'TIMEOUT'], ['pilot-stream', 'DISCONNECT'], ['pilot-cancel', 'UPSTREAM']]) {
    const t = claim(ledger, id); ledger.finish(t, reason);
    assert.equal(ledger.finish(t, 'SETTLED', usage()), false);
  }
  assert.equal(ledger.snapshot().retainedMicroCny, 9000000);
  ledger.armCase('resume-structure'); const t = ledger.claim();
  throwsCode(() => ledger.reserve(t, 4096, 'INSERTED'), 'BUDGET');
  assert.equal(ledger.snapshot().sentRequests, 3);
});
test('settled plus unresolved plus new reservation must fit the same run budget', () => {
  const ledger = new Ledger();
  const t = claim(ledger, 'interview-preparation'); ledger.finish(t, 'SETTLED', usage(900000, 8000));
  ledger.finish(claim(ledger, 'connection'), 'TIMEOUT'); ledger.finish(claim(ledger, 'pilot-stream'), 'DISCONNECT');
  ledger.armCase('offer-negotiation'); const blocked = ledger.claim();
  throwsCode(() => ledger.reserve(blocked, 8192, 'INSERTED'), 'BUDGET');
  assert.equal(ledger.snapshot().settledMicroCny, 1864000);
});
test('case cancellation consumes arm; global monotonic 10-minute deadline blocks send', () => {
  let now = 100; const ledger = new Ledger(() => now);
  ledger.armCase('connection'); ledger.cancelCase(); throwsCode(() => ledger.armCase('connection'), 'CASE');
  ledger.armCase('pilot-stream'); const t = ledger.claim(); now += POLICY.runMs;
  throwsCode(() => ledger.reserve(t, 4096, 'INSERTED'), 'DEADLINE');
  ledger.close(); throwsCode(() => ledger.armCase('offer-negotiation'), 'CLOSED');
});
test('JSON parser extracts only validated counters, never retained response or prompt content', () => {
  const parser = new UsageParser(false, 64); parser.push(Buffer.from(JSON.stringify(response())));
  assert.deepEqual(parser.end(), usage()); assert.equal(JSON.stringify(parser).includes('PRIVATE'), false);
});
test('SSE accepts official terminal-content usage and legacy separate usage after terminal, chunked at every byte', () => {
  const content = { model: 'deepseek-flash', choices: [{ index: 0, delta: { content: '秘密🙂' }, finish_reason: null }], usage: null };
  for (const terminal of [response(), { ...response(), usage: null }]) {
    const extra = terminal.usage ? '' : `data: ${JSON.stringify(response(usage(), { choices: [] }))}\r\n\r\n`;
    const text = `: keepalive\r\n\r\ndata: ${JSON.stringify(content)}\r\n\r\ndata: ${JSON.stringify(terminal)}\r\n\r\n${extra}data: [DONE]\r\n\r\n`;
    const parser = new UsageParser(true, 64);
    for (const byte of Buffer.from(text)) parser.push(Buffer.from([byte]));
    assert.deepEqual(parser.end(), usage());
  }
});
test('SSE refuses incomplete, missing, duplicated or nonterminal usage and post-DONE content', () => {
  const valid = `data: ${JSON.stringify(response())}\n\n`;
  for (const input of [valid, 'data: [DONE]\n\n',
    `data: ${JSON.stringify(response(null))}\n\ndata: [DONE]\n\n`,
    valid + valid + 'data: [DONE]\n\n',
    valid + 'data: [DONE]\n\n' + valid,
    `data: ${JSON.stringify(response(usage(), { choices: [{ finish_reason: null }] }))}\n\ndata: [DONE]\n\n`,
    valid + 'data: {"private":"BROKEN"}\n\n',
    'event: message\ndata: {}\n\n',
    `data: ${JSON.stringify(response(usage(), { model: 'unexpected-model' }))}\n\ndata: [DONE]\n\n`]) {
    const parser = new UsageParser(true, 64); assert.throws(() => { parser.push(Buffer.from(input)); parser.end(); });
  }
});
test('body, SSE event and malformed UTF-8 memory limits fail closed', () => {
  for (const [stream, chunk] of [[true, Buffer.alloc(POLICY.eventBytes + 1, 'x')],
    [false, Buffer.alloc(POLICY.responseBytes + 1, 'x')], [false, Buffer.from([0xff])]]) {
    const parser = new UsageParser(stream, 64); assert.throws(() => parser.push(chunk));
  }
});

const { RECOVERY, verifyPriorZip, createMockCarryForward } = require('../carry-forward-policy.cjs');
const inherited = () => verifyPriorZip(require('node:fs').readFileSync(require('node:path').join(__dirname, 'fixtures/prior-paid-38694575612.zip')));
const recoveryLedger = (now = () => performance.now(), options = {}) => new Ledger(now, { carryForward: inherited(), ...options });

test('recovery accepts only branded mode-matching history and preserves immutable exact prefix', () => {
  const carry = inherited();
  for (const untrusted of [null, {}, { ...carry }, JSON.parse(JSON.stringify(carry)), createMockCarryForward()]) {
    throwsCode(() => new Ledger(undefined, { carryForward: untrusted }), 'LEDGER');
  }
  const book = recoveryLedger(); const first = book.snapshot();
  assert.equal(first.sentRequests, 1); assert.equal(first.currentRequests, 0);
  assert.equal(first.settledMicroCny, 188); assert.equal(first.retainedMicroCny, 0);
  assert.equal(first.carryForward.scope, RECOVERY.scope);
  assert.deepEqual(first.requests, carry.requests);
  first.requests[0].micro = 0; first.requests.push({ caseId: 'pilot-stream' });
  assert.throws(() => { first.carryForward.source.runId = 0; }, TypeError);
  assert.deepEqual(book.snapshot().requests, carry.requests);
  // Undefined/fabricated tickets can never find the ticketless inherited row.
  book.markTransport(undefined, 'clientDisconnectObserved');
  assert.equal(book.finish(undefined, 'SETTLED', usage()), false);
  book.retainUncertain(undefined);
  assert.deepEqual(book.snapshot().requests, carry.requests);
  assert.equal(book.snapshot().settledMicroCny, 188);
});

test('recovery arm, claim and reserve allow only one new Pilot with cumulative cost/count', () => {
  const book = recoveryLedger(); const prefix = book.snapshot().requests[0];
  for (const id of Object.keys(CASES).filter(id => id !== 'pilot-stream')) throwsCode(() => book.armCase(id), 'CASE');
  throwsCode(() => book.claim(), 'UNARMED');
  book.armCase('pilot-stream'); const ticket = book.claim();
  throwsCode(() => book.claim(), 'BUSY');
  throwsCode(() => book.reserve({ ...ticket }, ticket.maxTokens, 'INSERTED'), 'CASE');
  book.reserve(ticket, ticket.maxTokens, 'INSERTED');
  throwsCode(() => book.reserve(ticket, ticket.maxTokens, 'INSERTED'), 'CASE');
  let snapshot = book.snapshot();
  assert.equal(snapshot.sentRequests, 2); assert.equal(snapshot.currentRequests, 1);
  assert.equal(snapshot.settledMicroCny + snapshot.retainedMicroCny, 3000188);
  assert.equal(snapshot.budgetMicroCny - snapshot.settledMicroCny - snapshot.retainedMicroCny, 6999812);
  book.markTransport(ticket, 'outboundStarted');
  book.finish(ticket, 'SETTLED', usage());
  snapshot = book.snapshot();
  assert.equal(snapshot.settledMicroCny, 188 + 280); assert.equal(snapshot.retainedMicroCny, 0);
  assert.equal(snapshot.closed, false, 'terminal UI proof runs before explicit cleanup close');
  assert.deepEqual(snapshot.requests[0], prefix);
  throwsCode(() => book.armCase('pilot-stream'), 'CASE');
  throwsCode(() => book.armCase('connection'), 'CASE');
  throwsCode(() => book.claim(), 'UNARMED');
  assert.equal(book.finish(ticket, 'SETTLED', usage(999, 99)), false);
  assert.equal(book.snapshot().settledMicroCny, 468);
});

test('recovery unknown usage, every failed outcome and cancelled arm seal the narrow segment', () => {
  for (const code of ['TIMEOUT', 'DISCONNECT', 'UPSTREAM_DISCONNECT', 'CANCELLED', 'UPSTREAM', 'PROTOCOL', 'USAGE', 'LEDGER', 'SETTLED']) {
    const book = recoveryLedger(); const prefix = book.snapshot().requests[0]; const ticket = claim(book, 'pilot-stream');
    book.finish(ticket, code, null);
    assert.equal(book.snapshot().closed, true, code);
    assert.equal(book.snapshot().settledMicroCny, 188, code);
    assert.equal(book.snapshot().retainedMicroCny, 3000000, code);
    assert.deepEqual(book.snapshot().requests[0], prefix);
    throwsCode(() => book.armCase('pilot-stream'), 'CLOSED');
    assert.equal(book.finish(ticket, 'SETTLED', usage()), false);
  }
  const book = recoveryLedger(); book.armCase('pilot-stream'); book.cancelCase();
  assert.equal(book.snapshot().closed, true); assert.equal(book.snapshot().currentRequests, 0);
  throwsCode(() => book.armCase('pilot-stream'), 'CLOSED');
});

test('recovery uses shared 120-second start, preserves 60-second Pilot bound and refuses late settlement', () => {
  let time = 1000; const book = recoveryLedger(() => time, { executionStartedAt: 100 });
  assert.equal(book.remainingMs(), 119100); assert.equal(CASES['pilot-stream'].timeoutMs, 60000);
  book.armCase('pilot-stream'); const ticket = book.claim(); book.reserve(ticket, 4096, 'INSERTED');
  time = 120100; assert.equal(book.remainingMs(), 0);
  book.finish(ticket, 'SETTLED', usage());
  assert.equal(book.snapshot().closed, true); assert.equal(book.snapshot().requests[1].status, 'DEADLINE');
  assert.equal(book.snapshot().settledMicroCny, 188); assert.equal(book.snapshot().retainedMicroCny, 3000000);
  for (const started of [NaN, Infinity, -1, time + 1]) throwsCode(() => recoveryLedger(() => time, { executionStartedAt: started }), 'DEADLINE');
  const legacy = new Ledger(() => time); assert.equal(legacy.remainingMs(), 600000);
});

test('recovery settlement persistence failure retains only current reservation without changing prefix', () => {
  const book = recoveryLedger(); const prefix = book.snapshot().requests[0]; const ticket = claim(book, 'pilot-stream');
  book.finish(ticket, 'SETTLED', usage()); book.retainUncertain(ticket);
  assert.deepEqual(book.snapshot().requests[0], prefix);
  assert.equal(book.snapshot().settledMicroCny, 188); assert.equal(book.snapshot().retainedMicroCny, 3000000);
  assert.equal(book.snapshot().requests[1].promptTokens, undefined); assert.equal(book.snapshot().closed, true);
});
