'use strict';

const http = require('node:http');
const https = require('node:https');
const fs = require('node:fs');
const path = require('node:path');
const tls = require('node:tls');
const { randomBytes, timingSafeEqual } = require('node:crypto');
const { POLICY, CASES, BudgetError, Ledger, envelope, UsageParser } = require('./broker-core.cjs');

// No URL argument, fetch, SDK, proxy environment, redirect handler, or retry path.
const UPSTREAM = Object.freeze({ protocol: 'https:', hostname: 'api.deepseek.com', port: 443,
  path: '/chat/completions', method: 'POST', agent: false, rejectUnauthorized: true,
  servername: 'api.deepseek.com', minVersion: 'TLSv1.2', ca: tls.rootCertificates });
const errorCode = (error) => error instanceof BudgetError ? error.code : 'PROTOCOL';
const safeReply = (res, code, status = 403) => {
  if (res.destroyed || res.writableEnded) return;
  if (res.headersSent) { res.destroy(); return; }
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', Connection: 'close' });
  res.end(JSON.stringify({ error: { type: 'validation_budget', code } }));
};
function equalToken(actual, expected) {
  if (typeof actual !== 'string' || typeof expected !== 'string') return false;
  const a = Buffer.from(actual); const b = Buffer.from(`Bearer ${expected}`);
  return a.length === b.length && timingSafeEqual(a, b);
}

const PROVENANCE = Object.freeze({ productCommit: 'c040a5d2f1949ff8a4ae806e7c3b593c6481e6d0', buildRunId: '37806395272',
  artifactId: '11564445795', installerSha256: '9e33c18f5c83d01bd23ebed01e22d5952a72787fef48cefec8dd875686e46ea7',
  priceCheckedAt: '2026-10-08', validUntilUtc: '2026-10-08T23:59:59.999Z', priceBasis: 'PEAK_CNY', mode: 'FIXED_EXE_UI_BUDGET_BROKER' });

function currentPriceWindow(timeoutMs = 0) {
  const now = Date.now();
  if (new Date(now).toISOString().slice(0, 10) !== PROVENANCE.priceCheckedAt ||
      now + timeoutMs > Date.parse(PROVENANCE.validUntilUtc)) throw new BudgetError('EXPIRED');
}

