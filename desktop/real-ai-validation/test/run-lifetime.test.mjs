import test from 'node:test';
import assert from 'node:assert/strict';
import { createRunLifetime } from '../run-lifetime.mjs';
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test('deadline during restart waiting prevents a second launch before cleanup', async () => {
  const waiting = deferred(), expired = deferred();
  const events = [];
  const lifetime = createRunLifetime(Date.now() + 20);
  const work = lifetime.run(async () => {
    events.push('restart-wait'); await waiting.promise;
    lifetime.assertActive(); events.push('launch');
  }, () => { events.push('timeout'); expired.resolve(); });
  await expired.promise;
  waiting.resolve();
  await assert.rejects(work, { safeCode: 'SESSION_DEADLINE' });
  events.push('owned-cleanup');
  assert.deepEqual(events, ['restart-wait', 'timeout', 'owned-cleanup']);
  assert.throws(lifetime.assertActive, { safeCode: 'SESSION_DEADLINE' });
});

test('deadline during an in-flight launch drains its result before owned cleanup and cannot resume later', async () => {
  const launch = deferred(), expired = deferred();
  const events = [];
  let app, cleanupFinished = false;
  const lifetime = createRunLifetime(Date.now() + 20);
  const work = lifetime.run(async () => {
    lifetime.assertActive(); events.push('launch-started');
    app = await launch.promise; events.push('launch-returned');
    assert.equal(cleanupFinished, false);
    lifetime.assertActive(); events.push('work-resumed');
  }, () => { events.push('timeout'); expired.resolve(); });
  let settled = false;
  const observed = assert.rejects(work, { safeCode: 'SESSION_DEADLINE' }).then(() => { settled = true; });
  await expired.promise;
  await Promise.resolve();
  assert.equal(settled, false, 'the owner cannot enter cleanup while launch is still running');
  launch.resolve({ pid: 42 });
  await observed;
  assert.deepEqual(app, { pid: 42 });
  events.push('owned-cleanup'); cleanupFinished = true;
  await Promise.resolve();
  assert.deepEqual(events, ['launch-started', 'timeout', 'launch-returned', 'owned-cleanup']);
});

test('completed work preserves result, stops late starts and never invokes timeout cleanup', async () => {
  const lifetime = createRunLifetime(Date.now() + 1000);
  let expired = false;
  assert.equal(await lifetime.run(async () => 7, () => { expired = true; }), 7);
  assert.equal(expired, false);
  assert.throws(lifetime.assertActive, { safeCode: 'SESSION_DEADLINE' });
  await assert.rejects(lifetime.run(async () => 8), { safeCode: 'INVALID_HARNESS' });
});

test('recovery monotonic deadline rejects late microtask success even after wall clock rollback', async t => {
  const start = performance.now();
  const wall = Date.now();
  const lifetime = createRunLifetime(wall + 20, { monotonicDeadline: start + 20 });
  await assert.rejects(lifetime.run(async () => {
    t.mock.method(Date, 'now', () => wall - 60000);
    while (performance.now() - start < 35) { /* Deliberately block timer dispatch. */ }
    return 'must not pass';
  }), { safeCode: 'SESSION_DEADLINE' });
  assert.throws(lifetime.assertActive, { safeCode: 'SESSION_DEADLINE' });
});

test('recovery already-expired monotonic budget refuses any work despite future wall clock deadline', async () => {
  const lifetime = createRunLifetime(Date.now() + 600000, { monotonicDeadline: performance.now() - 1 });
  let ran = false;
  await assert.rejects(lifetime.run(async () => { ran = true; }), { safeCode: 'SESSION_DEADLINE' });
  assert.equal(ran, false);
});
