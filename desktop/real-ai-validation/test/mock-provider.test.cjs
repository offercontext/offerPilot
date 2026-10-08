'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const https = require('node:https');
const tls = require('node:tls');
const net = require('node:net');
const { performance } = require('node:perf_hooks');
// Sentinels are installed BEFORE the offline factory. If it ever falls back to
// a saved real provider transport, the test fails without touching the network.
let originalHttpsCalls = 0;
https.request = () => { originalHttpsCalls += 1; throw new Error('ORIGINAL_HTTPS_FORBIDDEN'); };
https.get = () => { originalHttpsCalls += 1; throw new Error('ORIGINAL_HTTPS_FORBIDDEN'); };
tls.connect = () => { originalHttpsCalls += 1; throw new Error('ORIGINAL_TLS_FORBIDDEN'); };
const beforeImport = { now: Date.now, request: https.request, get: https.get, tls: tls.connect, connect: net.Socket.prototype.connect };
const { createOfflineBroker } = require('../mock-provider.cjs');
assert.equal(Date.now, beforeImport.now);
assert.equal(https.request, beforeImport.request);
assert.equal(https.get, beforeImport.get);
assert.equal(tls.connect, beforeImport.tls);
assert.equal(net.Socket.prototype.connect, beforeImport.connect);
const { CASE_IDS, MODEL } = require('../mock-fixtures.cjs');
const actualDateNow = Date.now;
// Mirror the existing fake-broker tests: clear only Node tracing/TLS diagnostic
// settings, never inspect provider-key environment variables or credentials.
const diagnosticNames = ['NODE_DEBUG', 'NODE_DEBUG_NATIVE', 'NODE_OPTIONS', 'NODE_EXTRA_CA_CERTS',
  'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_TLS_REJECT_UNAUTHORIZED'];
