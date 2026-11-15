import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

// Execute the actual cleanup function with synthetic OS/file observations only.
// No product process, profile, or external transport is created by these tests.
const source = fs.readFileSync(new URL('../cleanup.mjs', import.meta.url), 'utf8');
const functionSource = source.slice(source.indexOf('export async function'), source.indexOf('\nif (process.argv')).replace('export ', '');
function fixture({ remaining = poll => poll === 1, failCommand = false, postSnapshotCostMs = 0, snapshotTimesOut = false } = {}) {
  const env = { RUNNER_TEMP: '/synthetic-temp', APPDATA: '/synthetic-appdata', RUNNER_ENVIRONMENT: 'github-hosted' };
  const root = path.join(env.RUNNER_TEMP, 'offerpilot-bounded-ai-mock');
  const installDir = path.join(root, '安装 Application');
  const profile = path.join(env.APPDATA, 'OfferPilot Desktop');
  const prepared = { mode: 'mock', root, profile, installDir,
    exe: path.join(installDir, 'OfferPilot Desktop.exe'),
    backend: path.join(installDir, 'resources/backend/offerpilot-backend.exe'), profileOwned: true };
  const identity = { pid: 77, parentPid: 76, path: prepared.exe, created: 'synthetic-original' };
  const calls = [], removed = [];
  let snapshots = 0, now = 1000;
  const context = vm.createContext({ path, process: { env }, performance: { now: () => now },
    setTimeout: (resolve, ms) => { now += ms; queueMicrotask(resolve); },
    childEnvironment: value => value,
    demand: (condition, safeCode) => { if (!condition) throw Object.assign(new Error(safeCode), { safeCode }); },
    exists: async filename => !removed.includes(filename),
    fs: { readFile: async () => JSON.stringify(prepared), rm: async filename => { removed.push(filename); } },
    quietCommand: async (command, args, commandEnv, timeout) => {
      assert.equal(command, 'pwsh.exe');
      const mode = args.at(-1); calls.push({ mode, commandEnv, timeout });
      assert.equal(commandEnv.UI_EXE, prepared.exe); assert.equal(commandEnv.UI_BACKEND, prepared.backend);
      if (mode === 'cleanup') {
        assert.deepEqual(JSON.parse(commandEnv.UI_OWNED_IDENTITIES), [identity]);
        if (failCommand) throw Object.assign(new Error('private helper error'), { safeCode: 'HELPER_COMMAND_FAILED' });
        return JSON.stringify({ cleanupStopped: 1 });
      }
      assert.equal(mode, 'snapshot');
      snapshots += 1;
      if (snapshots > 1) {
        now += snapshotTimesOut ? timeout : postSnapshotCostMs;
        if (snapshotTimesOut) throw Object.assign(new Error('private helper timeout'), { safeCode: 'HELPER_COMMAND_FAILED' });
      }
      const observed = snapshots === 1 ? identity : remaining(snapshots - 1);
      return JSON.stringify({ processes: observed ? [observed === true ? identity : observed] : [], listeners: [] });
    },
  });
  const cleanup = vm.runInContext(`${functionSource}\ncleanOwnedFiles`, context);
  return { run: () => cleanup(env, 'mock'), calls, removed, root, profile, elapsed: () => now - 1000 };
}

test('owned process may finish exiting after the first post-stop snapshot', async () => {
  const f = fixture();
  const result = await f.run();
  assert.equal(result.status, 'passed');
  assert.deepEqual(f.calls.map(row => row.mode), ['snapshot', 'cleanup', 'snapshot', 'snapshot']);
  assert.deepEqual(f.removed, [f.profile, f.root]);
  assert.ok(f.elapsed() > 0 && f.elapsed() <= 15000);
  assert.ok(f.calls.slice(2).every(row => row.timeout > 0 && row.timeout <= 15000));
});

test('permanent survivors exhaust a bounded wait and preserve profile and scratch', async () => {
  const f = fixture({ remaining: () => true });
  await assert.rejects(f.run(), { safeCode: 'OWNED_PROCESS_CLEANUP_FAILED' });
  assert.equal(f.calls.filter(row => row.mode === 'cleanup').length, 1);
  assert.deepEqual(f.removed, []);
  assert.ok(f.elapsed() >= 15000 && f.elapsed() <= 15200);
});

test('a new or reused identity is never added to the kill authority while polling', async () => {
  const f = fixture({ remaining: () => ({ pid: 77, parentPid: 76, path: '/synthetic-temp/replacement.exe', created: 'synthetic-reused' }) });
  await assert.rejects(f.run(), { safeCode: 'OWNED_PROCESS_CLEANUP_FAILED' });
  assert.equal(f.calls.filter(row => row.mode === 'cleanup').length, 1);
  assert.deepEqual(f.removed, []);
});

test('failed OS stop cannot be followed by profile deletion', async () => {
  const f = fixture({ failCommand: true });
  await assert.rejects(f.run(), { safeCode: 'HELPER_COMMAND_FAILED' });
  assert.deepEqual(f.calls.map(row => row.mode), ['snapshot', 'cleanup']);
  assert.deepEqual(f.removed, []);
});

test('exit snapshots share one monotonic budget including command execution time', async () => {
  const f = fixture({ postSnapshotCostMs: 2000 });
  assert.equal((await f.run()).status, 'passed');
  assert.deepEqual(f.calls.slice(2).map(row => row.timeout), [15000, 12800]);
  assert.equal(f.elapsed(), 4200);
});

test('exit snapshot timeout is unproven owned-process cleanup and never deletes files', async () => {
  const f = fixture({ snapshotTimesOut: true });
  await assert.rejects(f.run(), { safeCode: 'OWNED_PROCESS_CLEANUP_FAILED' });
  assert.deepEqual(f.calls.map(row => row.mode), ['snapshot', 'cleanup', 'snapshot']);
  assert.equal(f.elapsed(), 15000); assert.deepEqual(f.removed, []);
});
