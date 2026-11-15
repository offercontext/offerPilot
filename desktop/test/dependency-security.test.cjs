'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { assertBoundedMinimatch, assertAsarRoundTrip } = require('../test-support/dependency-compatibility.cjs');
const builderRequire = createRequire(require.resolve('app-builder-lib'));
const universalRequire = createRequire(require.resolve('@electron/universal'));

for (const [name, dependencyRequire] of [['app-builder-lib', builderRequire], ['@electron/universal', universalRequire]]) {
  test(`${name} resolves a bounded minimatch optimizer`, () => {
    assertBoundedMinimatch(dependencyRequire('minimatch'));
  });
}

test('electron-builder filters preserve configured files, magic, exclusions and partial directories', () => {
  const { FileMatcher } = require('app-builder-lib/out/fileMatcher');
  const { hasMagic } = require('app-builder-lib/out/util/filter');
  const { Minimatch } = builderRequire('minimatch');
  const root = path.resolve('fixture');
  const file = { isDirectory: () => false }, directory = { isDirectory: () => true };
  const matcher = new FileMatcher(root, root, value => value, require('../package.json').build.files);
  const filter = matcher.createFilter();
  for (const name of ['main.cjs', 'assets/haru-icon.ico', 'assets/nested/sprite.png']) {
    assert.equal(filter(path.join(root, name), file), true, name);
  }
  assert.equal(filter(path.join(root, 'assets'), directory), true);
  assert.equal(filter(path.join(root, 'test/secret.txt'), file), false);
  assert.equal(hasMagic(new Minimatch('assets/*.{png,ico}')), true);
  assert.equal(hasMagic(new Minimatch('assets')), false);
  const exclusions = new FileMatcher(root, root, value => value, ['**/*', '!**/*.map']).createFilter();
  assert.equal(exclusions(path.join(root, '.settings.json'), file), true);
  assert.equal(exclusions(path.join(root, 'assets/code.js.map'), file), false);
});

test('legacy builder ASAR retains callable minimatch and callback glob', async t => {
  const asarRequire = createRequire(builderRequire.resolve('@electron/asar'));
  assert.equal(typeof asarRequire('minimatch'), 'function');
  assert.equal(typeof asarRequire('glob'), 'function');
  await assertAsarRoundTrip(t, builderRequire('@electron/asar'));
});

test('universal named minimatch API preserves allowlist boundaries on every platform', () => {
  const { minimatch } = universalRequire('minimatch');
  for (const platform of ['win32', 'darwin', 'linux']) {
    const options = { matchBase: true, platform };
    assert.equal(minimatch('native/extra-x64.node', '*-x64.node', options), true);
    assert.equal(minimatch('native/extra-arm64.node', '*-x64.node', options), false);
    assert.equal(minimatch('native/extra-x64.txt', '*.node', options), false);
    assert.equal(minimatch('Contents/Info.plist', '*.plist', options), true);
  }
});

// universal packages macOS apps and assumes POSIX archive entry separators.
// Its matcher contract above still runs on Windows, where this merge API is unsupported.
test('universal ASAR allowlist rejects unexpected files and merges an allowed file', {
  skip: process.platform === 'win32' ? 'macOS universal merge requires POSIX archive paths' : false,
}, async t => {
  const asar = universalRequire('@electron/asar');
  const { mergeASARs } = require(path.join(path.dirname(require.resolve('@electron/universal')), 'asar-utils.js'));
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot universal '));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  for (const arch of ['x64', 'arm64']) {
    await fs.mkdir(path.join(root, arch));
    await fs.writeFile(path.join(root, arch, 'common.txt'), 'shared');
  }
  await fs.writeFile(path.join(root, 'x64', 'extra-x64.txt'), 'x64 only');
  for (const arch of ['x64', 'arm64']) await asar.createPackage(path.join(root, arch), path.join(root, arch + '.asar'));
  const options = { x64AsarPath: path.join(root, 'x64.asar'), arm64AsarPath: path.join(root, 'arm64.asar'), outputAsarPath: path.join(root, 'merged.asar') };
  await assert.rejects(mergeASARs({ ...options, singleArchFiles: '*.node' }), /not covered by/);
  await mergeASARs({ ...options, singleArchFiles: '*-x64.txt' });
  assert.equal(asar.extractFile(options.outputAsarPath, 'extra-x64.txt').toString(), 'x64 only');
});
