import test from 'node:test';
import assert from 'node:assert/strict';
import { closeTaskThroughUi, leaveTask } from '../ui-scenarios.mjs';
import { UI_STAGES } from '../ui-diagnostics.mjs';

// Fake locator lifecycle tests complement test-ui's real React Host/controller
// tests. Neither establishes that the Windows EXE passed.
function fixture({ generation = '7', replacement = false, stall = false, exitVisible = false, replaceBeforeClick = false, disposeFails = false } = {}) {
  const events = [];
  let child = true, mounted = true, clicks = 0, release;
  const detached = new Promise(resolve => { release = resolve; });
  const ownerId = 'application-interview-prepare';
  const owner = { count: async () => Number(mounted), getAttribute: async name => name === 'data-core-task-owner' ? ownerId : generation };
  const all = { count: async () => Number(mounted || replacement) };
  const ownerButtons = {};
  const pinned = { count: async () => Number(mounted), getByRole(role) { assert.equal(role, 'button'); return ownerButtons; } };
  const exit = { isVisible: async () => exitVisible };
  const physicalControl = { async dispose() { if (disposeFails) throw new Error('private disposal error'); events.push('disposed'); } };
  const pinnedControl = { count: async () => Number(mounted), async elementHandle(options) { assert.equal(options.timeout, 100); return physicalControl; } };
  const control = { and(scope) { assert.equal(scope, ownerButtons); return pinnedControl; } };
  const surface = { locator(selector) {
    assert.equal(selector, 'xpath=ancestor::*[@data-core-task-owner][1]'); return owner;
  } };
  const page = {
    locator(selector) {
      if (selector === '[data-core-task-owner]') return all;
      assert.equal(selector, `[data-core-task-owner="${ownerId}"][data-core-task-generation="${generation}"]`);
      return pinned;
    },
    getByRole(role, options) {
      assert.equal(role, 'button'); assert.equal(options.name, '退出沉浸模式，返回原页面');
      return exit;
    },
  };
  const ctx = {
    mark(stage) { assert.ok(UI_STAGES.includes(stage)); events.push(stage); },
    timeout() { return 100; },
    async click(target, diagnostic) {
      if (target === exit) { clicks += 1; events.push('exit-click'); return; }
      assert.equal(target, physicalControl); assert.equal(diagnostic, pinnedControl);
      if (replaceBeforeClick) {
        mounted = false;
        throw Object.assign(new Error('captured control detached during actionability wait'), { name: 'TimeoutError' });
      }
      clicks += 1; child = false; events.push('close-click');
    },
    async hidden(target) {
      if (target === exit) { events.push('exit-hidden'); return; }
      assert.equal(target, surface); assert.equal(child, false); events.push('child-hidden');
    },
    async detached(target) {
      assert.ok(target === pinned || target === all); events.push('wait-owner-detached');
      if (stall) throw Object.assign(new Error('fixed test timeout'), { name: 'TimeoutError' });
      if (mounted) await detached;
      events.push('owner-detached');
    },
  };
  return { page, surface, control, ctx, events, clicks: () => clicks,
    unmount() { mounted = false; release(); } };
}

test('child hidden is insufficient: same owner must detach after the one product close click', async () => {
  const f = fixture(); let finished = false;
  const pending = closeTaskThroughUi(f.page, f.surface, f.control, f.ctx, 'application-interview-prepare').then(() => { finished = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(f.events.includes('child-hidden')); assert.equal(finished, false); assert.equal(f.clicks(), 1);
  f.unmount(); await pending;
  assert.equal(finished, true); assert.equal(f.clicks(), 1);
  assert.deepEqual(f.events.slice(-2), ['owner-detached', 'disposed']);
});

test('a stalled natural close fails without another click, forced dismissal or DOM mutation', async () => {
  const f = fixture({ stall: true });
  await assert.rejects(closeTaskThroughUi(f.page, f.surface, f.control, f.ctx, 'application-interview-prepare'), { name: 'TimeoutError' });
  assert.equal(f.clicks(), 1);
});

test('replacement task after captured owner detach is rejected rather than automatically closed', async () => {
  const f = fixture({ replacement: true });
  const pending = closeTaskThroughUi(f.page, f.surface, f.control, f.ctx, 'application-interview-prepare');
  await new Promise(resolve => setImmediate(resolve)); f.unmount();
  await assert.rejects(pending, { code: 'UI_ASSERTION_FAILED' }); assert.equal(f.clicks(), 1);
});

test('replacement during click auto-wait cannot receive the old generation-bound action', async () => {
  const f = fixture({ replaceBeforeClick: true });
  await assert.rejects(closeTaskThroughUi(f.page, f.surface, f.control, f.ctx, 'application-interview-prepare'), { name: 'TimeoutError' });
  assert.equal(f.clicks(), 0);
});

test('physical node replacement with reused owner/generation fails without another close or masking by disposal', async () => {
  const f = fixture({ replaceBeforeClick: true, disposeFails: true });
  await assert.rejects(closeTaskThroughUi(f.page, f.surface, f.control, f.ctx, 'application-interview-prepare'), { name: 'TimeoutError' });
  assert.equal(f.clicks(), 0);
});

test('untrusted or absent owner generation fails before any close action', async () => {
  for (const generation of [null, '', '0', '-1', '7] [data-private]', '9007199254740992']) {
    const f = fixture({ generation });
    await assert.rejects(closeTaskThroughUi(f.page, f.surface, f.control, f.ctx, 'application-interview-prepare'), { code: 'UI_ASSERTION_FAILED' });
    assert.equal(f.clicks(), 0);
  }
});

test('unknown owner or mismatched known owner cannot enter the close path', async () => {
  for (const expected of [undefined, 'application-material-kit', 'application-offer-review']) {
    const f = fixture();
    await assert.rejects(closeTaskThroughUi(f.page, f.surface, f.control, f.ctx, expected), { code: 'UI_ASSERTION_FAILED' });
    assert.equal(f.clicks(), 0);
  }
});

test('navigation refuses to click a remaining unresolved task', async () => {
  const f = fixture({ stall: true });
  await assert.rejects(leaveTask(f.page, f.ctx), { name: 'TimeoutError' });
  assert.equal(f.clicks(), 0); assert.equal(f.events[0], 'LEAVE_TASK');
});

test('navigation waits for natural closing completion then exits Pilot through its actual button once', async () => {
  const f = fixture({ exitVisible: true });
  const pending = leaveTask(f.page, f.ctx);
  await new Promise(resolve => setImmediate(resolve)); assert.equal(f.clicks(), 0);
  f.unmount(); await pending;
  assert.equal(f.clicks(), 1);
  assert.deepEqual(f.events.slice(-3), ['PILOT_EXIT', 'exit-click', 'exit-hidden']);
});
