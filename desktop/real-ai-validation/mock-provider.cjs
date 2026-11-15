'use strict';

// Strictly separate OFFLINE entrypoint only. Importing this module installs
// nothing. createOfflineBroker permanently locks this process to fake HTTPS
// plus literal loopback TCP; NEVER import it from the live runner or product.
const https = require('node:https');
const tls = require('node:tls');
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { Writable, PassThrough } = require('node:stream');
const { performance } = require('node:perf_hooks');
const { fixtureFor, MODE, CASE_IDS } = require('./mock-fixtures.cjs');
const { PRICE_REVIEW } = require('./broker-core.cjs');
const carryPolicy = require('./carry-forward-policy.cjs');
const FAKE_KEY = 'MOCK_ONLY_NOT_A_REAL_PROVIDER_CREDENTIAL';
const MOCK_EPOCH = Date.parse(`${PRICE_REVIEW.priceCheckedAt}T12:00:00.000Z`);
let installed = false;
let owner = null;
let creating = false;
const offlineBrokers = new WeakMap();
function isOfflineBroker(broker) {
  const state = broker && offlineBrokers.get(broker);
  return Boolean(installed && state && state === owner && state.closed === false);
}
const fail = () => { const error = new Error('MOCK_NETWORK_DENIED'); error.code = 'MOCK_NETWORK_DENIED'; throw error; };
const lock = (object, key, value) => Object.defineProperty(object, key, { value, writable: false, configurable: false });

function validateRequest(options, callback) {
  if (!owner || typeof callback !== 'function' || !options || typeof options !== 'object' || Array.isArray(options)) fail();
  const expected = ['protocol', 'hostname', 'port', 'path', 'method', 'agent', 'rejectUnauthorized', 'servername', 'minVersion', 'ca', 'headers'];
  if (Object.keys(options).length !== expected.length || expected.some((key) => !Object.hasOwn(options, key)) ||
      options.protocol !== 'https:' || options.hostname !== 'api.deepseek.com' || options.port !== 443 ||
      options.path !== '/chat/completions' || options.method !== 'POST' || options.agent !== false ||
      options.rejectUnauthorized !== true || options.servername !== 'api.deepseek.com' ||
      options.minVersion !== 'TLSv1.2' || options.ca !== tls.rootCertificates) fail();
  const h = options.headers;
  if (!h || Object.keys(h).length !== 4 || h.Authorization !== `Bearer ${FAKE_KEY}` ||
      h['Content-Type'] !== 'application/json' || h['Accept-Encoding'] !== 'identity' ||
      !Number.isSafeInteger(h['Content-Length']) || h['Content-Length'] < 1 || h['Content-Length'] > 524288 ||
      !owner.armedCase || owner.inflight ||
      (owner.recovery && (owner.armedCase !== 'pilot-stream' || owner.fakeRequests >= 1))) fail();
}

class FakeClientRequest extends Writable {
  constructor(options, callback, state, caseId) {
    super({ autoDestroy: false });
    this.options = options; this.callback = callback; this.state = state; this.caseId = caseId;
    this.parts = []; this.bytes = 0; this.incoming = null; this.timer = null;
    this.once('finish', () => queueMicrotask(() => this.respond()));
  }
  _write(chunk, _encoding, done) {
    this.bytes += chunk.length;
    if (this.bytes > this.options.headers['Content-Length'] || this.bytes > 524288) { done(new Error('MOCK_FIXTURE_DENIED')); return; }
    this.parts.push(Buffer.from(chunk)); done();
  }
  _final(done) {
    try {
      if (this.bytes !== this.options.headers['Content-Length']) fail();
      const body = JSON.parse(Buffer.concat(this.parts).toString('utf8'));
      this.fixture = fixtureFor(this.caseId, body);
      this.parts = []; done();
    } catch { this.parts = []; done(new Error('MOCK_FIXTURE_DENIED')); }
  }
  respond() {
    if (this.destroyed) return;
    const incoming = new PassThrough();
    incoming.statusCode = 200; incoming.complete = false;
    incoming.headers = { 'content-type': this.fixture.contentType };
    this.incoming = incoming;
    incoming.once('close', () => { clearTimeout(this.timer); this.state.inflight = null; });
    this.callback(incoming);
    if (this.destroyed || incoming.destroyed) return;
    if (this.fixture.body) { incoming.complete = true; incoming.end(this.fixture.body); return; }
    let index = 0;
    const emit = () => {
      if (this.destroyed || incoming.destroyed) return;
      const next = index < this.fixture.frames.length ? this.fixture.frames[index++] : this.fixture.repeatFrame;
      if (next === undefined) { incoming.complete = true; incoming.end(); return; }
      // EOF must accompany [DONE], not trail it by a pacing interval: an SDK
      // may close as soon as it sees [DONE], and that must settle normally.
      if (index === this.fixture.frames.length && !this.fixture.repeatFrame) {
        incoming.complete = true; incoming.end(next); return;
      }
      incoming.write(next);
      this.timer = setTimeout(emit, this.fixture.intervalMs);
    };
    this.timer = setTimeout(emit, this.fixture.initialDelayMs);
  }
  _destroy(error, done) {
    clearTimeout(this.timer); this.parts = [];
    if (this.incoming && !this.incoming.destroyed) {
      // Like an interrupted IncomingMessage: complete stays false, followed by
      // aborted/close. The broker itself must observe its product HTTP client.
      if (!this.incoming.complete) this.incoming.emit('aborted');
      this.incoming.destroy();
    }
    this.state.inflight = null;
    done(error);
  }
}

