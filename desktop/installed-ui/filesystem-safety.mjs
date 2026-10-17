// Shared read-only identity checks. Native realpath and bigint identities support
// Windows 8.3/case aliases without accepting a junction or symlink ancestor.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

export function unlinkedDirectory(value, filesystem = fs) {
  assert.ok(typeof value === 'string' && path.isAbsolute(value), 'absolute directory required');
  let directory = value;
  while (true) {
    const root = path.parse(directory).root;
    const separator = process.platform === 'win32' ? /[/\\]$/ : /\/$/;
    while (directory.length > root.length && separator.test(directory)) directory = directory.slice(0, -1);
    const stat = filesystem.lstatSync(directory);
    assert.ok(stat.isDirectory() && !stat.isSymbolicLink(), 'directory chain must not use links');
    const parent = path.dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  const canonical = filesystem.realpathSync.native(value);
  assert.ok(typeof canonical === 'string' && path.isAbsolute(canonical));
  const stat = filesystem.statSync(canonical, { bigint: true });
  assert.ok(stat.isDirectory(), 'directory identity required');
  return { canonical, ...fileIdentity(stat) };
}
function fileIdentity(stat) {
  assert.ok(typeof stat.dev === 'bigint' && stat.dev >= 0n
    && typeof stat.ino === 'bigint' && stat.ino > 0n, 'reliable directory identity required');
  return { dev: stat.dev, ino: stat.ino };
}
export function assertDirectoryIdentity(actual, expected, filesystem = fs) {
  const left = unlinkedDirectory(actual, filesystem), right = unlinkedDirectory(expected, filesystem);
  assert.deepEqual({ dev: left.dev, ino: left.ino }, { dev: right.dev, ino: right.ino }, 'directory identity mismatch');
  return true;
}
export function safeRelativePath(value) {
  assert.ok(typeof value === 'string' && value.length > 0 && value.length < 4096
    && !/[\\:\x00-\x1f]/.test(value), 'unsafe relative path');
  assert.ok(value.split('/').every(part => part && part !== '.' && part !== '..' && !/[. ]$/.test(part)), 'unsafe relative path');
  return value;
}
export function unlinkedFile(root, relative, filesystem = fs) {
  safeRelativePath(relative);
  const directory = unlinkedDirectory(root, filesystem).canonical;
  const filename = path.join(directory, ...relative.split('/'));
  const parent = unlinkedDirectory(path.dirname(filename), filesystem);
  const stat = filesystem.lstatSync(filename, { bigint: true });
  assert.ok(stat.isFile() && !stat.isSymbolicLink(), 'installed file must be regular and unlinked');
  const canonical = filesystem.realpathSync.native(filename);
  assertDirectoryIdentity(path.dirname(canonical), parent.canonical, filesystem);
  assert.deepEqual(fileIdentity(stat), fileIdentity(filesystem.statSync(canonical, { bigint: true })), 'file identity changed');
  return canonical;
}
