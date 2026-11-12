import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { installDownloadObservation } from './observe.mjs';
function setup(filename, invalid = false) {
  const session = new EventEmitter(), owner = { id: 7, session, getURL: () => 'http://127.0.0.1/' }, app = {};
  let selected = '';
  session.on('will-download', () => { selected = 'selected.pdf'; });
  const item = new EventEmitter();
  item.getSavePath = () => selected;
  for (const method of ['getState', 'getFilename', 'getReceivedBytes', 'getTotalBytes', 'getMimeType']) item[method] = () => { if (invalid) throw new Error('destroyed'); return method; };
  installDownloadObservation({ app, BrowserWindow: { getAllWindows: () => [{ webContents: owner }] } }, { ownerURL: owner.getURL(), filename });
  return { session, owner, item, app };
}
test('appends after production, exact owner only, records updated/done and isolates destroyed getters', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'native-observer-test-')), filename = path.join(dir, 'events');
  try {
    const {session, owner, item} = setup(filename, true);
    session.emit('will-download', { defaultPrevented: true }, item, {});
    session.emit('will-download', { defaultPrevented: true }, item, owner);
    item.emit('updated', {}, 'interrupted'); item.emit('done', {}, 'cancelled');
    const records = fs.readFileSync(filename, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(records.map(x => x.type), ['installed', 'will-download-after-production', 'updated', 'done']);
    assert.equal(records[1].getSavePath, 'selected.pdf'); assert.equal(records[1].defaultPrevented, true);
    assert.match(records[1].getState.error, /destroyed/); assert.equal(records[3].state, 'cancelled');
  } finally { fs.rmSync(dir, { recursive: true }); }
});
test('diagnostic write errors never escape download callbacks or prevent later listeners', () => {
  const {session, owner, item, app} = setup(path.join(os.tmpdir(), 'missing-native-observer-directory', 'file'));
  let after = false; session.on('will-download', () => { after = true; });
  assert.doesNotThrow(() => session.emit('will-download', {}, item, owner));
  assert.doesNotThrow(() => item.emit('done', {}, 'completed'));
  assert.equal(after, true); assert.ok(app.__resumeExportObservation.writeErrors.length);
});