const diagnostics = Object.fromEntries(diagnosticNames.map((name) => [name, process.env[name]]));
for (const name of diagnosticNames) delete process.env[name];
const directories = [];
after(() => {
  assert.equal(originalHttpsCalls, 0, 'no original provider/TLS transport was invoked');
  for (const name of diagnosticNames) { if (diagnostics[name] !== undefined) process.env[name] = diagnostics[name]; }
  for (const dir of directories) fs.rmSync(dir, { recursive: true, force: true });
});
async function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'offerpilot-mock-http-')); directories.push(dir);
  const ledgerPath = path.join(dir, 'MOCK-session-ledger.jsonl');
  const broker = await createOfflineBroker({ ledgerPath, sessionId: 'mock-local-http', runId: '123',
    runAttempt: 1, helperCommit: 'a'.repeat(40), requestCommit: 'b'.repeat(40) });
  t.after(() => broker.close());
  return { broker, ledgerPath };
}
function bodyFor(caseId) {
  return { model: MODEL, messages: [{ role: 'user', content: 'MOCK synthetic input only' }],
    ...(caseId.startsWith('pilot-') ? { stream: true } : {}),
    ...(caseId === 'pilot-hitl-reject' ? { tools: [{ type: 'function', function: { name: 'create_application',
      parameters: { type: 'object', properties: { company_name: { type: 'string' }, position_name: { type: 'string' } },
        required: ['company_name', 'position_name'] } } }] } : {}),
  };
}
function send(broker, clientToken, body, { stopAfterData = false } = {}) {
  return new Promise((resolve, reject) => {
    const started = performance.now(); const times = []; const chunks = [];
    const req = http.request(`${broker.origin}/v1/chat/completions`, { method: 'POST', agent: false,
      headers: { Authorization: `Bearer ${clientToken}`, 'Content-Type': 'application/json' } }, (res) => {
      res.on('error', (error) => { if (!stopAfterData) reject(error); });
      res.on('data', (chunk) => {
        times.push(performance.now() - started); chunks.push(chunk);
        if (stopAfterData) { res.destroy(); resolve({ status: res.statusCode, times, text: Buffer.concat(chunks).toString() }); }
      });
      res.on('end', () => resolve({ status: res.statusCode, times, text: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject); req.end(JSON.stringify(body));
  });
}
const ready = (broker, id) => { const { clientToken } = broker.prepareCase(id); broker.armCase(id); return clientToken; };
async function waitUntil(predicate) {
  const end = performance.now() + 2000;
  while (!predicate()) { assert.ok(performance.now() < end, 'condition timed out'); await new Promise((resolve) => setTimeout(resolve, 10)); }
}
const sse = (text) => text.split('\n\n').filter(Boolean).map((line) => line.slice(6)).filter((line) => line !== '[DONE]').map(JSON.parse);

test('all seven MOCK cases traverse actual broker HTTP/token/envelope/ledger; only product disconnect retains reserve', async (t) => {
  const { broker, ledgerPath } = await fixture(t);
  const tokens = new Set();
  for (const [index, caseId] of CASE_IDS.entries()) {
    const token = ready(broker, caseId); assert.equal(tokens.has(token), false); tokens.add(token);
    if (index > 0) {
      const previous = [...tokens][index - 1];
      assert.equal((await send(broker, previous, bodyFor(caseId))).status, 403, 'old case token is denied');
    }
    const result = await send(broker, token, bodyFor(caseId), { stopAfterData: caseId === 'pilot-cancel' });
    assert.equal(result.status, 200);
    await waitUntil(() => !broker.snapshot().active);
    const snapshot = broker.snapshot(); const row = snapshot.requests.at(-1);
    assert.equal(snapshot.mode, 'MOCK'); assert.equal(snapshot.mock.usageIsSynthetic, true);
    assert.equal(snapshot.mock.realProviderCalls, 0); assert.equal(snapshot.mock.fakeRequests, index + 1);
    assert.equal(row.caseId, caseId); assert.equal(row.outboundStarted, true); assert.equal(row.upstreamResponded, true);
    if (caseId === 'pilot-cancel') {
      assert.equal(row.status, 'DISCONNECT'); assert.equal(row.clientDisconnectObserved, true);
      assert.equal(snapshot.retainedMicroCny, 3000000);
      assert.equal(row.promptTokens, undefined);
      // No harness cancellation before this assertion, so Stop cannot be
      // falsely certified by close()/cancelCase() or an upstream-only abort.
    } else {
      assert.equal(row.status, 'SETTLED'); assert.equal(snapshot.retainedMicroCny, 0);
      assert.equal(row.promptTokens, 20); assert.equal(row.completionTokens, 3);
      if (caseId === 'pilot-stream') {
        const frames = sse(result.text);
        assert.ok(frames.filter((frame) => frame.choices[0]?.delta.content).length >= 3);
        assert.ok(result.times.length >= 3); assert.ok(result.times.at(-1) - result.times[0] >= 1000);
        assert.match(result.text, /MOCK/);
      } else if (caseId === 'pilot-hitl-reject') {
        const frames = sse(result.text); const tool = frames.flatMap((frame) => frame.choices[0]?.delta.tool_calls || [])[0];
        assert.equal(tool.function.name, 'create_application');
        assert.deepEqual(JSON.parse(tool.function.arguments), { company_name: '合成待拒绝公司', position_name: '合成待拒绝岗位', status: 'applied' });
        assert.ok(frames.some((frame) => frame.choices[0].finish_reason === 'tool_calls'));
      } else if (caseId === 'connection') assert.match(JSON.parse(result.text).choices[0].message.content, /^OK \(MOCK/);
      else assert.equal(typeof JSON.parse(JSON.parse(result.text).choices[0].message.content), 'object');
    }
    const entries = fs.readFileSync(ledgerPath, 'utf8').trim().split('\n').map(JSON.parse);
    const reservations = entries.filter((entry) => entry.event === 'RESERVE');
    assert.equal(reservations.length, index + 1);
    assert.equal(reservations.at(-1).requests.at(-1).status, 'RESERVED');
    assert.equal(reservations.at(-1).requests.at(-1).outboundStarted, false);
  }
  assert.equal(broker.snapshot().settledMicroCny, 6 * 64);
  const journal = fs.readFileSync(ledgerPath, 'utf8');
  for (const privateByte of [...tokens, 'MOCK_ONLY_NOT_A_REAL_PROVIDER_CREDENTIAL', '合成候选人', '合成待拒绝公司']) {
    assert.equal(journal.includes(privateByte), false, 'ledger contains numeric evidence only');
  }
  assert.equal(originalHttpsCalls, 0);
});

test('offline network guard rejects unknown endpoint, method, headers, real auth, TLS and non-loopback TCP', async (t) => {
  const { broker } = await fixture(t);
  ready(broker, 'connection');
  const valid = { protocol: 'https:', hostname: 'api.deepseek.com', port: 443, path: '/chat/completions', method: 'POST',
    agent: false, rejectUnauthorized: true, servername: 'api.deepseek.com', minVersion: 'TLSv1.2', ca: tls.rootCertificates,
    headers: { Authorization: 'Bearer MOCK_ONLY_NOT_A_REAL_PROVIDER_CREDENTIAL', 'Content-Type': 'application/json',
      'Content-Length': 2, 'Accept-Encoding': 'identity' } };
  for (const changed of [{ hostname: 'example.invalid' }, { path: '/redirect' }, { method: 'GET' }, { rejectUnauthorized: false },
    { headers: { ...valid.headers, Authorization: 'Bearer NOT_A_REAL_TEST_CREDENTIAL' } },
    { headers: { ...valid.headers, 'X-Extra': 'DENIED' } }, { port: 444 }, { extra: true }]) {
    assert.throws(() => https.request({ ...valid, ...changed }, () => {}), /MOCK_NETWORK_DENIED/);
  }
  assert.throws(() => https.request('https://api.deepseek.com/chat/completions', () => {}), /MOCK_NETWORK_DENIED/);
  assert.throws(() => https.get('https://example.invalid'), /MOCK_NETWORK_DENIED/);
  assert.throws(() => tls.connect({ host: '127.0.0.1', port: 443 }), /MOCK_NETWORK_DENIED/);
  assert.throws(() => net.connect({ host: 'example.invalid', port: 443 }), /MOCK_NETWORK_DENIED/);
  assert.throws(() => net.connect({ host: '203.0.113.1', port: 443 }), /MOCK_NETWORK_DENIED/);
  assert.throws(() => net.connect({ path: '/tmp/unknown.sock' }), /MOCK_NETWORK_DENIED/);
  assert.equal(originalHttpsCalls, 0);
  await broker.close();
  assert.throws(() => https.request(valid, () => {}), /MOCK_NETWORK_DENIED/, 'closing never restores outbound HTTPS');
});

test('MOCK price clock is broker-only and no broker API can override transport, fixture, key, or live price policy', async (t) => {
  const { broker } = await fixture(t);
  const before = Date.now(); await new Promise((resolve) => setTimeout(resolve, 30));
  assert.ok(Date.now() >= before + 20); assert.equal(Date.now, actualDateNow);
  assert.equal(broker.snapshot().mock.clockScope, 'BROKER_VM_ONLY');
  assert.throws(() => broker.prepareCase('unknown-case'), /MOCK_NETWORK_DENIED/);
  assert.throws(() => broker.armCase('connection'), /MOCK_NETWORK_DENIED/);
  await assert.rejects(createOfflineBroker({}), /MOCK_NETWORK_DENIED/, 'only one process-global fake owner');
  const token = ready(broker, 'connection');
  assert.equal((await send(broker, token, bodyFor('connection'))).status, 200);
  await waitUntil(() => !broker.snapshot().active);
  assert.throws(() => broker.prepareCase('connection'), /CASE/);
  assert.equal((await send(broker, token, bodyFor('connection'))).status, 403);
});

test('strict guard permits the installed Playwright WebSocket transport over literal loopback only', async (t) => {
  const { broker } = await fixture(t);
  // This is a transport echo, not a fake Electron app, renderer or UI scenario.
  // Electron's node inspector and Chromium CDP use this bundled ws transport.
  const { ws: WebSocket, wsServer: WebSocketServer } = require('playwright-core/lib/utilsBundle');
  const server = http.createServer();
  const websocketServer = new WebSocketServer({ server });
  websocketServer.on('connection', (socket) => socket.on('message', (data) => socket.send(data)));
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const socket = new WebSocket(`ws://127.0.0.1:${server.address().port}/mock-transport-only`);
  try {
    await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); });
    const received = new Promise((resolve) => socket.once('message', (data) => resolve(data.toString())));
    socket.send('MOCK_LOOPBACK_TRANSPORT_ONLY');
    assert.equal(await received, 'MOCK_LOOPBACK_TRANSPORT_ONLY');
    assert.equal(broker.snapshot().mock.fakeRequests, 0, 'WebSocket is real local networking, not a fake provider call');
    assert.equal(originalHttpsCalls, 0);
  } finally {
    socket.terminate();
    for (const peer of websocketServer.clients) peer.terminate();
    await new Promise((resolve) => websocketServer.close(resolve));
    await new Promise((resolve) => server.close(resolve));
  }
});
