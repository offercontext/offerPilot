// Test-only source reader. A helper checkout may be shallow and newer than the
// installer: product contract assertions must read immutable PIN blobs instead.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PIN } from '../contract.mjs';

const localRoot = fileURLToPath(new URL('../../../', import.meta.url));
const sourceFiles = new Set(['src/offerpilot/api.py', 'src/offerpilot/pilot_control.py',
  'desktop/haru-protocol.cjs', 'web/src/features/assistantSurface/useDesktopHaruOwner.ts',
  'src/offerpilot/pilot_timeline.py', 'src/offerpilot/pilot_runtime/managed_execution.py',
  'web/src/services/pilotRuntime.ts', 'web/src/services/chat.ts',
  'web/src/components/ChatPanel/MessageBubble.tsx',
  'web/src/components/ChatPanel/ChatPanel.module.css',
  'web/src/features/assistantSurface/DesktopHaruWindow.tsx',
  'web/src/features/assistantSurface/DesktopHaruWindow.css',
  'web/src/features/assistantSurface/usePilotConversationController.ts',
  'web/src/features/assistantSurface/AssistantSurfaceProvider.test.tsx',
  'src/offerpilot/pilot_runtime/service.py', 'src/offerpilot/pilot_runtime/event_sink.py',
  'web/src/features/assistantSurface/assistantPresentation.ts']);

import { assertDirectoryIdentity as assertSourceDirectoryIdentity } from '../../installed-ui/filesystem-safety.mjs';
export { assertSourceDirectoryIdentity };

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
    // object. Reading these allowlisted committed files never needs network access.
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
