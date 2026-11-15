import test from 'node:test';
import assert from 'node:assert/strict';
import { createPresenceSuppression, capturePresenceRequest, createSerialRequestQueue } from '../presence-guard.mjs';

const origin = 'http://127.0.0.1:43123';
const identity = '11111111-1111-4111-8111-111111111111';
const client = '22222222-2222-4222-8222-222222222222';
function fixture() {
  const page = {}, frame = {};
  const body = { identity, client_id: client, sequence: 1, visible: true, typing: false,
    work_running: false, awaiting_approval: false, mock_interview: false, quiet: false, failure: false };
  const input = { url: `${origin}/api/companion/presence`, method: 'POST', origin, contentType: 'application/json',
    body: JSON.stringify(body), aborted: true, redirected: false, worker: false, mainFrame: true,
    sourcePage: page, sourceFrame: frame, frameURL: `${origin}/` };
  const context = { origin, ownerPage: page, ownerMainFrame: frame, ownerRole: 'owner', ownerURL: `${origin}/`,
    snapshot: { identity, enabled: false } };
  return { input, context, body };
}
test('early heartbeat is not accepted until actual owner and disabled backend identity are verified', () => {
  const { input, context } = fixture();
  const guard = createPresenceSuppression();
  guard.recordAborted(input);
  assert.deepEqual(guard.summary(), { aborted: 1, validated: 0, pending: 1, rejected: false });
  assert.throws(() => guard.assertSettled());
  guard.verifyContext(context);
  guard.assertSettled();
  assert.deepEqual(guard.summary(), { aborted: 1, validated: 1, pending: 0, rejected: false });
  assert.doesNotMatch(JSON.stringify(guard.summary()), /11111111|22222222|visible|typing/);
});
test('only exact, confirmed-aborted same-origin owner heartbeat is suppressible', () => {
  for (const change of [
    { aborted: false }, { method: 'PUT' }, { method: 'post' }, { url: `${origin}/api/companion/presence?x=1` },
    { url: `${origin}/api/companion/presence#x` }, { url: `${origin}/api/companion/events` },
    { url: 'http://127.0.0.1:43124/api/companion/presence' },
    { url: 'http://u:p@127.0.0.1:43123/api/companion/presence' },
    { contentType: 'text/plain' }, { redirected: true }, { worker: true }, { mainFrame: false },
    { sourcePage: {} }, { sourceFrame: {} }, { frameURL: `${origin}/?desktopSurface=haru` },
    { frameURL: `${origin}/other` }, { body: 'invalid' }, { body: ' '.repeat(2049) },
  ]) {
    const { input, context } = fixture();
    const guard = createPresenceSuppression(); guard.verifyContext(context);
    assert.throws(() => guard.recordAborted({ ...input, ...change }), JSON.stringify(change));
    assert.throws(() => guard.assertSettled());
  }
});
test('body is bounded, exact-schema and tied to the real disabled backend, never inferred from defaults', () => {
  for (const mutate of [
    body => { body.extra = false; }, body => { delete body.failure; }, body => { body.visible = 1; },
    body => { body.identity = client; }, body => { body.client_id = 'not-a-uuid'; },
    body => { body.sequence = -1; }, body => { body.sequence = Number.MAX_SAFE_INTEGER + 1; },
    body => { body.sequence = 1.1; }, body => { body.work_running = true; },
    body => { body.awaiting_approval = true; }, body => { body.mock_interview = true; }, body => { body.quiet = true; },
  ]) {
    const { input, context, body } = fixture(); mutate(body);
    const guard = createPresenceSuppression(); guard.verifyContext(context);
    assert.throws(() => guard.recordAborted({ ...input, body: JSON.stringify(body) }));
  }
  for (const change of [{ snapshot: null }, { snapshot: { identity } }, { snapshot: { identity, enabled: true } },
    { ownerRole: 'haru' }, { ownerURL: `${origin}/?desktopSurface=haru` }, { ownerURL: `${origin}/#x` }]) {
    const { input, context } = fixture(); const guard = createPresenceSuppression(); guard.recordAborted(input);
    assert.throws(() => guard.verifyContext({ ...context, ...change }));
    assert.throws(() => guard.assertSettled());
  }
});
test('one client per launch advances sequence; normal visibility/typing and abort-induced failure remain bounded', () => {
  const { input, context, body } = fixture(); const guard = createPresenceSuppression(); guard.verifyContext(context);
  guard.recordAborted(input);
  guard.recordAborted({ ...input, body: JSON.stringify({ ...body, sequence: 2, visible: false, typing: true, failure: true }) });
  guard.assertSettled(); assert.equal(guard.summary().validated, 2);
  assert.throws(() => guard.recordAborted({ ...input, body: JSON.stringify({ ...body, sequence: 2 }) }));
  const other = createPresenceSuppression(); other.verifyContext(context); other.recordAborted(input);
  assert.throws(() => other.recordAborted({ ...input, body: JSON.stringify({ ...body, sequence: 3, client_id: identity }) }));
  const changed = createPresenceSuppression(); changed.verifyContext(context); changed.recordAborted(input);
  assert.throws(() => changed.verifyContext({ ...context, snapshot: { identity: client, enabled: false } }));
});
test('queued invalid ownership cannot be erased or converted to accepted suppression', () => {
  const { input, context } = fixture(); const guard = createPresenceSuppression();
  guard.recordAborted({ ...input, sourcePage: {} });
  assert.throws(() => guard.verifyContext(context));
  assert.throws(() => guard.assertSettled());
  assert.equal(guard.summary().rejected, true);
});

