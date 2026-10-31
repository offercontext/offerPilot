// Public index -> real UI installation service -> Git package -> MCP reads.
// Uses an existing authorized connection; creates no grants or business data.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: Object.fromEntries(
  ['dsh-cli', 'mcp-config', 'output', 'index', 'commit'].map(key => [key, { type: 'string' }]),
) });
for (const key of ['dsh-cli', 'mcp-config', 'output', 'index', 'commit']) {
  assert.ok(values[key], `--${key} is required`);
}
assert.match(values.commit, /^[0-9a-f]{40}$/);
const root = resolve(values.output);
await mkdir(root); // Never overwrite historical failed or successful evidence.
const profile = join(root, 'home', 'profiles', 'web');
await mkdir(profile, { recursive: true });
// The manager scrubs npm_config_* environment variables. Persist test-local
// pnpm settings so neither its Git download cache nor its store can hide a failure.
await writeFile(join(profile, 'pnpm-workspace.yaml'),
  `packages:\n  - .\nnodeLinker: hoisted\nautoInstallPeers: false\n` +
  `storeDir: ${JSON.stringify(join(root, 'pnpm-store'))}\n` +
  `cacheDir: ${JSON.stringify(join(root, 'pnpm-cache'))}\n`);
const report = join(root, 'report.json');
const patch = join(root, 'market.patch.json');
await writeFile(patch, JSON.stringify([{ insert: [
  { id: 'plugin-market', name: '@deepseek-ai/dsh-plugin-market', config: { indexes: [values.index] } },
  { id: 'offerpilot-market-install-probe',
    name: join(dirname(fileURLToPath(import.meta.url)), 'dsh-market-install-probe.mjs'),
    config: { index: values.index, commit: values.commit, report } },
] }]));
const child = spawn(process.execPath, [resolve(values['dsh-cli']), '--profile', 'web',
  '--patch', patch, '--no-open', '--port', '0'], {
  env: { ...process.env, DSH_HOME: join(root, 'home'), PYTHONUTF8: '1',
    OFFERPILOT_MCP_CONFIG: resolve(values['mcp-config']) },
  windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
});
const output = [];
child.stdout.on('data', data => output.push(data));
child.stderr.on('data', data => output.push(data));
let timedOut = false;
const timer = setTimeout(() => {
  timedOut = true;
  if (process.platform === 'win32' && child.pid) {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
  } else child.kill();
}, 300000);
let code;
try {
  code = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', resolve);
  });
} finally {
  clearTimeout(timer);
  await writeFile(join(root, 'runtime.log'), Buffer.concat(output).toString('utf8')
    .replace(/([?&]token=)[A-Za-z0-9_-]+/g, '$1[redacted]'));
}
assert.equal(timedOut, false, `timed out; evidence: ${root}`);
assert.equal(code, 0, `installation or activation failed; evidence: ${root}`);
const result = JSON.parse(await readFile(report, 'utf8'));
assert.equal(result.passed, true);
assert.equal(result.disposed, true);
const manifest = JSON.parse(await readFile(join(root, 'home/profiles/web/package.json'), 'utf8'));
assert.ok(manifest.dependencies['dsh-offerpilot'].includes(values.commit));
assert.ok(manifest.dependencies['dsh-offerpilot'].endsWith('&path:/plugins/dsh-offerpilot'));
assert.ok(manifest.dsh.profile.bundles.includes('dsh-offerpilot'));
console.log(`Market Git installation, activation and MCP reads passed: ${report}`);
