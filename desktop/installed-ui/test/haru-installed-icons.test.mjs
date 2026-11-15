import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { readInstalledIcons, validateInstalledIcon, verifyShortcutIdentity } from '../haru-installed-icons.mjs';

test('installed icon extraction is bounded to the verified executable and its fixed desktop shortcut', async () => {
  const read = vm.runInNewContext(`(${readInstalledIcons.toString()})`, {});
  const requested = [];
  const exe = path.resolve('fixture', 'OfferPilot Desktop.exe');
  const desktop = path.resolve('fixture', 'Desktop');
  const shortcut = { target: exe, icon: exe, args: '', iconIndex: 0 };
  const app = { getPath: name => name === 'exe' ? exe : desktop, getFileIcon: async (filename, { size }) => {
    requested.push({ filename, size });
    return { isEmpty: () => false, getSize: () => ({ width: 32, height: 32 }), toPNG: () => Buffer.from('synthetic') };
  } };
  const shell = { readShortcutLink: filename => { assert.equal(path.normalize(filename), path.join(desktop, 'OfferPilot Desktop.lnk')); return shortcut; } };
  const result = await read({ app, shell });
  assert.equal(result.ok, true);
  assert.equal(result.exe, exe);
  assert.equal(result.shortcut, shortcut);
  assert.equal(requested.length, 2);
  assert.deepEqual(requested.map(item => item.size), ['small', 'normal']);
  shell.readShortcutLink = () => { throw new Error('private path must not be returned'); };
  const failed = await read({ app, shell });
  assert.equal(JSON.stringify(failed), JSON.stringify({ ok: false, phase: 'desktop-shortcut-read', errorClass: 'Error' }));

});

test('saved native icon validation rejects unsupported names, wrong dimensions and malformed PNG', () => {
  const bytes = Buffer.alloc(24);
  Buffer.from('89504e470d0a1a0a', 'hex').copy(bytes);
  bytes.writeUInt32BE(32, 16); bytes.writeUInt32BE(32, 20);
  const item = { size: 'normal', dimensions: { width: 32, height: 32 }, png: bytes.toString('base64') };
  assert.deepEqual(validateInstalledIcon(item), bytes);
  for (const bad of [{ ...item, size: '../outside' }, { ...item, dimensions: { width: 0, height: 0 } }, { ...item, png: 'bad' }]) {
    assert.throws(() => validateInstalledIcon(bad));
  }
});

test('shortcut identity rejects other executables, icons and arguments using the shared unlinked-file checks', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'haru-icon-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const exe = path.join(root, 'OfferPilot Desktop.exe'), other = path.join(root, 'Other.exe');
  fs.writeFileSync(exe, 'synthetic'); fs.writeFileSync(other, 'synthetic');
  const result = { exe, shortcut: { target: exe, icon: exe, args: '', iconIndex: 0 } };
  assert.equal(verifyShortcutIdentity(result, exe), true);
  for (const shortcut of [{ ...result.shortcut, target: other }, { ...result.shortcut, icon: other }, { ...result.shortcut, args: '--unsafe' }, { ...result.shortcut, iconIndex: 1 }]) {
    assert.throws(() => verifyShortcutIdentity({ ...result, shortcut }, exe));
  }
});
