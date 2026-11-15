import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import http from 'node:http';
import { rendererAdmissionCapture, installAdmissionCapture } from '../admission-capture.mjs';
const origin = 'http://127.0.0.1:41000', route = `${origin}/api/pilot/runtime/v1/turns`;
const nonce = '7abca123-1234-4234-8234-123456789abc';
const id = '8abca123-1234-4234-8234-123456789abc';
const body = JSON.stringify({ message: 'PRIVATE_SYNTHETIC', request_id: id, conversation_id: 0 });
function fixture(original, selectedOrigin = origin) {
  const context = vm.createContext({ URL, TextEncoder, Reflect, Promise, setTimeout, clearTimeout,
    location: { origin: selectedOrigin, href: `${selectedOrigin}/` },
    window: { offerpilotDesktop: { role: 'owner' }, fetch: original } });
  const page = { async evaluate(fn, arg) { context.args = structuredClone(arg); return vm.runInContext(`(${fn.toString()})(args)`, context); } };
  const call = (operation, changes = {}) => page.evaluate(rendererAdmissionCapture, { operation, origin: selectedOrigin, nonce, ...changes });
  return { context, page, call };
}
for (const abort of [false, true]) test(`request captured before original ${abort ? 'abort' : 'hanging'} Promise settles; no response access`, async () => {
  let reject, received, calls = 0;
  const pending = new Promise((_, r) => { reject = r; }); pending.catch(() => {});
  const original = function (...args) { received = args; calls++; return pending; };
  const f = fixture(original); await f.call('install');
  const options = { method: 'POST', body, get headers() { assert.fail('headers must not be read'); } };
  assert.equal(f.context.window.fetch(route, options), pending);
  assert.equal(received[1], options); assert.equal(calls, 1);
  if (abort) reject(new Error('synthetic product recovery abort'));
  const value = await f.call('read'); assert.equal(value.phase, 'READY'); assert.equal(value.requestId, id);
  assert.deepEqual(Object.keys(value).sort(), ['count', 'fault', 'phase', 'requestId']);
  assert.doesNotMatch(JSON.stringify(value), /PRIVATE|message|headers|body/);
  assert.equal((await f.call('dispose')).phase, 'DISPOSED'); assert.equal(f.context.window.fetch, original);
});
test('factory binds renderer request ID to independently observed Playwright request', async () => {
  const f = fixture(() => Promise.resolve(new Response('unchanged')));
  const capture = await installAdmissionCapture(f.page, origin, action => action());
  const response = await f.context.window.fetch(route, { method: 'POST', body });
  assert.equal(await response.text(), 'unchanged');
  assert.equal(await capture.request(id), id); await capture.assertValid(true);
  await assert.rejects(capture.request(nonce), /ADMISSION_CAPTURE_INVALID/); await capture.dispose();
});
for (const invalid of [null, '{}', '{"request_id":"bad","conversation_id":0}',
  JSON.stringify({ request_id: id, conversation_id: 1 }),
  `{"request_id":"${id}","request_id":"${id}","conversation_id":0}`,
  JSON.stringify({ request_id: id, conversation_id: 0, message: '界'.repeat(23000) }),
  '{"request_id":"'+id+'","conversatio\\u006e_id":0}', ' '.repeat(65537)]) {
  test('invalid, ambiguous or oversize request is not retained or adopted', async () => {
    let calls = 0; const f = fixture(() => { calls++; return Promise.resolve(); }); await f.call('install');
    await f.context.window.fetch(route, { method: 'POST', body: invalid });
    const value = await f.call('read'); assert.equal(value.phase, 'FAILED'); assert.equal(value.requestId, null);
    assert.equal(calls, 1); await f.call('dispose');
  });
}
test('duplicates including late same ID invalidate completed observation', async () => {
  const original = () => Promise.resolve(); const f = fixture(original);
  const capture = await installAdmissionCapture(f.page, origin, action => action());
  await f.context.window.fetch(route, { method: 'POST', body }); await capture.request(id);
  await f.context.window.fetch(route, { method: 'POST', body });
  await assert.rejects(capture.assertValid(true)); await assert.rejects(capture.dispose());
  assert.equal(f.context.window.fetch, original);
});
test('wrong nonce and pre-existing capture cannot be adopted or removed', async () => {
  const f = fixture(() => Promise.resolve()); await f.call('install'); const original = f.context.window.fetch;
  assert.equal((await f.call('read', { nonce: id })).phase, 'FAILED');
  assert.equal((await f.call('dispose', { nonce: id })).phase, 'FAILED');
  await assert.rejects(installAdmissionCapture(f.page, origin, action => action()));
  assert.equal(f.context.window.fetch, original); await f.call('dispose');
});
test('cleanup does not overwrite third-party replacement', async () => {
  const f = fixture(() => Promise.resolve()); await f.call('install');
  const replacement = () => {}; f.context.window.fetch = replacement;
  assert.equal((await f.call('dispose')).phase, 'FAILED'); assert.equal(f.context.window.fetch, replacement);
  assert.equal(Object.hasOwn(f.context.window, '__offerpilotAdmissionCapture'), false);
});
test('installation timeout still restores original fetch', async () => {
  const original = () => Promise.resolve(); const f = fixture(original);
  await assert.rejects(installAdmissionCapture(f.page, origin, async action => { await action(); throw new Error('timeout'); }));
  assert.equal(f.context.window.fetch, original);
});
test('wrong route, origin and verb pass unchanged but never become proof', async () => {
  let calls = 0; const f = fixture(() => { calls++; return Promise.resolve(); }); await f.call('install');
  for (const [url, method] of [[route, 'GET'], [`${route}?extra=1`, 'POST'], ['https://example.invalid/api/pilot/runtime/v1/turns', 'POST']]) {
    await f.context.window.fetch(url, { method, body });
  }
  assert.equal((await f.call('read')).count, 0); assert.equal(calls, 3); await f.call('dispose');
});
test('real loopback aborted POST leaves request identity available; no replay', async () => {
  let calls = 0;
  const server = http.createServer((req, res) => { calls++; req.resume(); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const ownOrigin = `http://127.0.0.1:${server.address().port}`;
  const f = fixture(fetch, ownOrigin); const controller = new AbortController(); let capture;
  try {
    capture = await installAdmissionCapture(f.page, ownOrigin, action => action());
    const pending = f.context.window.fetch(`${ownOrigin}/api/pilot/runtime/v1/turns`, { method: 'POST', body, signal: controller.signal });
    const rejected = assert.rejects(pending, { name: 'AbortError' });
    for (let n = 0; n < 100 && calls === 0; n++) await new Promise(r => setTimeout(r, 5));
    assert.equal(calls, 1); controller.abort(); await rejected;
    assert.equal(await capture.request(id), id); await capture.assertValid(true);
  } finally { controller.abort(); await capture?.dispose(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
