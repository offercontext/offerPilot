// Install a real tarball in a new DSH home and run the full profile twice.
// All business data is fictional; this does not approve any proposal.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { createHash } from 'node:crypto';

const { values } = parseArgs({ options: {
  package: { type: 'string' }, 'dsh-cli': { type: 'string' },
  python: { type: 'string' }, output: { type: 'string' },
} });
for (const key of ['package', 'dsh-cli', 'python', 'output']) {
  assert.ok(values[key], `--${key} is required`);
}
const root = resolve(values.output);
await mkdir(root); // Refuse reuse; retain unsuccessful runs for diagnosis.
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const env = { ...process.env, DSH_HOME: join(root, 'dsh-home'), PYTHONUTF8: '1',
  OFFERPILOT_MCP_CONFIG: join(root, 'data', 'mcp.json') };

async function run(label, command, args, { expected = 0, timeout = 120000 } = {}) {
  const output = [];
  const child = spawn(command, args, { cwd: repo, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => output.push(data));
  child.stderr.on('data', data => output.push(data));
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    // Windows' Python launcher/DSH may own descendants holding the pipes open.
    // Terminate only the process tree started by this test on timeout.
    if (process.platform === 'win32' && child.pid) {
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
    } else child.kill();
  }, timeout);
  let code;
  try {
    code = await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', resolve);
    });
  } finally {
    clearTimeout(timer);
    const log = Buffer.concat(output).toString('utf8').replace(/([?&]token=)[A-Za-z0-9_-]+/g, '$1[redacted]');
    await writeFile(join(root, `${label}.log`), log);
  }
  assert.equal(timedOut, false, `${label} timed out; evidence retained at ${root}`);
  if (expected === 'nonzero') assert.notEqual(code, 0, `${label} unexpectedly succeeded`);
  else assert.equal(code, expected, `${label} failed; see ${join(root, `${label}.log`)}`);
  console.log(`${label}: exit ${code}`);
}

const cli = resolve(values['dsh-cli']);
const tarball = resolve(values.package);
await run('version', process.execPath, [cli, '--version']);
assert.equal((await readFile(join(root, 'version.log'), 'utf8')).trim(), '0.2.1-rc.5');
await run('prepare', resolve(values.python), [join(repo, 'scripts', 'external-agent-acceptance.py'),
  'prepare', '--data-dir', join(root, 'data')]);
await run('seed-out-of-scope', resolve(values.python), ['-c', `
import json, sys
from pathlib import Path
from offerpilot.external_agents.server import open_sessions
from offerpilot.repositories.applications import ApplicationCreate, ApplicationsRepository
root = Path(sys.argv[1])
fixture = json.loads((root / 'fixture.json').read_text(encoding='utf-8'))
sessions = open_sessions(root)
try:
    other = ApplicationsRepository(sessions).create(ApplicationCreate('Outside grant fixture', 'Engineer'))
    fixture['out_of_scope_id'] = other.id
    assert other.id != fixture['application_id']
finally:
    sessions.kw['bind'].dispose()
(root / 'fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
print(json.dumps({'out_of_scope_id': other.id}))
`, join(root, 'data')]);
await run('install', process.execPath, [cli, 'plugin', '--profile', 'acceptance', 'add', tarball]);
const manifest = JSON.parse(await readFile(join(env.DSH_HOME, 'profiles', 'acceptance', 'package.json'), 'utf8'));
assert.ok(manifest.dsh.profile.bundles.includes('dsh-offerpilot'));
await run('dump', process.execPath, [cli, '--profile', 'acceptance', '--dump-config']);
assert.match(await readFile(join(root, 'dump.log'), 'utf8'), /name: dsh-offerpilot/);
async function boot(phase, options = {}) {
  const report = join(root, `${phase}.json`);
  const patch = join(root, `${phase}.patch.json`);
  await writeFile(patch, JSON.stringify([{ insert: [{ id: 'offerpilot-probe',
    name: join(repo, 'scripts', 'dsh-plugin-probe.mjs'),
    config: { fixture: join(root, 'data', 'fixture.json'), report, ...options },
  }] }]));
  await run(phase, process.execPath, [cli, '--profile', options.web ? 'acceptance-web' : 'acceptance',
    '--patch', patch, ...(options.web ? ['--no-open', '--port', '0'] : [])], { timeout: 60000 });
  const result = JSON.parse(await readFile(report, 'utf8'));
  assert.equal(result.passed, true);
  assert.equal(result.disposed, true);
}
await boot('first-boot');
await boot('restart');
// A revoked existing connection must not silently publish stale tools or widen
// its scope. Revocation is performed by the operator CLI on fictional data.
const fixture = JSON.parse(await readFile(join(root, 'data', 'fixture.json'), 'utf8'));
await run('revoke', resolve(values.python), ['-c', 'from offerpilot.cli import main; main()', 'mcp', 'revoke',
  fixture.connection_id, '--data-dir', join(root, 'data')]);
assert.match(await readFile(join(root, 'revoke.log'), 'utf8'), /连接已撤销/);
// DSH keeps its host alive when an optional plugin fails. Inspect the failed
// fiber and absent tools, not the process exit code or a fixed sleep alone.
await boot('revoked-boot', { revoked: true });
assert.match(await readFile(join(root, 'revoked-boot.log'), 'utf8'), /connection_unavailable/);
await run('readonly-connect', resolve(values.python), ['-c', 'from offerpilot.cli import main; main()',
  'mcp', 'connect', 'DSH read-only acceptance', '--application-id', String(fixture.application_id),
  '--data-dir', join(root, 'data')]);
const readonly = JSON.parse(await readFile(join(root, 'readonly-connect.log'), 'utf8'));
const readonlyConfig = join(root, 'readonly.json');
await run('readonly-export', resolve(values.python), ['-c', 'from offerpilot.cli import main; main()',
  'mcp', 'config', readonly.connection_id, '--format', 'mcp', '--data-dir', join(root, 'data'),
  '--output', readonlyConfig]);
env.OFFERPILOT_MCP_CONFIG = readonlyConfig;
await boot('readonly-boot', { readOnly: true });
await run('web-profile', process.execPath, [cli, '--profile', 'acceptance-web', '--from-default-profile', 'web', '--dump-config']);
await run('web-install', process.execPath, [cli, 'plugin', '--profile', 'acceptance-web', 'add', tarball]);
await boot('web-boot', { readOnly: true, web: true });
await run('uninstall', process.execPath, [cli, 'plugin', '--profile', 'acceptance', 'remove', 'dsh-offerpilot']);
await run('uninstalled-dump', process.execPath, [cli, '--profile', 'acceptance', '--dump-config']);
assert.doesNotMatch(await readFile(join(root, 'uninstalled-dump.log'), 'utf8'), /name: dsh-offerpilot/);
const summary = { passed: true, dsh: '0.2.1-rc.5', package: tarball,
  sha256: createHash('sha256').update(await readFile(tarball)).digest('hex'),
  checks: ['install', 'profile composition', 'tool discovery', 'read', 'pending only',
    'idempotent retry', 'cancel', 'dispose', 'restart', 'revoked plugin fails closed',
    'default read-only tools', 'existing out-of-scope record rejected', 'web profile HTTP and tools', 'uninstall'],
  real_model_calls: 0, user_approval_exercised: false };
await writeFile(join(root, 'summary.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
