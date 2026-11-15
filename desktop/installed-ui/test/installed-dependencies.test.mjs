import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import syncFs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createPackageWithOptions, createPackageFromStreams } from '@electron/asar';
import { Readable } from 'node:stream';
import { auditInstalledDependencies, readPinnedDependencySource, nodeModuleCandidates } from '../installed-dependencies.mjs';
import { unlinkedFile, assertDirectoryIdentity, safeRelativePath } from '../filesystem-safety.mjs';
const { Filesystem } = await import(new URL('./filesystem.js', import.meta.resolve('@electron/asar')));

const UPDATER = 'node_modules/electron-updater';
const RUNTIME = 'node_modules/builder-util-runtime';
const git = (root, args) => execFileSync('git', ['-C', root, ...args], {
  encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^GIT_/i.test(key))),
}).trim();
const integrity = 'sha512-' + Buffer.from('synthetic integrity').toString('base64');
async function write(root, name, data) {
  await fs.mkdir(path.dirname(path.join(root, name)), { recursive: true });
  await fs.writeFile(path.join(root, name), typeof data === 'object' ? JSON.stringify(data) : data);
}
async function fixture(t, { updater = '6.8.9', runtime = '9.7.0', axios = '1.20.0', change, asarOptions } = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot 依赖 evidence '));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const source = path.join(root, 'PIN source'), app = path.join(root, 'app input');
  const installDir = path.join(root, '安装 Application'), payload = path.join(root, 'payload');
  await write(source, 'desktop/package.json', { dependencies: { 'electron-updater': updater } });
  await write(source, 'desktop/package-lock.json', { lockfileVersion: 3, packages: {
    '': { dependencies: { 'electron-updater': updater } },
    [UPDATER]: { version: updater, integrity, dependencies: { 'builder-util-runtime': runtime } },
    [RUNTIME]: { version: runtime, integrity },
  } });
  await write(source, 'web/package.json', { dependencies: { axios: '^' + axios } });
  await write(source, 'web/package-lock.json', { lockfileVersion: 3, packages: {
    '': { dependencies: { axios: '^' + axios } }, 'node_modules/axios': { version: axios, integrity },
  } });
  git(source, ['init']); git(source, ['add', '.']);
  git(source, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'synthetic dependency fixture']);
  const pin = { commit: git(source, ['rev-parse', 'HEAD']), installerSha256: 'a'.repeat(64) };
  await write(app, 'package.json', { name: 'offerpilot-desktop', main: 'main.cjs', dependencies: { 'electron-updater': updater } });
  await write(app, 'main.cjs', "require('electron-updater');");
  await write(app, `${UPDATER}/package.json`, { name: 'electron-updater', version: updater, main: 'out/main.js',
    dependencies: { 'builder-util-runtime': runtime } });
  await write(app, `${UPDATER}/out/main.js`, "module.exports = require('./providers/Provider');");
  await write(app, `${UPDATER}/out/providers/Provider.js`, "const runtime = require('builder-util-runtime');");
  await write(app, `${RUNTIME}/package.json`, { name: 'builder-util-runtime', version: runtime, main: 'out/index.js' });
  await write(app, `${RUNTIME}/out/index.js`, 'throw new Error("must never execute installed dependency");');
  await change?.({ root, source, app, installDir, payload, pin });
  await fs.mkdir(path.join(installDir, 'resources'), { recursive: true });
  await createPackageWithOptions(app, path.join(installDir, 'resources/app.asar'), asarOptions || {});
  await write(installDir, 'resources/web/index.html', '<script src="./assets/query-synthetic.js"></script>');
  await write(installDir, 'resources/web/assets/query-synthetic.js', '/* Synthetic Vite output; arbitrary 1.20.0 is not proof. */');
  await fs.cp(installDir, payload, { recursive: true });
  return { root, source, app, installDir, payload, pin };
}

