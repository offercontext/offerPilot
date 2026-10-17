import assert from 'node:assert/strict';

export function desktopRole(url, origin) {
  let value;
  try { value = new URL(url); } catch { return null; }
  if (value.origin !== origin || value.pathname !== '/' || value.username || value.password) return null;
  const surface = value.searchParams.getAll('desktopSurface');
  if (!surface.length) return 'owner';
  return surface.length === 1 && surface[0] === 'haru' && value.search === '?desktopSurface=haru' ? 'haru' : null;
}
export function selectDesktopSurfaces(candidates, origin) {
  assert.equal(candidates.length, 2, 'exactly two installed desktop windows required');
  const result = {};
  for (const candidate of candidates) {
    const role = desktopRole(candidate.url, origin);
    assert.ok(role, 'unexpected installed surface');
    assert.equal(candidate.role, role, 'preload role must match the actual surface URL');
    assert.equal(result[role], undefined, 'duplicate installed desktop surface');
    result[role] = candidate;
  }
  assert.ok(result.owner && result.haru, 'owner and Haru windows required');
  return result;
}
export async function waitForDesktopSurfaces(app, origin, timeout = 90000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const pages = app.windows();
    if (pages.length > 2) throw new Error('unexpected extra desktop window');
    if (pages.length === 2 && pages.every(page => desktopRole(page.url(), origin))) {
      const candidates = await Promise.all(pages.map(async page => ({ page, url: page.url(),
        role: await page.evaluate(() => window.offerpilotDesktop?.role) })));
      // A URL alone is never identity. Missing/mismatched preload roles fail.
      return selectDesktopSurfaces(candidates, origin);
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('desktop surfaces did not become available');
}
// Serialized into the installed Electron main process. Do not import test hooks.
export function readDesktopSecurity({ app, BrowserWindow, session }, ids) {
  const windows = BrowserWindow.getAllWindows();
  if (windows.length !== 2 || new Set([ids.owner, ids.haru]).size !== 2) throw new Error('two identified windows required');
  const owner = BrowserWindow.fromId(ids.owner);
  const haru = BrowserWindow.fromId(ids.haru);
  if (!owner || !haru) throw new Error('identified desktop window missing');
  const security = win => {
    const contents = win.webContents;
    const prefs = contents.getLastWebPreferences();
    return { packaged: app.isPackaged, nodeIntegration: prefs.nodeIntegration,
      contextIsolation: prefs.contextIsolation, sandbox: prefs.sandbox, webSecurity: prefs.webSecurity,
      devTools: prefs.devTools, devToolsOpened: contents.isDevToolsOpened(),
      unsafeSwitches: ['no-sandbox', 'disable-web-security', 'disable-site-isolation-trials',
        'allow-running-insecure-content', 'ignore-certificate-errors'].filter(name => app.commandLine.hasSwitch(name)) };
  };
  return { owner: security(owner), haru: security(haru), partitionIsolation: {
    distinctSessions: owner.webContents.session !== haru.webContents.session,
    ownerExpectedPartition: owner.webContents.session === session.fromPartition('persist:offerpilot-desktop'),
    haruExpectedPartition: haru.webContents.session === session.fromPartition('offerpilot-haru'),
    ownerPersistent: Boolean(owner.webContents.session.getStoragePath()),
    haruEphemeral: haru.webContents.session.getStoragePath() === null,
  } };
}
export function validatePartitionIsolation(value) {
  assert.deepEqual(value, { distinctSessions: true, ownerExpectedPartition: true, haruExpectedPartition: true,
    ownerPersistent: true, haruEphemeral: true }, 'Haru must use its separate ephemeral session');
}
