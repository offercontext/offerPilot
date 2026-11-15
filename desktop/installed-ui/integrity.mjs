import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';

export async function hash(filename) {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(filename)) digest.update(chunk);
  return digest.digest('hex');
}
export async function treeFiles(root, relative = '') {
  const result = [];
  for (const entry of await fs.readdir(path.join(root, relative), { withFileTypes: true })) {
    const name = path.join(relative, entry.name);
    if (entry.isDirectory()) result.push(...await treeFiles(root, name));
    else { assert.equal(entry.isFile(), true, 'payload may contain only regular files'); result.push(name); }
  }
  return result;
}
export async function verifyPayload(installed, payload) {
  const files = await treeFiles(payload);
  assert.ok(files.length > 0, 'empty payload cannot pass');
  for (const name of files) {
    assert.equal((await fs.lstat(path.join(installed, name))).isFile(), true, 'installed file must be regular');
    assert.equal(await hash(path.join(installed, name)), await hash(path.join(payload, name)), 'installed bytes differ from pinned installer payload');
  }
  return files.length;
}
export function normalizeSourceText(bytes) { return bytes.toString('utf8').replace(/\r\n/g, '\n'); }
