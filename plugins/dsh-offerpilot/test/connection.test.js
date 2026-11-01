import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { loadConnection, validateConnection } from '../connection.js';

function fixture() {
  return { mcpServers: { offerpilot: {
    command: resolve('Python with spaces/python.exe'),
    args: ['-m', 'offerpilot.external_agents.server', '--data-dir', resolve('资料 with spaces'),
      '--connection-id', '12345678-1234-1234-1234-123456789abc'],
  } } };
}

test('preserves the scoped server arguments, including spaces and Unicode, without shell quoting', () => {
  const input = fixture();
  const result = validateConnection(input);
  assert.deepEqual(result.args, input.mcpServers.offerpilot.args);
  assert.equal(result.serverName, 'offerpilot');
  assert.equal(result.failOnStartupError, true);
  result.args.push('modified');
  assert.equal(input.mcpServers.offerpilot.args.length, 6);
});

for (const [name, change] of [
  ['approval command', x => { x.args = ['-m', 'offerpilot.cli', 'mcp', 'review', 'id']; }],
  ['different module', x => { x.args[1] = 'another.module'; }],
  ['extra authority argument', x => { x.args.push('--confirm'); }],
  ['relative data directory', x => { x.args[3] = './data'; }],
  ['relative interpreter', x => { x.command = 'python'; }],
  ['invalid connection', x => { x.args[5] = 'not-a-uuid'; }],
  ['environment injection', x => { x.env = { PYTHONPATH: 'untrusted' }; }],
]) {
  test(`rejects ${name}`, () => {
    const input = fixture(); change(input.mcpServers.offerpilot);
    assert.throws(() => validateConnection(input), /unmodified file/);
  });
}

test('does not pick a different server or silently accept additional servers', () => {
  const input = fixture(); input.mcpServers.other = input.mcpServers.offerpilot;
  assert.throws(() => validateConnection(input));
  assert.throws(() => validateConnection(null));
});

test('reads exported config; missing, invalid, relative and oversized files fail closed', async () => {
  const root = await mkdtemp(join(tmpdir(), 'offerpilot-dsh-test-'));
  try {
    const path = join(root, '连接.json');
    await writeFile(path, JSON.stringify(fixture()));
    assert.equal((await loadConnection(path)).transport, 'stdio');
    await assert.rejects(loadConnection(join(root, 'missing.json')));
    await assert.rejects(loadConnection('relative.json'), /absolute path/);
    await assert.rejects(loadConnection(undefined), /absolute path/);
    await writeFile(path, '{bad');
    await assert.rejects(loadConnection(path), /unmodified file/);
    await writeFile(path, ' '.repeat(65537));
    await assert.rejects(loadConnection(path), /unmodified file/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
