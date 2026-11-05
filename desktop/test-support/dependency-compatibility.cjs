'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

function assertBoundedMinimatch({ Minimatch }) {
  // GHSA-g3hv-75wg-m249: the old optimizer grows 128 rows despite this limit.
  const pattern = Array.from({ length: 8 }, (_, i) => `a${i}/**/../`).join('') + 'target';
  const matcher = new Minimatch(pattern, { optimizationLevel: 2, braceExpandMax: 32 });
  assert.ok(matcher.globParts.length <= 32, `unbounded globparts: ${matcher.globParts.length}`);
}

async function assertAsarRoundTrip(t, asar) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot 打包 compatibility '));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const source = path.join(root, 'source');
  const archive = path.join(root, 'app.asar');
  const files = {
    'main.cjs': 'module.exports = true;',
    '.settings.json': '{"hidden":true}',
    'assets/头像.txt': '中文 resource',
    'assets/nested/sprite.png': 'image fixture',
    'native/addon.node': 'native fixture',
    'unpacked/data.txt': 'unpacked directory',
    'ignored/omit.txt': 'must not ship',
  };
  for (const [name, content] of Object.entries(files)) {
    await fs.mkdir(path.dirname(path.join(source, name)), { recursive: true });
    await fs.writeFile(path.join(source, name), content);
  }
  await asar.createPackageWithOptions(source, archive, {
    unpack: '**/*.node', unpackDir: '**/unpacked',
    globOptions: { ignore: ['**/ignored', '**/ignored/**'] },
  });
  const entries = asar.listPackage(archive).map(value => value.replace(/\\/g, '/'));
  assert.ok(!entries.some(value => value.includes('ignored')));
  for (const [name, content] of Object.entries(files)) {
    if (name.startsWith('ignored/')) continue;
    assert.ok(entries.includes('/' + name), `missing archive entry ${name}`);
    assert.equal(asar.extractFile(archive, name).toString(), content);
  }
  for (const name of ['native/addon.node', 'unpacked/data.txt']) {
    assert.equal(asar.statFile(archive, name).unpacked, true);
    assert.equal(await fs.readFile(path.join(archive + '.unpacked', name), 'utf8'), files[name]);
  }
  const extracted = path.join(root, 'extracted');
  asar.extractAll(archive, extracted);
  assert.equal(await fs.readFile(path.join(extracted, 'assets/头像.txt'), 'utf8'), files['assets/头像.txt']);
}

async function assertAsarDirectoryLink(t, asar) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot linked assets '));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const source = path.join(root, 'source'), assets = path.join(source, 'assets');
  await fs.mkdir(assets, { recursive: true });
  await fs.writeFile(path.join(assets, 'sprite.txt'), 'linked resource');
  // Junctions exercise Windows directory links without requiring symlink privilege.
  await fs.symlink(assets, path.join(source, 'linked-assets'), 'junction');
  const archive = path.join(root, 'app.asar');
  await asar.createPackage(source, archive);
  const entries = asar.listPackage(archive).map(value => value.replace(/\\/g, '/'));
  assert.ok(entries.includes('/linked-assets'));
  assert.ok(!entries.some(value => value.startsWith('/linked-assets/')));
  assert.equal(asar.statFile(archive, 'linked-assets', false).link, 'assets');
  assert.equal(asar.extractFile(archive, 'linked-assets/sprite.txt').toString(), 'linked resource');
}

module.exports = { assertBoundedMinimatch, assertAsarRoundTrip, assertAsarDirectoryLink };
