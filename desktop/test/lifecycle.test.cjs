'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const http = require('node:http');
const { PassThrough } = require('node:stream');
const { TOKEN_HEADER, isLocalOrigin, isSameOrigin, waitForReady, checkHealth, stopBackend } = require('../lifecycle.cjs');
function backend() {
  const child = new EventEmitter();
  Object.assign(child, { pid: 42, stdout: new PassThrough(), stdin: new PassThrough(), exitCode: null, signalCode: null });
  child.kill = () => { child.signalCode = 'SIGTERM'; child.emit('exit', null, 'SIGTERM'); };
  return child;
}
function ready(child, changes = {}) {
  child.stdout.write(`${JSON.stringify({type: 'offerpilot.desktop.ready', protocol: 1, origin: 'http://127.0.0.1:12345', pid: 42, ...changes})}\n`);
}
test('accept only a literal IPv4 loopback origin, never URL credentials or paths', () => {
  assert.equal(isLocalOrigin('http://127.0.0.1:12345'), true);
  for (const value of ['https://127.0.0.1:123', 'http://localhost:123', 'http://127.0.0.1:0', 'http://127.0.0.1:123/a', 'http://evil.test:123', 'http://user@127.0.0.1:123', 'http://127.0.0.1:123?x=1']) assert.equal(isLocalOrigin(value), false, value);
});
test('origin checks do not leak token to a changed port or deceptive hostname', () => {
  const origin = 'http://127.0.0.1:12345';
  assert.equal(isSameOrigin(`${origin}/api/settings`, origin), true);
  for (const url of ['http://127.0.0.1:12346/', 'http://127.0.0.1.evil.test:12345/', 'file:///tmp/a', 'invalid']) assert.equal(isSameOrigin(url, origin), false);
});
test('readiness validates process identity and protocol and tolerates unrelated output', async () => {
  const child = backend();
  const promise = waitForReady(child, 100);
  child.stdout.write('ordinary output\n');
  ready(child);
  assert.equal((await promise).origin, 'http://127.0.0.1:12345');
  for (const changes of [{pid: 43}, {protocol: 2}, {origin: 'http://evil.test:1234'}]) {
    const other = backend(); const result = waitForReady(other, 100); ready(other, changes);
    await assert.rejects(result, /Invalid backend/);
  }
});
test('Windows development redirector accepts only the owned one-hop child', async () => {
  const child = backend();
  const result = waitForReady(child, 100, { allowPythonRedirector: true });
  ready(child, { pid: 43, parent_pid: child.pid });
  assert.equal((await result).pid, 43);

  // A direct interpreter still works when the optional redirector path is enabled.
  const direct = backend();
  const directResult = waitForReady(direct, 100, { allowPythonRedirector: true });
  ready(direct, { parent_pid: 1 });
  assert.equal((await directResult).pid, direct.pid);
});
test('redirector identity cannot weaken the default packaged identity contract', async () => {
  for (const options of [undefined, { allowPythonRedirector: false }, { allowPythonRedirector: 'true' }]) {
    const child = backend();
    const result = waitForReady(child, 100, options);
    ready(child, { pid: 43, parent_pid: child.pid });
    await assert.rejects(result, /Invalid backend/);
  }
});
test('redirector readiness rejects unrelated parents, malformed PIDs and other protocol failures', async () => {
  for (const changes of [
    { parent_pid: undefined }, { parent_pid: 1 }, { parent_pid: '42' },
    { parent_pid: null }, { parent_pid: 43 },
    { pid: undefined }, { pid: null }, { pid: '43' }, { pid: true },
    { pid: 0 }, { pid: -1 }, { pid: 43.5 }, { pid: Number.MAX_SAFE_INTEGER + 1 },
    { protocol: 2 }, { origin: 'http://evil.test:1234' },
  ]) {
    const child = backend();
    const result = waitForReady(child, 100, { allowPythonRedirector: true });
    ready(child, { pid: 43, parent_pid: child.pid, ...changes });
    await assert.rejects(result, /Invalid backend/, JSON.stringify(changes));
  }
  for (const pid of [undefined, null, 0, -1, 42.5, '42', Number.MAX_SAFE_INTEGER + 1]) {
    const child = backend();
    child.pid = pid;
    const result = waitForReady(child, 100, { allowPythonRedirector: true });
    ready(child, { pid: 43, parent_pid: pid });
    await assert.rejects(result, /Invalid backend/);
  }
});
test('redirector readiness still requires the authenticated health check', async (t) => {
  const token = 'fresh-session-token';
  const server = http.createServer((request, response) => {
    assert.equal(request.url, '/api/health');
    const authenticated = request.headers[TOKEN_HEADER.toLowerCase()] === token;
    response.writeHead(authenticated ? 200 : 401).end();
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const child = backend();
  const result = waitForReady(child, 100, { allowPythonRedirector: true });
  ready(child, { pid: 43, parent_pid: child.pid, origin });
  const metadata = await result;
  await assert.rejects(checkHealth(metadata.origin, 'wrong-token'), /health check failed/);
  await checkHealth(metadata.origin, token);
});
test('readiness failure, spawn error, early exit and timeout reject', async () => {
  const child = backend(); const result = waitForReady(child, 100);
  child.stdout.write('{"type":"offerpilot.desktop.error"}\n');
  await assert.rejects(result, /startup failed/);
  const failed = backend(); const error = waitForReady(failed, 100); failed.emit('error', new Error('ENOENT'));
  await assert.rejects(error, /Could not start/);
  const exited = backend(); const exit = waitForReady(exited, 100); exited.emit('exit', 1);
  await assert.rejects(exit, /exited before startup/);
  await assert.rejects(waitForReady(backend(), 5), /timed out/);
});
test('shutdown closes stdin, waits for exit, and kills only after grace period', async () => {
  const child = backend();
  child.stdin.once('finish', () => setTimeout(() => {child.exitCode = 0; child.emit('exit', 0);}, 2));
  await stopBackend(child, 100);
  assert.equal(child.stdin.writableEnded, true);
  assert.equal(child.exitCode, 0);
  assert.equal(child.signalCode, null);
  const stuck = backend(); await stopBackend(stuck, 5);
  assert.equal(stuck.signalCode, 'SIGTERM');
});
