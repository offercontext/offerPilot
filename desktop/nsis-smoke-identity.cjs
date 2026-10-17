'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

// Bind the safety preflight to the same locked packager implementation that
// produced this job's installer. Unknown GUID construction fails closed.
function nsisIdentity(packageJson, source) {
  assert.equal(packageJson.build.appId, 'org.offerpilot.desktop');
  assert.equal(packageJson.build.productName, 'OfferPilot Desktop');
  assert.equal(packageJson.build.nsis.guid, undefined);
  assert.equal(packageJson.build.nsis.script, undefined);
  assert.equal(packageJson.build.nsis.include, undefined);
  const namespace = source.match(/ELECTRON_BUILDER_NS_UUID\s*=\s*[\w.]*UUID\.parse\(["']([0-9a-f-]{36})["']\)/i)?.[1];
  assert.ok(namespace, 'Locked NSIS namespace is unavailable');
  assert.match(source, /options\.guid\s*\|\|\s*[\w.]*UUID\.v5\(appInfo\.id,\s*ELECTRON_BUILDER_NS_UUID\)/);
  const bytes = crypto.createHash('sha1').update(Buffer.from(namespace.replaceAll('-', ''), 'hex'))
    .update(packageJson.build.appId).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString('hex');
  const guid = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  return { appId: packageJson.build.appId, productName: packageJson.build.productName, guid };
}

if (require.main === module) {
  const source = fs.readFileSync(path.join(__dirname, 'node_modules/app-builder-lib/out/targets/nsis/NsisTarget.js'), 'utf8');
  process.stdout.write(JSON.stringify(nsisIdentity(require('./package.json'), source)));
}
module.exports = { nsisIdentity };
