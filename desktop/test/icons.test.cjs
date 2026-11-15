'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('Windows application and installer use the selected multi-size transparent Haru icon', () => {
  const root = path.join(__dirname, '..');
  const config = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).build;
  assert.equal(config.win.icon, 'assets/haru-icon.ico');
  assert.equal(config.nsis.installerIcon, config.win.icon);
  assert.equal(config.nsis.uninstallerIcon, config.win.icon);
  const ico = fs.readFileSync(path.join(root, config.win.icon));
  assert.equal(ico.readUInt16LE(0), 0);
  assert.equal(ico.readUInt16LE(2), 1);
  const count = ico.readUInt16LE(4);
  const sizes = [];
  for (let index = 0; index < count; index++) {
    const entry = 6 + index * 16;
    const width = ico[entry] || 256;
    assert.equal(ico[entry + 1] || 256, width);
    assert.equal(ico.readUInt16LE(entry + 6), 32);
    const bytes = ico.readUInt32LE(entry + 8), offset = ico.readUInt32LE(entry + 12);
    assert.ok(offset >= 6 + count * 16 && offset + bytes <= ico.length);
    assert.equal(ico.subarray(offset, offset + 8).toString('hex'), '89504e470d0a1a0a');
    sizes.push(width);
  }
  assert.deepEqual(sizes, [16, 20, 24, 32, 40, 48, 64, 128, 256]);
  const tray = fs.readFileSync(path.join(root, 'assets/haru-tray.png'));
  assert.equal(tray.readUInt32BE(16), 64);
  assert.equal(tray.readUInt32BE(20), 64);
  assert.equal(tray[25], 6, 'tray retains RGBA transparency');
});
