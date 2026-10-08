// Test-only source reader. A helper checkout may be shallow and newer than the
// installer: product contract assertions must read immutable PIN blobs instead.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PIN } from '../contract.mjs';

const localRoot = fileURLToPath(new URL('../../../', import.meta.url));
const sourceFiles = new Set(['src/offerpilot/api.py',
  'web/src/features/assistantSurface/assistantPresentation.ts']);

// Same layered checks as the installed ORT identity guard, kept test-only and
// synchronous so a cold source checkout needs no ASAR/runtime dependencies.
export function assertSourceDirectoryIdentity(actual, expected, filesystem = fs) {
  const identity = value => {
    assert.ok(typeof value === 'string' && path.isAbsolute(value));
    let directory = value;
    while (true) {
      // lstat('link/') follows a directory link; inspect its unlinked spelling.
      const root = path.parse(directory).root;
      const separator = process.platform === 'win32' ? /[/\\]$/ : /\/$/;
      while (directory.length > root.length && separator.test(directory)) directory = directory.slice(0, -1);
      const stat = filesystem.lstatSync(directory);
      assert.ok(stat.isDirectory() && !stat.isSymbolicLink());
      const parent = path.dirname(directory);
      if (parent === directory) break;
      directory = parent;
    }
    // JS realpathSync preserves Windows short-name/case aliases. Use the native
    // resolver and directory identity, never lowercase/prefix string equality.
    const canonical = filesystem.realpathSync.native(value);
    assert.ok(typeof canonical === 'string' && path.isAbsolute(canonical));
    const stat = filesystem.statSync(canonical, { bigint: true });
    assert.ok(stat.isDirectory() && typeof stat.dev === 'bigint' && stat.dev >= 0n
      && typeof stat.ino === 'bigint' && stat.ino > 0n, 'reliable directory identity required');
    return { dev: stat.dev, ino: stat.ino };
  };
  assert.deepEqual(identity(actual), identity(expected));
  return true;
}

export function readPinnedSource(relative, { env = process.env } = {}) {
  assert.ok(sourceFiles.has(relative), 'PINNED_SOURCE_PATH_DENIED');
  const configured = Object.hasOwn(env, 'AI_PRODUCT_SOURCE');
  const selected = configured ? env.AI_PRODUCT_SOURCE : localRoot;
  assert.ok(typeof selected === 'string' && selected.trim() && path.isAbsolute(selected),
    'PINNED_PRODUCT_SOURCE_REQUIRED');
  let stage = 'SOURCE_REALPATH';
  try {
    const root = fs.realpathSync.native(selected);
    // Do not inherit Git repository/config overrides or lazily fetch a missing
    // object. Reading these two committed files never needs any network access.
    const gitEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^GIT_/i.test(key)));
    const git = args => execFileSync('git', ['--no-replace-objects', '-c', 'protocol.allow=never', '-C', root, ...args], {
      encoding: 'utf8', env: { ...gitEnv, GIT_NO_LAZY_FETCH: '1' },
      stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 2 * 1024 * 1024,
    });
    stage = 'ROOT_IDENTITY';
    assertSourceDirectoryIdentity(selected, git(['rev-parse', '--show-toplevel']).trim());
    stage = 'PIN_OBJECT';
    assert.equal(git(['rev-parse', '--verify', `${PIN.commit}^{commit}`]).trim(), PIN.commit);
    // Only the no-environment local-development path may use a newer checkout
    // with the exact PIN object. An explicit source must itself be pinned.
    stage = 'PIN_HEAD';
    if (configured) assert.equal(git(['rev-parse', '--verify', 'HEAD']).trim(), PIN.commit);
    stage = 'PIN_BLOB';
    return git(['show', '--no-ext-diff', '--no-textconv', `${PIN.commit}:${relative}`]);
  } catch {
    // Fixed stages identify the failing boundary without emitting paths/errors.
    throw new Error(`PINNED_PRODUCT_SOURCE_UNVERIFIED:${stage}`);
  }
}
