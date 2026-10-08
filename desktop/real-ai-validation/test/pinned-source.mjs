// Test-only source reader. A helper checkout may be shallow and newer than the
// installer: product contract assertions must read immutable PIN blobs instead.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { realpathSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PIN } from '../contract.mjs';

const localRoot = fileURLToPath(new URL('../../../', import.meta.url));
const sourceFiles = new Set(['src/offerpilot/api.py',
  'web/src/features/assistantSurface/assistantPresentation.ts']);

export function readPinnedSource(relative, { env = process.env } = {}) {
  assert.ok(sourceFiles.has(relative), 'PINNED_SOURCE_PATH_DENIED');
  const configured = Object.hasOwn(env, 'AI_PRODUCT_SOURCE');
  const selected = configured ? env.AI_PRODUCT_SOURCE : localRoot;
  assert.ok(typeof selected === 'string' && selected.trim() && path.isAbsolute(selected),
    'PINNED_PRODUCT_SOURCE_REQUIRED');
  try {
    const root = realpathSync(selected);
    // Do not inherit Git repository/config overrides or lazily fetch a missing
    // object. Reading these two committed files never needs any network access.
    const gitEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^GIT_/i.test(key)));
    const git = args => execFileSync('git', ['--no-replace-objects', '-c', 'protocol.allow=never', '-C', root, ...args], {
      encoding: 'utf8', env: { ...gitEnv, GIT_NO_LAZY_FETCH: '1' },
      stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 2 * 1024 * 1024,
    });
    assert.equal(realpathSync(git(['rev-parse', '--show-toplevel']).trim()), root);
    assert.equal(git(['rev-parse', '--verify', `${PIN.commit}^{commit}`]).trim(), PIN.commit);
    // Only the no-environment local-development path may use a newer checkout
    // with the exact PIN object. An explicit source must itself be pinned.
    if (configured) assert.equal(git(['rev-parse', '--verify', 'HEAD']).trim(), PIN.commit);
    return git(['show', '--no-ext-diff', '--no-textconv', `${PIN.commit}:${relative}`]);
  } catch {
    throw new Error('PINNED_PRODUCT_SOURCE_UNVERIFIED');
  }
}
