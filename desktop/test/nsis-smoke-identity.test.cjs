'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { nsisIdentity } = require('../nsis-smoke-identity.cjs');
const pkg = require('../package.json');
const source = 'const ELECTRON_BUILDER_NS_UUID = builder_util_runtime_1.UUID.parse("50e065bc-3134-11e6-9bab-38c9862bdaf3");\n' +
  'const guid = options.guid || builder_util_runtime_1.UUID.v5(appInfo.id, ELECTRON_BUILDER_NS_UUID);';
test('installer preflight derives the exact stable app GUID from the reviewed packager algorithm', () => {
  assert.deepEqual(nsisIdentity(pkg, source), {
    appId: 'org.offerpilot.desktop', productName: 'OfferPilot Desktop', guid: '3071a979-5689-541d-a10b-f0047e6ae1a7',
  });
});
test('unknown packager identity or custom installer fails closed before installation', () => {
  for (const bad of ['', source.replace('UUID.v5', 'UUID.v4'), source.replace('appInfo.id', 'appInfo.name')]) {
    assert.throws(() => nsisIdentity(pkg, bad));
  }
  for (const change of [{ guid: 'custom' }, { script: 'custom.nsi' }, { include: 'custom.nsh' }]) {
    assert.throws(() => nsisIdentity({ ...pkg, build: { ...pkg.build, nsis: { ...pkg.build.nsis, ...change } } }, source));
  }
  assert.throws(() => nsisIdentity({ ...pkg, build: { ...pkg.build, appId: 'different.app' } }, source));
});
