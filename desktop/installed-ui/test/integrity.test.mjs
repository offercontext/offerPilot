import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { hash, treeFiles, verifyPayload, normalizeSourceText } from '../integrity.mjs';

test('derived payload hashes compare actual bytes; corruption and missing files fail', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-helper-test-'));
  try {
    const installed = path.join(root, 'installed');
    const payload = path.join(root, 'payload');
    await fs.mkdir(path.join(installed, 'resources'), { recursive: true });
    await fs.mkdir(path.join(payload, 'resources'), { recursive: true });
    const name = path.join('resources', 'sample.bin');
    const bytes = Buffer.from([0, 1, 2, 128, 255]);
    await fs.writeFile(path.join(payload, name), bytes);
    await fs.writeFile(path.join(installed, name), bytes);
    await fs.writeFile(path.join(installed, 'uninstaller-extra.txt'), 'allowed installer-generated metadata');
    assert.equal(await verifyPayload(installed, payload), 1);
    assert.equal((await hash(path.join(payload, name))).length, 64);
    await fs.writeFile(path.join(installed, name), Buffer.from([0, 1, 3, 128, 255]));
    await assert.rejects(verifyPayload(installed, payload));
    await fs.rm(path.join(installed, name));
    await assert.rejects(verifyPayload(installed, payload));
    await fs.rm(path.join(payload, name));
    await assert.rejects(verifyPayload(installed, payload));
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
test('source comparison normalizes only CRLF, never other whitespace or contents', () => {
  assert.equal(normalizeSourceText(Buffer.from('one\r\ntwo\r\n')), 'one\ntwo\n');
  assert.notEqual(normalizeSourceText(Buffer.from(' one\r\n')), 'one\n');
  assert.notEqual(normalizeSourceText(Buffer.from('one\r')), 'one\n');
  assert.notEqual(normalizeSourceText(Buffer.from('one')), 'one\n');
});
test('payload traversal rejects symbolic links instead of reading outside payload', { skip: process.platform === 'win32' }, async () => {
  // Windows symlink creation requires privileges that this helper never requests.
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-helper-link-test-'));
  try {
    await fs.symlink(path.join(root, 'nonexistent'), path.join(root, 'link'));
    await assert.rejects(treeFiles(root));
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