test('installed package metadata and all updater JavaScript locations bind to PIN lock and payload hashes without executing modules', async t => {
  const f = await fixture(t), result = await auditInstalledDependencies(f);
  assert.equal(result.electronUpdater.version, '6.8.9');
  assert.equal(result.builderUtilRuntime.version, '9.7.0');
  assert.equal(result.sourceCommit, f.pin.commit);
  assert.equal(result.archive.payloadMatched, true);
  assert.equal(result.dependencyCodeExecuted, false);
  assert.equal(result.builderUtilRuntime.resolutions.length, 2);
  assert.ok(result.builderUtilRuntime.resolutions.every(value => value.packagePath === RUNTIME));
  assert.equal(result.axios.expectedVersion, '1.20.0');
  assert.equal(result.axios.directInstalledVersionProven, false);
  assert.equal(result.axios.bundleFiles.length, 2);
  for (const sha of Object.values(result.sourceSha256)) assert.match(sha, /^[a-f0-9]{64}$/);
  assert.doesNotMatch(JSON.stringify(result), /PIN source|must never|arbitrary|fake-secret/);
});

test('expectations derive from the exact selected PIN, never from newer helper source', async t => {
  const f = await fixture(t, { updater: '6.6.2', runtime: '9.3.1', axios: '1.15.0' });
  const result = await auditInstalledDependencies(f);
  assert.equal(result.electronUpdater.version, '6.6.2');
  assert.equal(result.builderUtilRuntime.version, '9.3.1');
  assert.equal(result.axios.expectedVersion, '1.15.0');
  assert.notEqual(result.electronUpdater.version, '6.8.9');
});

test('Windows ASAR lookup reads nested metadata and preserves dependency rejection causes', async t => {
  const good = await fixture(t);
  const missing = await fixture(t, { change: ({ app }) => fs.rm(path.join(app, RUNTIME, 'package.json')) });
  const stale = await fixture(t, { change: async ({ app }) => {
    await write(app, `${UPDATER}/${RUNTIME}/package.json`, {
      name: 'builder-util-runtime', version: '9.3.1', main: 'index.js',
    });
    await write(app, `${UPDATER}/${RUNTIME}/index.js`, '');
  } });
  // Exercise the real pinned ASAR implementation with Windows lookup semantics
  // on every host; fixture creation and disk I/O still use the host filesystem.
  const getFile = Filesystem.prototype.getFile;
  t.mock.method(Filesystem.prototype, 'getFile', function (...args) {
    const saved = { dirname: path.dirname, basename: path.basename, sep: path.sep };
    Object.assign(path, { dirname: path.win32.dirname, basename: path.win32.basename, sep: path.win32.sep });
    try { return getFile.apply(this, args); }
    finally { Object.assign(path, saved); }
  });
  const separator = path.sep;
  path.sep = path.win32.sep;
  t.after(() => { path.sep = separator; });
  assert.equal((await auditInstalledDependencies(good)).builderUtilRuntime.version, '9.7.0');
  await assert.rejects(auditInstalledDependencies(missing), /required installed dependency file missing/);
  await assert.rejects(auditInstalledDependencies(stale), /resolved installed runtime version differs from PIN/);
});
for (const [name, change] of [
  ['old nested runtime despite a correct root', async ({ app }) => {
    await write(app, `${UPDATER}/${RUNTIME}/package.json`, { name: 'builder-util-runtime', version: '9.3.1', main: 'out/index.js' });
    await write(app, `${UPDATER}/${RUNTIME}/out/index.js`, '');
  }],
  ['deeper nested runtime in actual importer directory', async ({ app }) => {
    await write(app, `${UPDATER}/out/providers/${RUNTIME}/package.json`, { name: 'builder-util-runtime', version: '9.3.1', main: 'index.js' });
    await write(app, `${UPDATER}/out/providers/${RUNTIME}/index.js`, '');
  }],
  ['whitespace require and hidden importer file shadow', async ({ app }) => {
    await write(app, `${UPDATER}/out/hidden/Hidden.js`, "require ('builder-util-runtime');");
    await write(app, `${UPDATER}/out/hidden/node_modules/builder-util-runtime.js`, 'module.exports = {};');
  }],
  ['nested package self-reference overrides upward lookup', async ({ app }) => {
    await write(app, `${UPDATER}/out/providers/package.json`, { name: 'builder-util-runtime', version: '9.3.1', exports: './wrong.js' });
    await write(app, `${UPDATER}/out/providers/wrong.js`, '');
  }],
  ['unused old runtime contamination', async ({ app }) => {
    await write(app, `node_modules/unrelated/${RUNTIME}/package.json`, { name: 'builder-util-runtime', version: '9.3.1' });
  }],
  ['missing runtime metadata', ({ app }) => fs.rm(path.join(app, RUNTIME, 'package.json'))],
  ['missing runtime entrypoint', ({ app }) => fs.rm(path.join(app, RUNTIME, 'out/index.js'))],
  ['wrong updater version', ({ app }) => write(app, `${UPDATER}/package.json`, { name: 'electron-updater', version: '6.6.2', main: 'out/main.js' })],
  ['package main escapes its directory', ({ app }) => write(app, `${RUNTIME}/package.json`, { name: 'builder-util-runtime', version: '9.7.0', main: '../electron-updater/out/main.js' })],
  ['file shadows package resolution', ({ app }) => write(app, `${UPDATER}/out/node_modules/builder-util-runtime.js`, 'module.exports = {};')],
  ['nested directory lacks package metadata instead of falling back', ({ app }) => write(app, `${UPDATER}/${RUNTIME}/index.js`, 'module.exports = {};')],
  ['unsupported exports resolution', ({ app }) => write(app, `${RUNTIME}/package.json`, { name: 'builder-util-runtime', version: '9.7.0', main: 'out/index.js', exports: './different.js' })],

  ['dirty source lock', ({ source }) => write(source, 'desktop/package-lock.json', { wrong: true })],
  ['ambiguous case archive entries', ({ app }) => write(app, 'Package.json', '{}')],
]) {
  test(`dependency audit rejects ${name}`, async t => {
    const f = await fixture(t, { change });
    await assert.rejects(auditInstalledDependencies(f));
  });
}

