import test from 'node:test';
import assert from 'node:assert/strict';
import { desktopRole, selectDesktopSurfaces, waitForDesktopSurfaces, readDesktopSecurity, validatePartitionIsolation } from '../desktop-surfaces.mjs';
const origin = 'http://127.0.0.1:8111';
const owner = { url: `${origin}/?view=settings`, role: 'owner' };
const haru = { url: `${origin}/?desktopSurface=haru`, role: 'haru' };
test('two-window selection uses exact URL and preload role, never creation order', () => {
  assert.deepEqual(selectDesktopSurfaces([haru, owner], origin), { haru, owner });
  assert.deepEqual(selectDesktopSurfaces([owner, haru], origin), { owner, haru });
  for (const candidates of [[owner], [haru], [owner, owner], [haru, haru], [owner, haru, owner],
    [owner, { ...haru, role: 'owner' }], [{ ...owner, role: 'haru' }, haru], [owner, { ...haru, role: undefined }]]) {
    assert.throws(() => selectDesktopSurfaces(candidates, origin));
  }
});
test('Haru surface aliases, duplicate query, external/credentialed and unsupported paths fail', () => {
  assert.equal(desktopRole(`${origin}/`, origin), 'owner');
  assert.equal(desktopRole(haru.url, origin), 'haru');
  for (const url of [`${origin}/?desktopSurface=owner`, `${origin}/?desktopSurface=haru&desktopSurface=haru`,
    `${origin}/?desktopSurface=haru&view=pilot`, `${origin}/api/config`, 'not-url',
    'http://localhost:8111/', 'http://127.0.0.1:8112/', 'http://user@127.0.0.1:8111/']) assert.equal(desktopRole(url, origin), null);
});
test('polling cannot return the Haru page as owner or accept a third window', async () => {
  const page = item => ({ url: () => item.url, evaluate: async () => item.role });
  const pages = [page(haru), page(owner)];
  const result = await waitForDesktopSurfaces({ windows: () => pages }, origin, 1000);
  assert.equal(result.owner.page, pages[1]);
  assert.equal(result.haru.page, pages[0]);
  await assert.rejects(waitForDesktopSurfaces({ windows: () => [...pages, pages[0]] }, origin, 1000));
  await assert.rejects(waitForDesktopSurfaces({ windows: () => [] }, origin, 0));
});
function securityFixture() {
  const ownerSession = { getStoragePath: () => 'private-owner-path' };
  const haruSession = { getStoragePath: () => null };
  const make = (id, session) => ({ id, webContents: { session,
    getLastWebPreferences: () => ({ nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true }),
    isDevToolsOpened: () => false } });
  const windows = [make(3, haruSession), make(7, ownerSession)];
  const electron = { app: { isPackaged: true, commandLine: { hasSwitch: () => false } },
    BrowserWindow: { getAllWindows: () => windows, fromId: id => windows.find(win => win.id === id) },
    session: { fromPartition: name => name === 'persist:offerpilot-desktop' ? ownerSession : haruSession } };
  return { windows, electron, ownerSession, haruSession };
}
test('security examines both exact windows and proves native partition identities without leaking paths', () => {
  const { electron } = securityFixture();
  const result = readDesktopSecurity(electron, { owner: 7, haru: 3 });
  validatePartitionIsolation(result.partitionIsolation);
  assert.equal(result.owner.sandbox, true); assert.equal(result.haru.sandbox, true);
  assert.doesNotMatch(JSON.stringify(result), /private-owner-path/);
  for (const ids of [{ owner: 7, haru: 7 }, { owner: 7, haru: 99 }]) assert.throws(() => readDesktopSecurity(electron, ids));
});
test('shared, persistent Haru or unexpected session partitions fail closed', () => {
  const { electron, windows, ownerSession } = securityFixture();
  windows[0].webContents.session = ownerSession;
  assert.throws(() => validatePartitionIsolation(readDesktopSecurity(electron, { owner: 7, haru: 3 }).partitionIsolation));
  const good = { distinctSessions: true, ownerExpectedPartition: true, haruExpectedPartition: true, ownerPersistent: true, haruEphemeral: true };
  for (const key of Object.keys(good)) for (const value of [false, undefined, 'true']) assert.throws(() => validatePartitionIsolation({ ...good, [key]: value }));
});
