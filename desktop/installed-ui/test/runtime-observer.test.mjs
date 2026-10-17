import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { observeRuntime } from '../runtime-observer.mjs';

const ORIGIN = 'http://127.0.0.1:8000';
const KIT = `${ORIGIN}/api/applications/17/material-kit`;
const NOT_FOUND = 'Failed to load resource: the server responded with a status of 404 (Not Found)';
function fixture() {
  const page = new EventEmitter();
  const mainFrame = {};
  page.url = () => `${ORIGIN}/?token=page-secret`;
  page.mainFrame = () => mainFrame;
  const runtime = observeRuntime(page);
  const request = (url = KIT, method = 'GET') => ({ url: () => url, method: () => method });
  const response = (url = KIT, status = 404, method = 'GET') => page.emit('response', {
    url: () => url, status: () => status, request: () => request(url, method),
  });
  const consoleError = (url = KIT, text = NOT_FOUND, args = []) => page.emit('console', {
    type: () => 'error', text: () => text, location: () => ({ url, lineNumber: 0, columnNumber: 0 }), args: () => args,
  });
  return { page, runtime, request, response, consoleError, mainFrame };
}

for (const order of ['response-first', 'console-first']) {
  test(`only the matching expected GET 404 is nonblocking, ${order}`, () => {
    const { runtime, response, consoleError } = fixture();
    if (order === 'response-first') { response(); consoleError(); }
    else { consoleError(); response(); }
    const snapshot = runtime.snapshot();
    assert.deepEqual(snapshot.classifications, { 'expected-resource-console': 1 });
    assert.equal(snapshot.ownCriticalFailureCount, 0);
    assert.equal(snapshot.requestFailureCount, 1);
    assert.deepEqual(snapshot.ownRequestFailures, [{ category: 'own-api', status: 404, expectedAbsent: true }]);
  });
}

test('real 500, unmatched 404 and unrelated resource errors stay blocking', () => {
  const { runtime, response, consoleError } = fixture();
  response(KIT, 500);
  consoleError(KIT, NOT_FOUND.replace('404 (Not Found)', '500 (Internal Server Error)'));
  consoleError();
  consoleError(`${ORIGIN}/assets/missing.js`);
  const result = runtime.snapshot();
  assert.equal(result.ownCriticalFailureCount, 1);
  assert.deepEqual(result.classifications, { 'unclassified-console-error': 3 });
});

test('wrong URL, origin, query, method, and missing actual request method cannot match', () => {
  for (const [responseUrl, consoleUrl, method] of [
    [KIT, `${ORIGIN}/api/applications/18/material-kit`, 'GET'],
    [KIT, `${KIT}?token=other-secret`, 'GET'],
    [KIT, 'https://elsewhere.invalid/api/applications/17/material-kit', 'GET'],
    ['https://elsewhere.invalid/api/applications/17/material-kit', KIT, 'GET'],
    [KIT, KIT, 'POST'], [KIT, KIT, 'HEAD'], [KIT, KIT, 'DELETE'],
  ]) {
    for (const consoleFirst of [true, false]) {
      const { runtime, response, consoleError } = fixture();
      if (consoleFirst) consoleError(consoleUrl);
      response(responseUrl, 404, method);
      if (!consoleFirst) consoleError(consoleUrl);
      assert.deepEqual(runtime.snapshot().classifications, { 'unclassified-console-error': 1 });
    }
  }
  const { page, runtime, consoleError } = fixture();
  page.emit('response', { url: () => KIT, status: () => 404 });
  consoleError();
  assert.deepEqual(runtime.snapshot().classifications, { 'unclassified-console-error': 1 });
});

test('fake resource messages and script-generated copies cannot borrow a response', () => {
  for (const [text, args] of [
    [`prefix ${NOT_FOUND}`, []], [`${NOT_FOUND} token=secret`, []],
    [NOT_FOUND.replace('404', '403'), []], [NOT_FOUND, [{ value: 'script argument' }]],
  ]) {
    const { runtime, response, consoleError } = fixture();
    response(); consoleError(KIT, text, args);
    assert.deepEqual(runtime.snapshot().classifications, { 'unclassified-console-error': 1 });
  }
  const { page, runtime, response } = fixture();
  response();
  page.emit('console', { type: () => 'error', text: () => NOT_FOUND, location: () => ({ url: KIT }) });
  assert.deepEqual(runtime.snapshot().classifications, { 'unclassified-console-error': 1 });
});

test('each response can excuse only one console error', () => {
  const { runtime, response, consoleError } = fixture();
  response(); consoleError(); consoleError();
  assert.deepEqual(runtime.snapshot().classifications, { 'expected-resource-console': 1, 'unclassified-console-error': 1 });
});

