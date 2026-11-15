import assert from 'node:assert/strict';
import test from 'node:test';
import { waitForMailState } from './job-mail-browser-wait.mjs';

test('awaits false, false, true and returns the third fresh state', async () => {
  let reads = 0;
  const state = await waitForMailState(async () => ({ attempt: ++reads }), async value => value.attempt >= 3, { timeoutMs: 1000, pollMs: 1 });
  assert.equal(reads, 3);
  assert.deepEqual(state, { attempt: 3 });
});
test('propagates a terminal failure immediately without retrying', async () => {
  let reads = 0;
  await assert.rejects(waitForMailState(async () => { reads++; return { status: 'failed' }; }, value => {
    if (value.status === 'failed') throw new Error('Sync failed');
    return false;
  }), /Sync failed/);
  assert.equal(reads, 1);
});
test('bounds a hanging read by the total deadline', async () => {
  await assert.rejects(waitForMailState(() => new Promise(() => {}), () => true, { timeoutMs: 20, pollMs: 1 }), /deadline exceeded/);
});
test('bounds repeated false results and propagates read failures', async () => {
  await assert.rejects(waitForMailState(async () => false, async value => value, { timeoutMs: 20, pollMs: 1 }), /deadline exceeded/);
  await assert.rejects(waitForMailState(async () => { throw new Error('Read failed'); }, () => true), /Read failed/);
});
