'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { createBroker, recoveryBaselineSnapshot } = require('../broker.cjs');
const { RECOVERY, verifyPriorZip, createMockCarryForward } = require('../carry-forward-policy.cjs');
const inherited = () => verifyPriorZip(fs.readFileSync(path.join(__dirname, 'fixtures/prior-paid-38694575612.zip')));
const { POLICY } = require('../broker-core.cjs');
// These tests replace https.request before every local request: no provider is reachable.
// Isolate only diagnostic/TLS configuration (never provider credentials) for fake transport tests.
const diagnosticNames = ['NODE_DEBUG', 'NODE_DEBUG_NATIVE', 'NODE_OPTIONS', 'NODE_EXTRA_CA_CERTS',
  'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_TLS_REJECT_UNAUTHORIZED'];
const diagnostics = Object.fromEntries(diagnosticNames.map((name) => [name, process.env[name]]));
for (const name of diagnosticNames) delete process.env[name];
after(() => { for (const name of diagnosticNames) { if (diagnostics[name] !== undefined) process.env[name] = diagnostics[name]; } });
const payload = { model: 'deepseek-flash', messages: [{ role: 'user', content: 'FAKE_PRIVATE_PROMPT' }] };
const fakeUsage = { prompt_tokens: 20, completion_tokens: 3, prompt_cache_hit_tokens: 0,
  prompt_cache_miss_tokens: 20, total_tokens: 23 };
const fakeResponse = { model: 'deepseek-flash', choices: [{ index: 0, finish_reason: 'stop',
  message: { content: 'FAKE_PRIVATE_RESPONSE' } }], usage: fakeUsage };
function fakeUpstream(t, handler) {
  const calls = [];
  t.mock.method(https, 'request', (options, callback) => {
    const request = new EventEmitter(); request.destroyed = false;
    request.destroy = () => { request.destroyed = true; };
    request.end = (body) => {
      const incoming = new PassThrough(); incoming.statusCode = 200; incoming.complete = false;
      incoming.headers = { 'content-type': 'application/json', 'x-private': 'FAKE_UPSTREAM_HEADER' };
      const call = { options, body: JSON.parse(body), request, incoming, callback };
      calls.push(call); queueMicrotask(() => { request.emit('finish'); handler(call); });
    };
    return request;
  });
  return calls;
}
function answer(call, body = fakeResponse) {
  call.callback(call.incoming); call.incoming.complete = true;
  call.incoming.end(Buffer.from(JSON.stringify(body)));
}
async function fixture(t, options = {}) {
  t.mock.method(Date, 'now', () => Date.UTC(2026, 9, 16, 12));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'offerpilot-fake-broker-'));
  const ledgerPath = path.join(dir, 'session-ledger.json');
  const args = { providerKey: 'FAKE_ONLY_PROVIDER_KEY', ledgerPath, sessionId: 'test-session', runId: '123', runAttempt: 1,
    helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40), ...options };
  const broker = await createBroker(args);
  t.after(async () => {
    try { await broker.close(); } catch (error) { assert.equal(error.code, 'LEDGER'); assert.equal(broker.snapshot().journalFailed, true); }
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return { broker, ledgerPath, args };
}
function send(broker, token, body = payload, options = {}) {
  return new Promise((resolve) => {
    const encoded = typeof body === 'string' ? body : JSON.stringify(body);
    const req = http.request(`${broker.origin}${options.path || '/v1/chat/completions'}`, {
      method: options.method || 'POST', headers: { Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json', ...(options.headers || {}) }, agent: false,
    }, (res) => {
      const data = [];
      res.on('data', (c) => data.push(c));
      res.on('error', () => resolve({ status: res.statusCode, interrupted: true }));
      res.on('aborted', () => resolve({ status: res.statusCode, interrupted: true }));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, text: Buffer.concat(data).toString() }));
    });
    req.on('error', () => resolve({ interrupted: true })); req.end(encoded);
  });
}
function ready(b, id = 'connection') { const { clientToken } = b.prepareCase(id); b.armCase(id); return clientToken; }