async function createBroker({ providerKey, ledgerPath, sessionId, runId, runAttempt, helperCommit, requestCommit } = {}) {
  currentPriceWindow();
  // Fail before binding if process-wide Node tracing could disclose HTTP headers.
  if (process.env.NODE_DEBUG || process.env.NODE_DEBUG_NATIVE || process.env.NODE_OPTIONS ||
      process.env.NODE_EXTRA_CA_CERTS || process.env.SSL_CERT_FILE || process.env.SSL_CERT_DIR ||
      (process.env.NODE_TLS_REJECT_UNAUTHORIZED && process.env.NODE_TLS_REJECT_UNAUTHORIZED !== '1') ||
      typeof providerKey !== 'string' || !/^[\x21-\x7e]{8,512}$/.test(providerKey)) throw new BudgetError('AUTH');
  if (typeof ledgerPath !== 'string' || !path.isAbsolute(ledgerPath) ||
      typeof sessionId !== 'string' || !/^[a-zA-Z0-9_-]{1,96}$/.test(sessionId) ||
      typeof runId !== 'string' || !/^[0-9]{1,20}$/.test(runId) || runAttempt !== 1 ||
      typeof helperCommit !== 'string' || !/^[a-f0-9]{40}$/.test(helperCommit) ||
      typeof requestCommit !== 'string' || !/^[a-f0-9]{40}$/.test(requestCommit)) throw new BudgetError('LEDGER');
  const ledger = new Ledger();
  const binding = Object.freeze({ ...PROVENANCE, sessionId, runId, runAttempt, helperCommit, requestCommit });
  let journal; let sequence = 0; let journalFailed = false;
  const snapshot = () => ({ ...ledger.snapshot(), provenance: { ...binding }, journalFailed });
  const persist = (event) => {
    if (journalFailed) throw new BudgetError('LEDGER');
    try {
      const line = Buffer.from(JSON.stringify({ sequence: ++sequence, event, ...snapshot() }) + '\n');
      let offset = 0;
      while (offset < line.length) {
        const written = fs.writeSync(journal, line, offset, line.length - offset);
        if (written <= 0) throw new BudgetError('LEDGER');
        offset += written;
      }
      fs.fsyncSync(journal);
    } catch {
      journalFailed = true; ledger.close(); throw new BudgetError('LEDGER');
    }
  };
  try { journal = fs.openSync(ledgerPath, 'wx', 0o600); persist('OPEN'); }
  catch { try { if (journal !== undefined) fs.closeSync(journal); } catch {} throw new BudgetError('LEDGER'); }
  const tokens = new Map();
  let armedToken = null; let activeAbort = null; let closing = null;
  const sockets = new Set();
  const socketAborts = new WeakMap();
  const brokerDestroyedSockets = new WeakSet();
  const server = http.createServer({ maxHeaderSize: 8192, requestTimeout: 15000, headersTimeout: 10000 }, (req, res) => {
    let ticket; let upstream; let response; let timer; let terminal = false; let successUsage = null; let requestDeadline = 0;
    let parser; let requestBytes = 0; let chunks = [];
    const finish = (code, usage = null) => {
      if (terminal) return;
      terminal = true; clearTimeout(timer);
      if (ticket) {
        ledger.finish(ticket, code, usage);
        if (['USAGE', 'PROTOCOL'].includes(code) && ledger.snapshot().sentRequests > 0) ledger.close();
        try { persist('FINISH'); } catch { ledger.retainUncertain(ticket); code = 'LEDGER'; }
      } else ledger.deny(code);
      if (activeAbort === abort) activeAbort = null;
      if (socketAborts.get(req.socket) === abort) socketAborts.delete(req.socket);
      if (code !== 'SETTLED') {
        response?.destroy(); upstream?.destroy(); safeReply(res, code);
      }
      chunks = [];
    };
    const abort = (code = 'CANCELLED') => finish(code);
    const clientDisconnect = () => {
      // finish() marks terminal before any broker/upstream-caused socket teardown.
      // Never reinterpret our own timeout, parser failure or cleanup as a UI cancellation.
      if (terminal) return;
      if (brokerDestroyedSockets.has(req.socket)) { finish('CANCELLED'); return; }
      if (ticket) ledger.markTransport(ticket, 'clientDisconnectObserved');
      finish('DISCONNECT');
    };
    req.on('error', clientDisconnect);
    res.on('error', clientDisconnect);
    res.on('close', () => { if (!res.writableFinished) clientDisconnect(); });
    res.on('finish', () => { if (successUsage) finish('SETTLED', successUsage); });
    if (req.socket.remoteAddress !== '127.0.0.1' || !equalToken(req.headers.authorization, armedToken)) {
      finish('AUTH'); req.resume(); return;
    }
    if (req.method !== 'POST' || !['/v1/chat/completions', '/chat/completions'].includes(req.url) ||
        !/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(req.headers['content-type'] || '') ||
        req.headers['content-encoding'] || req.headers.expect || req.headers.origin) {
      finish('ROUTE'); req.resume(); return;
    }
    try { ticket = ledger.claim(); } catch (error) { finish(errorCode(error)); req.resume(); return; }
    // A case token is consumed at ingress, so SDK retries cannot use a later case's grant.
    armedToken = null; activeAbort = abort; socketAborts.set(req.socket, abort);
    requestDeadline = performance.now() + ticket.timeoutMs;
    req.socket.setTimeout(ticket.timeoutMs);
    timer = setTimeout(() => finish('TIMEOUT'), Math.min(ticket.timeoutMs, ledger.remainingMs()));
    req.on('aborted', clientDisconnect);
    req.on('data', (chunk) => {
      if (terminal) return;
      requestBytes += chunk.length;
      if (requestBytes > POLICY.requestBytes) { finish('BODY'); req.resume(); return; }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (terminal) return;
      let prepared; let encoded;
      try {
        const raw = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
        chunks = [];
        prepared = envelope(JSON.parse(raw), ticket.maxTokens);
        encoded = Buffer.from(JSON.stringify(prepared.body));
        if (encoded.length > POLICY.requestBytes) throw new BudgetError('BODY');
        parser = new UsageParser(prepared.body.stream === true, prepared.maxTokens);
        currentPriceWindow(ticket.timeoutMs);
        ledger.reserve(ticket, prepared.maxTokens, prepared.envelope);
        persist('RESERVE');
        // fsync is synchronous: timers cannot fire if it blocks across a deadline.
        // Recheck both clocks after durable reservation, immediately before outbound.
        currentPriceWindow(ticket.timeoutMs);
        if (ledger.remainingMs() <= 0) throw new BudgetError('DEADLINE');
        if (performance.now() >= requestDeadline) throw new BudgetError('TIMEOUT');
        // This call is the only possible real upstream request. Reserve precedes it.
        upstream = https.request({ ...UPSTREAM, headers: { Authorization: `Bearer ${providerKey}`,
          'Content-Type': 'application/json', 'Content-Length': encoded.length, 'Accept-Encoding': 'identity' } }, (incoming) => {
          response = incoming;
          response.on('error', () => finish('UPSTREAM'));
          response.on('aborted', () => finish('UPSTREAM_DISCONNECT'));
          response.on('close', () => { if (!response.complete) finish('UPSTREAM_DISCONNECT'); });
          if (terminal) { response.destroy(); return; }
          ledger.markTransport(ticket, 'upstreamResponded');
          if (response.statusCode >= 300 && response.statusCode < 400) { finish('REDIRECT'); return; }
          if (response.statusCode !== 200) { finish('UPSTREAM'); return; }
          const type = prepared.body.stream ? 'text/event-stream' : 'application/json';
          if (response.headers['content-encoding'] && response.headers['content-encoding'] !== 'identity' ||
              !(response.headers['content-type'] || '').toLowerCase().startsWith(type)) { finish('PROTOCOL'); return; }
          res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store', Connection: 'close' });
          response.on('data', (chunk) => {
            if (terminal) return;
            try { parser.push(chunk); } catch (error) { finish(errorCode(error)); return; }
            // Bound downstream buffering. No raw prompt/response is written to disk or audit.
            if (!res.write(chunk)) response.pause();
          });
          res.on('drain', () => { if (!terminal) response.resume(); });
          response.on('end', () => {
            if (terminal) return;
            if (!response.complete) { finish('UPSTREAM_DISCONNECT'); return; }
            try { successUsage = parser.end(); } catch (error) { finish(errorCode(error)); return; }
            res.end();
          });
        });
        upstream.on('error', () => finish('UPSTREAM'));
        upstream.once('finish', () => { if (!terminal) ledger.markTransport(ticket, 'outboundStarted'); });
        upstream.end(encoded);
      } catch (error) { finish(errorCode(error)); }
    });
  });
  server.maxConnections = 4;
  server.on('connection', (socket) => {
    sockets.add(socket); socket.on('close', () => sockets.delete(socket));
    socket.setTimeout(15000, () => {
      brokerDestroyedSockets.add(socket); socketAborts.get(socket)?.('TIMEOUT'); socket.destroy();
    });
  });
  // HTTP parsing failures must never print the parser's rawPacket (headers or bodies).
  server.on('clientError', (_error, socket) => {
    brokerDestroyedSockets.add(socket); socketAborts.get(socket)?.('PROTOCOL');
    ledger.deny('PROTOCOL'); socket.destroy();
  });
  const close = async () => {
    if (closing) return closing;
    armedToken = null; activeAbort?.('CANCELLED'); ledger.close(); clearTimeout(runTimer);
    try { persist('CLOSE'); } catch { /* Prior durable reservation remains authoritative. */ }
    try { fs.closeSync(journal); } catch { journalFailed = true; }
    closing = new Promise((resolve) => server.close(resolve)).then(() => {
      if (journalFailed) throw new BudgetError('LEDGER');
    });
    for (const socket of sockets) { brokerDestroyedSockets.add(socket); socket.destroy(); }
    return closing;
  };
  let runTimer;
  try { await new Promise((resolve, reject) => {
    const failed = () => reject(new BudgetError('UPSTREAM'));
    server.once('error', failed);
    server.listen(0, '127.0.0.1', () => { server.removeListener('error', failed); resolve(); });
  }); } catch { ledger.close(); try { fs.closeSync(journal); } catch {} throw new BudgetError('UPSTREAM'); }
  server.on('error', () => { void close().catch(() => {}); });
  runTimer = setTimeout(() => { activeAbort?.('DEADLINE'); void close().catch(() => {}); }, Math.min(ledger.remainingMs(), Date.parse(PROVENANCE.validUntilUtc) - Date.now()));
  return Object.freeze({
    origin: `http://127.0.0.1:${server.address().port}`,
    prepareCase(caseId) {
      if (!Object.hasOwn(CASES, caseId) || tokens.has(caseId)) throw new BudgetError('CASE');
      if (ledger.snapshot().closed || ledger.remainingMs() <= 0) throw new BudgetError('CLOSED');
      const clientToken = randomBytes(32).toString('hex'); tokens.set(caseId, clientToken);
      return Object.freeze({ clientToken });
    },
    armCase(caseId) {
      if (!tokens.has(caseId)) throw new BudgetError('CASE');
      ledger.armCase(caseId); armedToken = tokens.get(caseId);
    },
    cancelCase() { armedToken = null; activeAbort?.('CANCELLED'); ledger.cancelCase(); },
    snapshot, close,
  });
}
module.exports = { createBroker };