test('payload mismatch fails before installed version can pass', async t => {
  const f = await fixture(t);
  await fs.appendFile(path.join(f.installDir, 'resources/app.asar'), 'tampered');
  await assert.rejects(auditInstalledDependencies(f), /differs from installer payload/);
});
test('Vite payload mismatch cannot be laundered by a matching source Axios version', async t => {
  const f = await fixture(t);
  await fs.appendFile(path.join(f.installDir, 'resources/web/assets/query-synthetic.js'), 'tampered');
  await assert.rejects(auditInstalledDependencies(f), /Vite bytes differ/);
});
test('unpacked archive metadata is refused instead of read outside the archive', async t => {
  const f = await fixture(t, { asarOptions: { unpack: '**/package.json' } });
  await assert.rejects(auditInstalledDependencies(f), /links or unpacked/);
});
test('source HEAD, wrong repository root and Git override pollution cannot substitute a different source', async t => {
  const f = await fixture(t);
  assert.throws(() => readPinnedDependencySource(f.source, 'f'.repeat(40)), /exact PIN checkout/);
  assert.throws(() => readPinnedDependencySource(path.join(f.source, 'desktop'), f.pin.commit), /identity mismatch/);
  const saved = { GIT_DIR: process.env.GIT_DIR, GIT_WORK_TREE: process.env.GIT_WORK_TREE };
  t.after(() => { for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
  process.env.GIT_DIR = path.join(f.root, 'not-a-repository');
  process.env.GIT_WORK_TREE = f.root;
  assert.equal(readPinnedDependencySource(f.source, f.pin.commit).commit, f.pin.commit);
});
test('upward resolution includes nearest nested packages but never crosses application root', () => {
  assert.deepEqual(nodeModuleCandidates(`${UPDATER}/out/providers/Provider.js`, 'builder-util-runtime'), [
    `${UPDATER}/out/providers/${RUNTIME}`, `${UPDATER}/out/${RUNTIME}`, `${UPDATER}/${RUNTIME}`, RUNTIME,
  ]);
  for (const name of ['../outside', '/outside', 'C:/outside', 'a\\b', 'a//b', 'a/../b', 'a/./b', 'a./b', 'a /b']) {
    assert.throws(() => safeRelativePath(name));
  }
});

test('native aliases, Unicode paths and reliable bigint identities bind to the same directory', async t => {
  const f = await fixture(t), real = syncFs.realpathSync.native(f.installDir);
  const aliases = [path.join(f.root, 'INSTALL~1'), path.join(f.root, '安装 APPLICATION')];
  const map = value => aliases.includes(value) ? f.installDir : value;
  const ports = { ...syncFs, realpathSync: { native: value => syncFs.realpathSync.native(map(value)) },
    lstatSync: (...args) => syncFs.lstatSync(map(args[0]), ...args.slice(1)) };
  for (const alias of aliases) {
    assert.equal(assertDirectoryIdentity(alias, real, ports), true);
    assert.equal(unlinkedFile(alias, 'resources/app.asar', ports), unlinkedFile(f.installDir, 'resources/app.asar'));
  }
  assert.throws(() => assertDirectoryIdentity(f.installDir, f.payload));
  for (const invalid of [{ ino: 0n }, { ino: 1 }, { dev: -1n }, { dev: undefined }]) {
    assert.throws(() => assertDirectoryIdentity(real, real, { ...syncFs,
      statSync: (...args) => ({ ...syncFs.statSync(...args), ...invalid, isDirectory: () => true }),
    }), /reliable directory identity/);
  }
});
test('junction or symlink at installation root or ancestor is refused even with matching bytes', async t => {
  const f = await fixture(t), type = process.platform === 'win32' ? 'junction' : 'dir';
  const link = path.join(f.root, 'linked install'), ancestor = path.join(f.root, 'linked ancestor');
  await fs.symlink(f.installDir, link, type); await fs.symlink(f.root, ancestor, type);
  for (const installDir of [link, link + path.sep, path.join(ancestor, path.basename(f.installDir))]) {
    await assert.rejects(auditInstalledDependencies({ ...f, installDir }), /must not use links/);
  }
});
test('file link evidence and escaped canonical parent are refused on every platform', async t => {
  const f = await fixture(t), archive = unlinkedFile(f.installDir, 'resources/app.asar');
  const original = syncFs.lstatSync;
  t.mock.method(syncFs, 'lstatSync', (value, ...args) => value === archive
    ? { isFile: () => true, isSymbolicLink: () => true } : original(value, ...args));
  await assert.rejects(auditInstalledDependencies(f), /regular and unlinked/);
  t.mock.restoreAll();
  const native = syncFs.realpathSync.native;
  t.mock.method(syncFs.realpathSync, 'native', value => value === archive
    ? path.join(f.payload, 'resources/app.asar') : native(value));
  await assert.rejects(auditInstalledDependencies(f), /identity mismatch/);
});

test('comments and alternate require spellings do not determine the audited location set', async t => {
  const f = await fixture(t, { change: async ({ app }) => {
    await write(app, `${UPDATER}/out/providers/Provider.js`, "/* require('builder-util-runtime') */");
    await write(app, `${UPDATER}/out/hidden/Hidden.js`, "require ('builder-util-runtime');");
  } });
  const result = await auditInstalledDependencies(f);
  assert.equal(result.builderUtilRuntime.resolution, 'static-node-lookup-from-every-installed-updater-js-location');
  assert.equal(result.builderUtilRuntime.resolutions.length, 3);
  assert.ok(result.builderUtilRuntime.resolutions.some(item => item.importer.endsWith('/hidden/Hidden.js')));
});

test('archive-internal symbolic links are refused without filesystem symlink privileges', async t => {
  const f = await fixture(t), archive = path.join(f.installDir, 'resources/app.asar');
  const bytes = Buffer.from('{}');
  await createPackageFromStreams(archive, [
    { path: 'package.json', type: 'file', stat: { size: bytes.length, mode: 0o644 }, streamGenerator: () => Readable.from([bytes]) },
    { path: 'linked-metadata', type: 'link', symlink: 'package.json', stat: { mode: 0o120777 } },
  ]);
  await fs.copyFile(archive, path.join(f.payload, 'resources/app.asar'));
  await assert.rejects(auditInstalledDependencies(f), /links or unpacked/);
});