test('real local HTTP path reserves durably before exactly one fake upstream and settles safe usage', async (t) => {
  const { broker, ledgerPath } = await fixture(t);
  const calls = fakeUpstream(t, (call) => {
    const rows = fs.readFileSync(ledgerPath, 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(rows.at(-1).event, 'RESERVE'); assert.equal(rows.at(-1).retainedMicroCny, 3000000);
    assert.equal(call.options.hostname, 'api.deepseek.com'); assert.equal(call.options.port, 443);
    assert.equal(call.options.protocol, 'https:'); assert.equal(call.options.path, '/chat/completions');
    assert.equal(call.options.rejectUnauthorized, true); assert.equal(call.options.agent, false);
    assert.equal(call.options.headers.Authorization, 'Bearer FAKE_ONLY_PROVIDER_KEY');
    assert.equal(Object.keys(call.options.headers).length, 4);
    assert.equal(call.body.max_tokens, 64); assert.deepEqual(call.body.messages, payload.messages);
    answer(call);
  });
  const token = ready(broker); const result = await send(broker, token);
  assert.equal(result.status, 200); assert.equal(result.headers['x-private'], undefined);
  assert.equal(calls.length, 1); assert.equal(broker.snapshot().settledMicroCny, 64);
  assert.equal(broker.snapshot().requests[0].outboundStarted, true);
  assert.equal(broker.snapshot().requests[0].upstreamResponded, true);
  assert.equal(broker.snapshot().retainedMicroCny, 0);
  const audit = JSON.stringify(broker.snapshot()) + fs.readFileSync(ledgerPath, 'utf8');
  for (const secret of [token, 'FAKE_ONLY_PROVIDER_KEY', 'FAKE_PRIVATE_PROMPT', 'FAKE_PRIVATE_RESPONSE', 'FAKE_UPSTREAM_HEADER']) assert.equal(audit.includes(secret), false);
  assert.equal(broker.snapshot().provenance.runId, '123');
  const product = require('../product.json');
  const provenance = broker.snapshot().provenance;
  assert.equal(provenance.productCommit, product.commit);
  assert.equal(provenance.buildRunId, String(product.runId));
  assert.equal(provenance.artifactId, String(product.artifactId));
  assert.equal(provenance.installerSha256, product.installerSha256);
});
test('only approved loopback route/auth/model may reach HTTPS, with no admin endpoint', async (t) => {
  const { broker } = await fixture(t); const calls = fakeUpstream(t, answer); const token = ready(broker);
  for (const options of [{ path: '/admin/arm' }, { path: '/v1/models' }, { path: '/chat/completions?extra=1' },
    { method: 'GET' }, { headers: { Origin: 'https://example.com' } }, { headers: { 'Content-Encoding': 'gzip' } }]) {
    const result = await send(broker, token, payload, options); assert.equal(result.status, 403);
  }
  assert.equal((await send(broker, 'WRONG_TOKEN')).status, 403);
  assert.equal((await send(broker, token, { ...payload, model: 'deepseek-v4-pro' })).status, 403);
  assert.equal(calls.length, 0); assert.equal(broker.snapshot().sentRequests, 0);
});
test('simultaneous duplicate, SDK retry, and late old-case token cannot consume the next grant', async (t) => {
  const { broker } = await fixture(t); let first; let accepted;
  const waiting = new Promise((resolve) => { accepted = resolve; });
  const calls = fakeUpstream(t, (call) => { if (!first) { first = call; accepted(); } else answer(call); });
  const oldToken = ready(broker); const pending = send(broker, oldToken); await waiting;
  assert.equal((await send(broker, oldToken)).status, 403);
  const next = broker.prepareCase('pilot-stream').clientToken;
  assert.throws(() => broker.armCase('pilot-stream'), /BUSY/);
  answer(first); assert.equal((await pending).status, 200);
  broker.armCase('pilot-stream'); assert.equal((await send(broker, oldToken)).status, 403);
  assert.equal((await send(broker, next)).status, 200);
  assert.equal(calls.length, 2); assert.equal(broker.snapshot().requests.length, 2);
  assert.throws(() => broker.armCase('connection'), /CASE/);
});
test('redirect and error replies are never followed or exposed; unknown spend retains reservation', async (t) => {
  const { broker } = await fixture(t);
  const calls = fakeUpstream(t, (call) => {
    if (calls.length === 1) { call.incoming.statusCode = 307; call.incoming.headers.location = 'https://evil.invalid/FAKE_SECRET'; }
    if (calls.length >= 2) call.incoming.statusCode = 500;
    answer(call, { ...fakeResponse, usage: null, error: 'FAKE_PRIVATE_ERROR' });
  });
  for (const id of ['connection', 'pilot-stream', 'pilot-cancel']) await send(broker, ready(broker, id));
  assert.equal(calls.length, 3); assert.equal(broker.snapshot().retainedMicroCny, 9000000);
  assert.equal(broker.snapshot().requests[0].status, 'REDIRECT');
  assert.equal(broker.snapshot().requests[1].status, 'UPSTREAM');
  const result = await send(broker, ready(broker, 'resume-structure'));
  assert.equal(result.status, 403); assert.equal(JSON.parse(result.text).error.code, 'BUDGET');
  assert.equal(calls.length, 3); assert.equal(JSON.stringify(broker.snapshot()).includes('FAKE_PRIVATE_ERROR'), false);
});
test('raw upstream SSE usage settles even when client adapter would discard usage', async (t) => {
  const { broker } = await fixture(t);
  const calls = fakeUpstream(t, (call) => {
    assert.deepEqual(call.body.stream_options, { include_usage: true });
    call.incoming.headers['content-type'] = 'text/event-stream'; call.callback(call.incoming);
    call.incoming.write(`data: ${JSON.stringify({ model: 'deepseek-flash', choices: [{ index: 0, delta: { content: 'FAKE_STREAM' }, finish_reason: null }] })}\n\n`);
    call.incoming.complete = true;
    call.incoming.end(`data: ${JSON.stringify(fakeResponse)}\n\ndata: [DONE]\n\n`);
  });
  const result = await send(broker, ready(broker, 'pilot-stream'), { ...payload, stream: true, max_tokens: 99999 });
  assert.equal(result.status, 200); assert.equal(calls[0].body.max_tokens, 4096);
  assert.equal(broker.snapshot().requests[0].status, 'SETTLED');
});
test('cancel and upstream disconnect never release reserve, including late usage', async (t) => {
  const { broker } = await fixture(t); let call; let accepted;
  const waiting = new Promise((resolve) => { accepted = resolve; });
  fakeUpstream(t, (c) => { call = c; accepted(); });
  const pending = send(broker, ready(broker)); await waiting;
  broker.cancelCase(); await pending;
  answer(call); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(call.request.destroyed, true);
  assert.equal(broker.snapshot().requests[0].status, 'CANCELLED');
  assert.equal(broker.snapshot().requests[0].clientDisconnectObserved, false);
  assert.equal(broker.snapshot().retainedMicroCny, 3000000);
});
test('stream client disconnect aborts upstream and keeps all uncertain spend reserved', async (t) => {
  const { broker } = await fixture(t); let call;
  fakeUpstream(t, (c) => {
    call = c; c.incoming.headers['content-type'] = 'text/event-stream'; c.callback(c.incoming);
    c.incoming.write(`data: ${JSON.stringify({ model: 'deepseek-flash', choices: [{ delta: { content: 'FAKE_STREAM' }, finish_reason: null }] })}\n\n`);
  });
  const token = ready(broker, 'pilot-cancel');
  await new Promise((resolve) => {
    const req = http.request(`${broker.origin}/v1/chat/completions`, { method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, agent: false }, (res) => {
      res.once('data', () => { res.destroy(); resolve(); });
    }); req.on('error', () => {}); req.end(JSON.stringify({ ...payload, stream: true }));
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(call.request.destroyed, true); assert.equal(broker.snapshot().requests[0].status, 'DISCONNECT');
  assert.equal(broker.snapshot().requests[0].clientDisconnectObserved, true);
  assert.equal(broker.snapshot().retainedMicroCny, 3000000);
});
test('per-request deadline aborts without retry and cannot be settled by later usage', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { broker } = await fixture(t); let call; let accepted;
  const waiting = new Promise((resolve) => { accepted = resolve; });
  fakeUpstream(t, (c) => { call = c; accepted(); });
  const pending = send(broker, ready(broker)); await waiting;
  t.mock.timers.tick(15001); const result = await pending;
  assert.equal(JSON.parse(result.text).error.code, 'TIMEOUT');
  answer(call); assert.equal(broker.snapshot().requests[0].status, 'TIMEOUT');
  assert.equal(broker.snapshot().requests[0].clientDisconnectObserved, false);
  assert.equal(broker.snapshot().retainedMicroCny, 3000000);
});
test('persistence failure before send fail-closes and blocks every new request', async (t) => {
  const { broker } = await fixture(t); const calls = fakeUpstream(t, answer);
  t.mock.method(fs, 'fsyncSync', () => { throw new Error('FAKE_SECRET_IO_ERROR'); });
  const result = await send(broker, ready(broker));
  assert.equal(JSON.parse(result.text).error.code, 'LEDGER'); assert.equal(calls.length, 0);
  assert.equal(broker.snapshot().requests[0].outboundStarted, false);
  assert.equal(broker.snapshot().requests[0].upstreamResponded, false);
  assert.equal(broker.snapshot().closed, true); assert.equal(broker.snapshot().journalFailed, true);
  assert.throws(() => broker.prepareCase('pilot-stream'), /CLOSED/);
  assert.equal(JSON.stringify(broker.snapshot()).includes('FAKE_SECRET'), false);
});
test('existing journal and rerun attempt reject before port binding or upstream calls', async (t) => {
  const { args } = await fixture(t); const calls = fakeUpstream(t, answer);
  await assert.rejects(createBroker(args), /LEDGER/);
  await assert.rejects(createBroker({ ...args, runAttempt: 2 }), /LEDGER/);
  await assert.rejects(createBroker({ ...args, sessionId: 'unsafe\nFAKE_SECRET' }), /LEDGER/);
  assert.equal(calls.length, 0);
});
test('malformed and oversized authenticated bodies consume case but never make paid request', async (t) => {
  const { broker } = await fixture(t); const calls = fakeUpstream(t, answer);
  await send(broker, ready(broker), '{BROKEN_PRIVATE');
  await send(broker, ready(broker, 'pilot-stream'), 'x'.repeat(POLICY.requestBytes + 1));
  assert.equal(calls.length, 0); assert.equal(broker.snapshot().sentRequests, 0);
});


test('invalid or missing usage closes the session and retains the entire reservation', async (t) => {
  for (const malformed of [undefined, { ...fakeUsage, completion_tokens: 999999999 }, { ...fakeUsage, total_tokens: 0 }]) {
    const { broker } = await fixture(t); const calls = fakeUpstream(t, (call) => answer(call, { ...fakeResponse, usage: malformed }));
    await send(broker, ready(broker));
    assert.equal(broker.snapshot().closed, true); assert.equal(broker.snapshot().retainedMicroCny, 3000000);
    assert.throws(() => broker.prepareCase('pilot-stream'), /CLOSED/); assert.equal(calls.length, 1);
  }
});
test('proxy environment cannot reroute fixed TLS transport, insecure Node settings reject startup', async (t) => {
  const { broker, args } = await fixture(t);
  const names = ['HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NODE_USE_ENV_PROXY'];
  const previous = Object.fromEntries(names.map((key) => [key, process.env[key]]));
  t.after(() => { for (const name of names) { if (previous[name] === undefined) delete process.env[name]; else process.env[name] = previous[name]; } });
  for (const name of names) process.env[name] = name === 'NODE_USE_ENV_PROXY' ? '1' : 'http://invalid.example:1234';
  fakeUpstream(t, (call) => { assert.equal(call.options.hostname, 'api.deepseek.com'); assert.equal(call.options.agent, false); assert.equal(call.options.rejectUnauthorized, true); answer(call); });
  assert.equal((await send(broker, ready(broker))).status, 200);
  for (const name of ['NODE_DEBUG', 'NODE_DEBUG_NATIVE', 'NODE_OPTIONS', 'NODE_TLS_REJECT_UNAUTHORIZED', 'NODE_EXTRA_CA_CERTS', 'SSL_CERT_FILE', 'SSL_CERT_DIR']) {
    const old = process.env[name]; process.env[name] = name === 'NODE_TLS_REJECT_UNAUTHORIZED' ? '0' : 'unsafe';
    try { await assert.rejects(createBroker(args), /AUTH/); }
    finally { if (old === undefined) delete process.env[name]; else process.env[name] = old; }
  }
});


test('FINISH fsync failure restores the full uncertain reservation in exported snapshot', async (t) => {
  const { broker } = await fixture(t); const sync = fs.fsyncSync;
  let writes = 0;
  t.mock.method(fs, 'fsyncSync', (...args) => { if (++writes === 2) throw new Error('FAKE_FINISH_FSYNC'); return sync(...args); });
  fakeUpstream(t, answer);
  await send(broker, ready(broker));
  const summary = broker.snapshot();
  assert.equal(summary.journalFailed, true); assert.equal(summary.closed, true);
  assert.equal(summary.settledMicroCny, 0); assert.equal(summary.retainedMicroCny, 3000000);
  assert.equal(summary.requests[0].status, 'LEDGER'); assert.equal(summary.requests[0].micro, 3000000);
  assert.equal(summary.requests[0].promptTokens, undefined);
  assert.throws(() => broker.prepareCase('pilot-stream'), /CLOSED/);
});


test('final CLOSE persistence failure rejects close and cannot be reported as a passing run', async (t) => {
  const { broker } = await fixture(t); fakeUpstream(t, answer);
  await send(broker, ready(broker));
  t.mock.method(fs, 'fsyncSync', () => { throw new Error('FAKE_CLOSE_FSYNC'); });
  await assert.rejects(broker.close(), /LEDGER/);
  await assert.rejects(broker.close(), /LEDGER/);
  assert.equal(broker.snapshot().journalFailed, true); assert.equal(broker.snapshot().closed, true);
});


test('price review rejects both the prior and following UTC dates before any reserve or HTTPS', async (t) => {
  const { broker, args } = await fixture(t); const calls = fakeUpstream(t, answer);
  for (const day of [8, 9, 10, 11, 14, 15, 17]) {
    t.mock.method(Date, 'now', () => Date.UTC(2026, 9, day));
    const rejectedPath = `${args.ledgerPath}-rejected-${day}`;
    await assert.rejects(createBroker({ ...args, ledgerPath: rejectedPath }), /EXPIRED/);
    assert.equal(fs.existsSync(rejectedPath), false, 'invalid date creates no new ledger');
  }
  const result = await send(broker, ready(broker));
  assert.equal(JSON.parse(result.text).error.code, 'EXPIRED');
  assert.equal(calls.length, 0); assert.equal(broker.snapshot().sentRequests, 0);
});
test('price window must cover the entire allowed request deadline', async (t) => {
  const { broker } = await fixture(t); const calls = fakeUpstream(t, answer);
  t.mock.method(Date, 'now', () => Date.UTC(2026, 9, 16, 23, 59, 50));
  const result = await send(broker, ready(broker));
  assert.equal(JSON.parse(result.text).error.code, 'EXPIRED'); assert.equal(calls.length, 0);
});
for (const afterBoundaryMs of [0, 1]) {
  test(`price window admission at last complete request timeout plus ${afterBoundaryMs} ms`, async (t) => {
    const { broker } = await fixture(t); const calls = fakeUpstream(t, answer);
    t.mock.method(Date, 'now', () => Date.parse('2026-10-16T23:59:59.999Z') - 15000 + afterBoundaryMs);
    const result = await send(broker, ready(broker));
    assert.equal(result.status, afterBoundaryMs === 0 ? 200 : 403);
    assert.equal(calls.length, afterBoundaryMs === 0 ? 1 : 0);
    assert.equal(broker.snapshot().sentRequests, afterBoundaryMs === 0 ? 1 : 0);
    if (afterBoundaryMs !== 0) assert.equal(JSON.parse(result.text).error.code, 'EXPIRED');
  });
}


test('upstream stream abort cannot manufacture evidence that the product cancelled', async (t) => {
  const { broker } = await fixture(t);
  fakeUpstream(t, (call) => {
    call.incoming.headers['content-type'] = 'text/event-stream'; call.callback(call.incoming);
    call.incoming.write(`data: ${JSON.stringify({ model: 'deepseek-flash', choices: [{ delta: { content: 'FAKE_STREAM' }, finish_reason: null }] })}\n\n`);
    setImmediate(() => { call.incoming.emit('aborted'); call.incoming.destroy(); });
  });
  await send(broker, ready(broker, 'pilot-cancel'), { ...payload, stream: true });
  const row = broker.snapshot().requests[0];
  assert.equal(row.upstreamResponded, true); assert.equal(row.status, 'UPSTREAM_DISCONNECT');
  assert.equal(row.clientDisconnectObserved, false); assert.equal(broker.snapshot().retainedMicroCny, 3000000);
});
test('broker forced close cannot manufacture evidence that the product cancelled', async (t) => {
  const { broker } = await fixture(t); let accepted;
  const waiting = new Promise((resolve) => { accepted = resolve; });
  fakeUpstream(t, accepted);
  const pending = send(broker, ready(broker, 'pilot-cancel'));
  await waiting;
  // This is a broker-origin cancellation, including teardown after an incomplete upstream.
  await broker.close(); await pending;
  const row = broker.snapshot().requests[0];
  assert.equal(row.status, 'CANCELLED'); assert.equal(row.clientDisconnectObserved, false);
});
test('RESERVE fsync crossing the UTC tariff window retains reservation and sends nothing', async (t) => {
  const { broker } = await fixture(t); const calls = fakeUpstream(t, answer); const sync = fs.fsyncSync;
  t.mock.method(fs, 'fsyncSync', (...args) => {
    sync(...args); t.mock.method(Date, 'now', () => Date.UTC(2026, 9, 17));
  });
  const result = await send(broker, ready(broker));
  assert.equal(JSON.parse(result.text).error.code, 'EXPIRED'); assert.equal(calls.length, 0);
  const summary = broker.snapshot(); assert.equal(summary.retainedMicroCny, 3000000);
  assert.equal(summary.requests[0].outboundStarted, false); assert.equal(summary.requests[0].clientDisconnectObserved, false);
});
test('RESERVE fsync crossing the monotonic run deadline cannot beat the pending timer', async (t) => {
  let monotonic = 100;
  t.mock.method(performance, 'now', () => monotonic);
  const { broker } = await fixture(t); const calls = fakeUpstream(t, answer); const sync = fs.fsyncSync;
  t.mock.method(fs, 'fsyncSync', (...args) => { sync(...args); monotonic += POLICY.runMs + 1; });
  const result = await send(broker, ready(broker));
  assert.equal(JSON.parse(result.text).error.code, 'DEADLINE'); assert.equal(calls.length, 0);
  assert.equal(broker.snapshot().retainedMicroCny, 3000000);
  assert.equal(broker.snapshot().requests[0].outboundStarted, false);
});
test('RESERVE fsync crossing only the case deadline cannot start an already expired call', async (t) => {
  let monotonic = 100;
  t.mock.method(performance, 'now', () => monotonic);
  const { broker } = await fixture(t); const calls = fakeUpstream(t, answer); const sync = fs.fsyncSync;
  t.mock.method(fs, 'fsyncSync', (...args) => { sync(...args); monotonic += 15001; });
  const result = await send(broker, ready(broker));
  assert.equal(JSON.parse(result.text).error.code, 'TIMEOUT'); assert.equal(calls.length, 0);
  assert.equal(broker.snapshot().retainedMicroCny, 3000000);
});


test('broker socket timeout is not mistaken for a product-side disconnect', async (t) => {
  const create = http.createServer; let socket;
  t.mock.method(http, 'createServer', (...args) => {
    const server = create(...args); server.on('connection', (value) => { socket = value; }); return server;
  });
  const { broker } = await fixture(t); let accepted;
  const waiting = new Promise((resolve) => { accepted = resolve; });
  fakeUpstream(t, accepted);
  const pending = send(broker, ready(broker, 'pilot-cancel')); await waiting;
  socket.emit('timeout'); await pending;
  const row = broker.snapshot().requests[0];
  assert.equal(row.status, 'TIMEOUT'); assert.equal(row.clientDisconnectObserved, false);
  assert.equal(broker.snapshot().retainedMicroCny, 3000000);
});

test('recovery baseline is pure verified prior-cost evidence and cannot manufacture MOCK guard state', t => {
  const carry = inherited();
  const binding = { mode: 'live', runId: '123', runAttempt: 1, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40) };
  for (const [object, name] of [[fs, 'openSync'], [http, 'createServer'], [https, 'request']]) {
    t.mock.method(object, name, () => { throw new Error('UNEXPECTED_IO'); });
  }
  const baseline = recoveryBaselineSnapshot(carry, binding);
  assert.equal(baseline.sentRequests, 1); assert.equal(baseline.currentRequests, 0);
  assert.equal(baseline.settledMicroCny, 188); assert.equal(baseline.retainedMicroCny, 0);
  assert.equal(baseline.active, false); assert.equal(baseline.closed, true); assert.equal(baseline.journalFailed, false);
  assert.equal(baseline.provenance.sessionId, RECOVERY.sessionId);
  assert.notEqual(baseline.provenance.productCommit, baseline.carryForward.source.productCommit);
  assert.throws(() => recoveryBaselineSnapshot({ ...carry }, binding), /LEDGER/);
  assert.throws(() => recoveryBaselineSnapshot(createMockCarryForward(), { ...binding, mode: 'mock' }), /LEDGER/);
});

test('recovery durable OPEN precedes server creation, tokens and one cumulative Pilot request', async t => {
  const sync = fs.fsyncSync, create = http.createServer;
  let synced = false;
  t.mock.method(fs, 'fsyncSync', (...args) => { sync(...args); synced = true; });
  t.mock.method(http, 'createServer', (...args) => { assert.equal(synced, true); return create(...args); });
  const { broker, ledgerPath } = await fixture(t, { carryForward: inherited(), sessionId: RECOVERY.sessionId });
  const prefix = broker.snapshot().requests[0];
  const opening = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
  assert.equal(opening.event, 'CARRY_FORWARD_OPEN'); assert.equal(opening.currentRequests, 0);
  assert.equal(opening.sentRequests, 1); assert.equal(opening.settledMicroCny, 188);
  assert.equal(opening.carryForward.source.runId, RECOVERY.source.runId);
  assert.ok(broker.remainingMs() > 118000 && broker.remainingMs() <= 120000);
  for (const id of ['connection', 'pilot-cancel', 'pilot-hitl-reject', 'interview-preparation', 'resume-structure', 'offer-negotiation']) {
    assert.throws(() => broker.prepareCase(id), /CASE/); assert.throws(() => broker.armCase(id), /CASE/);
  }
  const calls = fakeUpstream(t, call => {
    const durable = fs.readFileSync(ledgerPath, 'utf8').trim().split('\n').map(JSON.parse).at(-1);
    assert.equal(durable.event, 'RESERVE'); assert.equal(durable.currentRequests, 1);
    assert.equal(durable.settledMicroCny + durable.retainedMicroCny, 3000188);
    assert.deepEqual(durable.requests[0], prefix); answer(call);
  });
  const token = ready(broker, 'pilot-stream'); const result = await send(broker, token);
  assert.equal(result.status, 200); assert.equal(calls.length, 1);
  assert.equal(calls[0].body.max_tokens, 4096);
  assert.equal(broker.snapshot().sentRequests, 2); assert.equal(broker.snapshot().currentRequests, 1);
  assert.equal(broker.snapshot().settledMicroCny, 188 + 64); assert.equal(broker.snapshot().retainedMicroCny, 0);
  assert.deepEqual(broker.snapshot().requests[0], prefix);
  assert.throws(() => broker.prepareCase('pilot-stream'), /CASE/);
  assert.throws(() => broker.armCase('pilot-stream'), /CASE/);
  assert.equal((await send(broker, token)).status, 403); assert.equal(calls.length, 1);
});

test('recovery rejects unbranded/mode-mismatched/session-mismatched carry before creating journal or listener', async t => {
  const { args, broker } = await fixture(t); await broker.close();
  let bound = 0;
  t.mock.method(http, 'createServer', () => { bound++; throw new Error('MUST_NOT_LISTEN'); });
  for (const options of [
    { carryForward: { ...inherited() }, sessionId: RECOVERY.sessionId },
    { carryForward: createMockCarryForward(), sessionId: RECOVERY.sessionId, mode: 'mock' },
    { carryForward: inherited(), sessionId: 'different-session' },
  ]) {
    const ledgerPath = `${args.ledgerPath}-invalid`;
    await assert.rejects(createBroker({ ...args, ...options, ledgerPath }), /LEDGER/);
    assert.equal(fs.existsSync(ledgerPath), false);
  }
  assert.equal(bound, 0);
});

test('recovery OPEN fsync failure or elapsed shared start never bind a port or call HTTPS', async t => {
  const { args, broker } = await fixture(t); await broker.close();
  let bound = 0; const calls = fakeUpstream(t, answer);
  t.mock.method(http, 'createServer', () => { bound++; throw new Error('MUST_NOT_LISTEN'); });
  t.mock.method(fs, 'fsyncSync', () => { throw new Error('FAKE_FSYNC'); });
  await assert.rejects(createBroker({ ...args, ledgerPath: `${args.ledgerPath}-sync-failure`,
    carryForward: inherited(), sessionId: RECOVERY.sessionId }), /LEDGER/);
  let now = 121000; t.mock.method(performance, 'now', () => now);
  await assert.rejects(createBroker({ ...args, ledgerPath: `${args.ledgerPath}-expired-start`,
    carryForward: inherited(), sessionId: RECOVERY.sessionId, executionStartedAt: 100 }), /DEADLINE/);
  assert.equal(bound, 0); assert.equal(calls.length, 0);
});

test('recovery OPEN fsync cannot extend shared deadline or cross price boundary before listen', async t => {
  const { args, broker } = await fixture(t); await broker.close();
  const sync = fs.fsyncSync;
  let now = 100; t.mock.method(performance, 'now', () => now);
  let bound = 0;
  t.mock.method(http, 'createServer', () => { bound++; throw new Error('MUST_NOT_LISTEN'); });
  t.mock.method(fs, 'fsyncSync', (...args) => { sync(...args); now += RECOVERY.executionMs + 1; });
  await assert.rejects(createBroker({ ...args, ledgerPath: `${args.ledgerPath}-slow-open`,
    carryForward: inherited(), sessionId: RECOVERY.sessionId, executionStartedAt: 100 }), /DEADLINE/);
  assert.equal(bound, 0);
});

for (const reason of ['missing-usage', 'upstream-disconnect', 'reserve-fsync', 'finish-fsync']) {
  test(`recovery ${reason} preserves188 plus full3million reserve and permanently seals current segment`, async t => {
    const { broker } = await fixture(t, { carryForward: inherited(), sessionId: RECOVERY.sessionId });
    const prefix = broker.snapshot().requests[0]; const sync = fs.fsyncSync; let syncs = 0;
    if (reason.endsWith('fsync')) t.mock.method(fs, 'fsyncSync', (...args) => {
      if (++syncs === (reason === 'reserve-fsync' ? 1 : 2)) throw new Error('FAKE_FSYNC_FAILURE');
      return sync(...args);
    });
    const calls = fakeUpstream(t, call => {
      if (reason === 'upstream-disconnect') { call.callback(call.incoming); call.incoming.emit('aborted'); call.incoming.destroy(); }
      else answer(call, reason === 'missing-usage' ? { ...fakeResponse, usage: null } : fakeResponse);
    });
    await send(broker, ready(broker, 'pilot-stream'));
    const summary = broker.snapshot();
    assert.equal(summary.sentRequests, 2); assert.equal(summary.currentRequests, 1);
    assert.equal(summary.settledMicroCny, 188); assert.equal(summary.retainedMicroCny, 3000000);
    assert.equal(summary.closed, true); assert.equal(summary.active, false);
    assert.deepEqual(summary.requests[0], prefix);
    assert.equal(calls.length, reason === 'reserve-fsync' ? 0 : 1);
    assert.throws(() => broker.armCase('pilot-stream'), /CLOSED/);
  });
}

test('recovery concurrent duplicate and later retry never make a second new request', async t => {
  const { broker } = await fixture(t, { carryForward: inherited(), sessionId: RECOVERY.sessionId });
  let first, accepted; const waiting = new Promise(resolve => { accepted = resolve; });
  const calls = fakeUpstream(t, call => { first = call; accepted(); });
  const token = ready(broker, 'pilot-stream'); const pending = send(broker, token); await waiting;
  assert.equal((await send(broker, token)).status, 403);
  assert.throws(() => broker.prepareCase('connection'), /CASE/);
  assert.throws(() => broker.armCase('pilot-stream'), /CASE/);
  answer(first); assert.equal((await pending).status, 200);
  assert.equal((await send(broker, token)).status, 403);
  assert.equal(calls.length, 1); assert.equal(broker.snapshot().currentRequests, 1);
  assert.equal(broker.snapshot().sentRequests, 2); assert.equal(broker.snapshot().settledMicroCny, 252);
});

for (const [advance, reason] of [[60001, 'TIMEOUT'], [120001, 'DEADLINE']]) {
  test(`recovery reservation fsync crossing ${reason} keeps188 and reserve without outbound`, async t => {
    let now = 100; t.mock.method(performance, 'now', () => now);
    const { broker } = await fixture(t, { carryForward: inherited(), sessionId: RECOVERY.sessionId, executionStartedAt: 100 });
    const calls = fakeUpstream(t, answer); const sync = fs.fsyncSync;
    t.mock.method(fs, 'fsyncSync', (...args) => { sync(...args); now += advance; });
    const result = await send(broker, ready(broker, 'pilot-stream'));
    assert.equal(JSON.parse(result.text).error.code, reason);
    const summary = broker.snapshot();
    assert.equal(calls.length, 0); assert.equal(summary.currentRequests, 1); assert.equal(summary.sentRequests, 2);
    assert.equal(summary.settledMicroCny, 188); assert.equal(summary.retainedMicroCny, 3000000);
    assert.equal(summary.closed, true); assert.equal(summary.requests[1].outboundStarted, false);
  });
}

test('recovery cannot extend expired tariffs or reset cumulative cost when new reservation is blocked', async t => {
  const { broker } = await fixture(t, { carryForward: inherited(), sessionId: RECOVERY.sessionId });
  const calls = fakeUpstream(t, answer);
  t.mock.method(Date, 'now', () => Date.parse('2026-10-16T23:59:59.999Z') - 60000 + 1);
  const result = await send(broker, ready(broker, 'pilot-stream'));
  assert.equal(JSON.parse(result.text).error.code, 'EXPIRED');
  const summary = broker.snapshot();
  assert.equal(calls.length, 0); assert.equal(summary.currentRequests, 0); assert.equal(summary.sentRequests, 1);
  assert.equal(summary.settledMicroCny, 188); assert.equal(summary.retainedMicroCny, 0); assert.equal(summary.closed, true);
});

test('recovery OPEN fsync crossing tariff expiry fails before listener creation', async t => {
  const { args, broker } = await fixture(t); await broker.close();
  const sync = fs.fsyncSync; let bound = 0;
  t.mock.method(http, 'createServer', () => { bound++; throw new Error('MUST_NOT_LISTEN'); });
  t.mock.method(fs, 'fsyncSync', (...args) => { sync(...args); t.mock.method(Date, 'now', () => Date.UTC(2026, 9, 17)); });
  await assert.rejects(createBroker({ ...args, ledgerPath: `${args.ledgerPath}-expired-open`,
    carryForward: inherited(), sessionId: RECOVERY.sessionId }), /EXPIRED/);
  assert.equal(bound, 0);
});
