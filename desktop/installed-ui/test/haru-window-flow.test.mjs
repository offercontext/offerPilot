import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { nativeHandles, readNativeWindow, assertRestoredWindow } from '../haru-window-flow.mjs';

test('native input takes only the already role-verified exact owner/Haru window IDs', () => {
  const win = value => ({ isDestroyed: () => false, getNativeWindowHandle: () => {
    const buffer = Buffer.alloc(8); buffer.writeBigUInt64LE(BigInt(value)); return buffer;
  } });
  const windows = new Map([[7, win(71)], [8, win(81)]]);
  const electron = { BrowserWindow: { fromId: id => windows.get(id) } };
  assert.deepEqual(nativeHandles(electron, { owner: 7, haru: 8 }), { pid: process.pid, owner: '71', haru: '81' });
  for (const ids of [{ owner: 7, haru: 7 }, { owner: 7, haru: 99 }, { owner: 99, haru: 8 }]) {
    assert.throws(() => nativeHandles(electron, ids));
  }
  windows.get(8).isDestroyed = () => true;
  assert.throws(() => nativeHandles(electron, { owner: 7, haru: 8 }));
  windows.get(8).isDestroyed = () => false;
  windows.get(8).getNativeWindowHandle = () => Buffer.alloc(2);
  assert.throws(() => nativeHandles(electron, { owner: 7, haru: 8 }));
});

test('native observations dispose their inspector handle even when a real getter fails', async () => {
  let disposed = 0;
  const app = { browserWindow: async () => ({ evaluate: async () => { throw new Error('window closed'); }, dispose: async () => { disposed++; } }) };
  await assert.rejects(readNativeWindow(app, {}), /window closed/);
  assert.equal(disposed, 1);
});

test('restore needs a proven starting native condition and visible, restored, focused result', () => {
  const after = { visible: true, minimized: false, focused: true };
  const starts = { minimized: { visible: true, minimized: true, focused: false },
    'hidden-after-close': { visible: false, minimized: false, focused: false },
    background: { visible: true, minimized: false, focused: false } };
  for (const [mode, before] of Object.entries(starts)) {
    assertRestoredWindow(before, after, mode);
    for (const patch of [{ visible: false }, { minimized: true }, { focused: false }, { focused: 'true' }]) {
      assert.throws(() => assertRestoredWindow(before, { ...after, ...patch }, mode));
    }
    assert.throws(() => assertRestoredWindow(after, after, mode));
  }
  assert.throws(() => assertRestoredWindow(after, after, 'unknown'));
});

test('installed Win32 helper preserves reviewed owned-window boundaries and layered OS pixels', async () => {
  const actual = (await fs.readFile(new URL('../haru-native-input.ps1', import.meta.url), 'utf8')).replace(/\r\n/g, '\n');
  const reviewed = (await fs.readFile(new URL('../../haru-ui-preview/native-drag.ps1', import.meta.url), 'utf8')).replace(/\r\n/g, '\n')
    .replace("'evidence/native-os-composited-haru.png'", "'evidence/screens/haru-native-os-composited.png'")
    .replace("file = 'native-os-composited-haru.png'", "file = 'screens/haru-native-os-composited.png'");
  assert.equal(actual, reviewed, 'only fixed evidence destination may differ from the reviewed Win32 helper');
  assert.match(actual, /0x40CC0020/);
  assert.match(actual, /Format24bppRgb/);
  assert.doesNotMatch(actual, /ExecutionPolicy|Set-ExecutionPolicy|Add-MpPreference|Set-NetFirewall/);
});