function installOfflineBoundary() {
  if (installed) return;
  // No original https.request/get or tls.connect is saved: there is no provider
  // fallback even on a malformed request, missing fixture, or closed broker.
  lock(https, 'request', (options, callback) => {
    validateRequest(options, callback);
    const state = owner; const caseId = state.armedCase; state.armedCase = null;
    const request = new FakeClientRequest(options, callback, state, caseId);
    state.inflight = request; state.fakeRequests += 1;
    return request;
  });
  lock(https, 'get', fail);
  lock(tls, 'connect', fail);
  // Other transports (HTTP, WebSocket, fetch/undici) cannot bypass the fake by
  // opening a non-loopback socket. Real local broker and CDP traffic still run.
  const connectLoopback = net.Socket.prototype.connect;
  lock(net.Socket.prototype, 'connect', function (...args) {
    const normalized = Array.isArray(args[0]) ? args[0] : args;
    const first = normalized[0];
    const options = first && typeof first === 'object' ? first : { port: first, host: normalized[1] };
    if (options.path || !['127.0.0.1', '::1'].includes(options.host) ||
        !Number.isInteger(Number(options.port)) || Number(options.port) < 1 || Number(options.port) > 65535) fail();
    return connectLoopback.apply(this, args);
  });
  installed = true;
}

// Evaluate byte-identical reviewed broker/core source in a separate clock realm.
// Only this broker sees the fixed reviewed price window. The harness, real EXE,
// synthetic event dates and UI timeouts keep the actual system date unchanged.
// The immutable, allowlisted Node transports are shared with this offline
// process; neither clock nor transport injection is added to the live API.
function loadOfflineBroker() {
  const started = performance.now();
  class MockDate extends Date {
    static now() { return MOCK_EPOCH + Math.floor(performance.now() - started); }
  }
  const context = vm.createContext({ Date: MockDate, Buffer, TextDecoder, performance,
    setTimeout, clearTimeout, process });
  const allowedBuiltins = new Set(['node:http', 'node:https', 'node:fs', 'node:path', 'node:tls', 'node:crypto']);
  const cache = new Map();
  const load = (name) => {
    if (!['broker.cjs', 'broker-core.cjs'].includes(name)) fail();
    if (cache.has(name)) return cache.get(name).exports;
    const filename = path.join(__dirname, name);
    const module = { exports: {} }; cache.set(name, module);
    const localRequire = (id) => {
      if (allowedBuiltins.has(id)) return require(id);
      if (name === 'broker.cjs' && id === './broker-core.cjs') return load('broker-core.cjs');
      if (id === './carry-forward-policy.cjs') {
        // Share the original policy closure/WeakSet across VM realms. Only
        // this permanently network-locked loader may substitute MOCK mode;
        // production createBroker has no mode/transport/capability override.
        if (!installed || !owner) fail();
        return Object.freeze({ ...carryPolicy, validateCarryForward(carry, mode) {
          if (!installed || !owner || owner.closed || mode !== 'live') fail();
          return carryPolicy.validateCarryForward(carry, 'mock');
        } });
      }
      fail();
    };
    const code = fs.readFileSync(filename, 'utf8');
    const factory = new vm.Script(`(function (exports, require, module, __filename, __dirname) {\n${code}\n})`, { filename })
      .runInContext(context, { timeout: 1000 });
    factory(module.exports, localRequire, module, filename, __dirname);
    return module.exports;
  };
  return load('broker.cjs');
}

async function createOffline(recovery, { ledgerPath, sessionId, runId, runAttempt, helperCommit, requestCommit, executionStartedAt } = {}) {
  if (creating || owner) fail();
  installOfflineBoundary(); // Must precede even requiring the real live broker.
  creating = true;
  const state = { recovery, armedCase: null, prepared: new Set(), inflight: null, fakeRequests: 0, closed: false };
  owner = state;
  try {
    const { createBroker } = loadOfflineBroker();
    const broker = await createBroker({ providerKey: FAKE_KEY, ledgerPath, sessionId, runId, runAttempt, helperCommit, requestCommit,
      executionStartedAt, ...(recovery ? { carryForward: carryPolicy.createMockCarryForward() } : {}) });
    let closing;
    const offline = Object.freeze({
      mode: MODE, origin: broker.origin, remainingMs: () => broker.remainingMs(),
      prepareCase(caseId) {
        if (!CASE_IDS.includes(caseId)) fail();
        const prepared = broker.prepareCase(caseId); state.prepared.add(caseId); return prepared;
      },
      armCase(caseId) {
        if (!state.prepared.has(caseId)) fail();
        broker.armCase(caseId); state.armedCase = caseId;
      },
      cancelCase() { state.armedCase = null; broker.cancelCase(); },
      snapshot() {
        return { ...broker.snapshot(), mode: MODE,
          mock: { transport: 'IN_PROCESS_SYNTHETIC_HTTPS', externalNetwork: 'DENIED',
            fakeRequests: state.fakeRequests, clockEpochUtc: new Date(MOCK_EPOCH).toISOString(), clockScope: 'BROKER_VM_ONLY',
            realProviderCalls: 0, usageIsSynthetic: true } };
      },
      close() {
        state.closed = true;
        if (!closing) closing = broker.close().finally(() => {
          state.inflight?.destroy(); state.armedCase = null;
          if (owner === state) owner = null;
        });
        return closing;
      },
    });
    offlineBrokers.set(offline, state);
    return offline;
  } catch (error) { state.inflight?.destroy(); owner = null; throw error; }
  finally { creating = false; }
}
const createOfflineBroker = options => createOffline(false, options);
// Fixed synthetic seed only: callers cannot supply or restore a ledger. This
// entry retains the same network-lock/private-brand checks as the seven cases.
const createOfflineRecoveryBroker = options => createOffline(true, options);
module.exports = { createOfflineBroker, createOfflineRecoveryBroker, isOfflineBroker };