test('verified owner Settings navigation keeps exact provenance and sequence without allowing other URLs', () => {
  const { input, context, body } = fixture(); const guard = createPresenceSuppression();
  guard.verifyContext(context); guard.recordAborted(input);
  const ownerURL = `${origin}/?view=settings`;
  guard.recordAborted({ ...input, frameURL: ownerURL, body: JSON.stringify({ ...body, sequence: 2 }) });
  guard.verifyContext({ ...context, ownerURL });
  guard.recordAborted({ ...input, body: JSON.stringify({ ...body, sequence: 3 }) });
  guard.verifyContext(context); guard.assertSettled();
  assert.equal(guard.summary().validated, 3);
  for (const suffix of ['/?view=pilot', '/?view=settings&x=1', '/?view=settings&view=settings',
    '/?view=settings#x', '/?view=%73ettings', '/?View=settings', '/?view=settings&',
    '/?desktopSurface=haru', '/settings', '/./?view=settings', '/?view=settings/']) {
    for (const target of ['request', 'context']) {
      const rejected = createPresenceSuppression(); rejected.verifyContext(context);
      assert.throws(() => target === 'request'
        ? rejected.recordAborted({ ...input, frameURL: `${origin}${suffix}` })
        : rejected.verifyContext({ ...context, ownerURL: `${origin}${suffix}` }), `${target}: ${suffix}`);
      assert.equal(rejected.summary().firstRejection, 'owner-document');
    }
  }
});

test('first rejection is a fixed safe code and survives sticky failures without leaking values', () => {
  const { input, context, body } = fixture(); const guard = createPresenceSuppression(); guard.verifyContext(context);
  assert.throws(() => guard.recordAborted({ ...input, frameURL: `${origin}/?secret=private` }));
  assert.throws(() => guard.recordAborted(input));
  assert.throws(() => guard.verifyContext(context));
  assert.equal(guard.summary().firstRejection, 'owner-document');
  assert.doesNotMatch(JSON.stringify(guard.summary()), /secret|private|11111111|22222222|127\.0\.0\.1/);
  const flags = createPresenceSuppression(); flags.verifyContext(context);
  assert.throws(() => flags.recordAborted({ ...input, body: JSON.stringify({ ...body, quiet: true }) }));
  assert.equal(flags.summary().firstRejection, 'inactive-signals');
});

test('route queue registers before delayed port reads and drains requests added during shutdown', async () => {
  const queue = createSerialRequestQueue(); const order = [];
  let release;
  const port = new Promise(resolve => { release = resolve; });
  const first = queue.schedule(async () => { await port; order.push(1); });
  let done = false; const drain = queue.drain().then(() => { done = true; });
  const second = queue.schedule(async () => {
    order.push(2);
    queue.schedule(async () => { order.push(3); });
  });
  await Promise.resolve(); assert.equal(done, false);
  release(); await Promise.all([first, second, drain]);
  assert.deepEqual(order, [1, 2, 3]); assert.equal(done, true);
});
test('failed abort stays failed while the queue continues to block later requests', async () => {
  const queue = createSerialRequestQueue(); let continued = false;
  const first = queue.schedule(async () => { throw new Error('abort-unconfirmed'); });
  const second = queue.schedule(async () => { continued = true; });
  await assert.rejects(first, /abort-unconfirmed/); await second;
  await assert.rejects(queue.drain(), /abort-unconfirmed/); assert.equal(continued, true);
});
test('frame provenance survives detach during abort and only confirmed abort can be recorded', async () => {
  const { input, context } = fixture(); let detached = false;
  const read = value => { if (detached) throw new Error('detached'); return value; };
  const frame = { parentFrame: () => read(null), page: () => read(context.ownerPage), url: () => read(`${origin}/`) };
  context.ownerMainFrame = frame;
  const request = { frame: () => read(frame), url: () => input.url, method: () => input.method,
    headers: () => ({ 'content-type': input.contentType }), postData: () => input.body,
    redirectedFrom: () => null, serviceWorker: () => null };
  const captured = capturePresenceRequest(request);
  const guard = createPresenceSuppression(); guard.verifyContext(context);
  const abort = async () => { detached = true; };
  await abort(); guard.recordAborted({ ...captured, origin, aborted: true }); guard.assertSettled();
  assert.throws(() => capturePresenceRequest(request), /detached/);
});