test('a snapshot reports unmatched console errors permanently and consumes their late response', () => {
  const { runtime, response, consoleError } = fixture();
  consoleError();
  const before = runtime.snapshot();
  assert.deepEqual(before.classifications, { 'unclassified-console-error': 1 });
  response(); consoleError();
  const after = runtime.snapshot();
  assert.deepEqual(after.classifications, { 'unclassified-console-error': 2 });
  assert.deepEqual(runtime.snapshot(), after, 'repeated snapshots must not double-count errors');
});

test('snapshot boundaries discard stale unmatched response credits', () => {
  const { runtime, response, consoleError } = fixture();
  response(); runtime.snapshot(); consoleError();
  assert.deepEqual(runtime.snapshot().classifications, { 'unclassified-console-error': 1 });
});

test('new same-URL request, nonexpected response, transport failure and main navigation invalidate credits', () => {
  for (const invalidate of [
    ({ page, request }) => page.emit('request', request(KIT, 'POST')),
    ({ response }) => response(KIT, 500),
    ({ response }) => response(KIT, 200),
    ({ page, request }) => page.emit('requestfailed', request()),
    ({ page, mainFrame }) => page.emit('framenavigated', mainFrame),
  ]) {
    const f = fixture();
    f.response(); invalidate(f); f.consoleError();
    assert.deepEqual(f.runtime.snapshot().classifications, { 'unclassified-console-error': 1 });
  }
});

test('pending consoles are retained as blocking errors when request evidence is invalidated', () => {
  const { runtime, response, consoleError } = fixture();
  consoleError(); response(KIT, 500);
  assert.deepEqual(runtime.snapshot().classifications, { 'unclassified-console-error': 1 });
});

test('correlation overflow fails closed without dropping unmatched or later error counts', () => {
  const { runtime, response, consoleError } = fixture();
  for (let i = 0; i < 120; i++) consoleError(`${ORIGIN}/api/applications/${i}/material-kit`);
  response(); consoleError();
  assert.deepEqual(runtime.snapshot().classifications, { 'unclassified-console-error': 121 });
  assert.equal(runtime.snapshot().classifications['expected-resource-console'], undefined);
});

test('response overflow cannot leave reusable credits and request critical counts stay unbounded', () => {
  const { runtime, response, consoleError } = fixture();
  for (let i = 0; i < 120; i++) response(`${ORIGIN}/api/applications/${i}/material-kit`);
  consoleError(); response(KIT, 500); response(`${ORIGIN}/api/required`, 422, 'POST');
  const result = runtime.snapshot();
  assert.deepEqual(result.classifications, { 'unclassified-console-error': 1 });
  assert.equal(result.ownCriticalFailureCount, 2);
  assert.equal(result.requestFailureCount, 122);
  assert.equal(result.ownRequestFailures.length, 100);
  assert.equal(result.bounded, true);
});

test('many sequential expected pairs do not exhaust correlation capacity', () => {
  const { runtime, response, consoleError } = fixture();
  for (let i = 0; i < 150; i++) { response(); consoleError(); }
  assert.deepEqual(runtime.snapshot().classifications, { 'expected-resource-console': 150 });
});

test('snapshots serialize no raw URLs, console text, arguments, tokens or transport details', () => {
  const { page, runtime, response, consoleError, request } = fixture();
  const url = `${KIT}?token=private-response-secret`;
  response(url); consoleError(url);
  consoleError(KIT, 'unrecognized private-console-secret', ['private-argument-secret']);
  page.emit('pageerror', new Error('unsafe-eval private-stack-secret'));
  page.emit('requestfailed', { ...request(`${ORIGIN}/api/private-endpoint-secret`),
    failure: () => ({ errorText: 'net::ERR_CONNECTION_REFUSED private-transport-secret' }) });
  const serialized = JSON.stringify(runtime.snapshot());
  assert.doesNotMatch(serialized, /private-|secret|token|127\.0\.0\.1|http|material-kit|Failed to load|ERR_CONNECTION/);
  assert.equal(runtime.snapshot().ownCriticalFailureCount, 1);
});

test('failed POST preserves the sticky unknown-mutation barrier even for cancellation', () => {
  const { page, runtime, request } = fixture();
  const mutation = { ...request(KIT, 'POST'), failure: () => ({ errorText: 'net::ERR_ABORTED' }) };
  page.emit('request', mutation);
  assert.equal(runtime.hasPendingWrite(), true);
  page.emit('requestfailed', mutation);
  page.emit('requestfinished', mutation);
  assert.equal(runtime.hasPendingWrite(), true);
  assert.equal(runtime.snapshot().uncertainMutationOutcome, true);
  assert.equal(runtime.snapshot().ownCriticalFailureCount, 0);
});
