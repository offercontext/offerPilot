// Serialized into the installed Electron main process by Playwright.
// Observation only: append after production handlers, never change a download.
export function installDownloadObservation({ BrowserWindow, app }, { ownerURL, filename }) {
  const fs = process.getBuiltinModule('fs');
  const owners = BrowserWindow.getAllWindows().filter(win => win.webContents.getURL() === ownerURL);
  if (owners.length !== 1) throw new Error('Download observer owner is not unique');
  const contents = owners[0].webContents, session = contents.session;
  const prior = session.listeners('will-download');
  if (!prior.length) throw new Error('Production download listener missing');
  const health = { ownerId: contents.id, priorListeners: prior.length, records: 0, writeErrors: [] };
  app.__resumeExportObservation = health;
  const emit = record => {
    try {
      if (health.records++ >= 1000) return;
      fs.appendFileSync(filename, JSON.stringify({ at: new Date().toISOString(), ...record }) + '\n');
    } catch (error) { if (health.writeErrors.length < 10) health.writeErrors.push(String(error)); }
  };
  let sequence = 0;
  session.on('will-download', (event, item, source) => {
    try {
      if (source !== contents) return;
      const id = ++sequence;
      const read = () => {
        const result = {};
        for (const method of ['getSavePath', 'getState', 'getFilename', 'getReceivedBytes', 'getTotalBytes', 'getMimeType']) {
          try { result[method] = item[method](); } catch (error) { result[method] = { error: String(error) }; }
        }
        return result;
      };
      // Attach before doing disk I/O. Production's synchronous handler has
      // returned, but EventEmitter dispatch has not yet finished.
      try { item.on('updated', (_event, state) => { try { emit({ type: 'updated', id, state, ...read() }); } catch {} }); } catch (error) { emit({ type: 'attach-error', id, error: String(error) }); }
      try { item.once('done', (_event, state) => { try { emit({ type: 'done', id, state, ...read() }); } catch {} }); } catch (error) { emit({ type: 'attach-error', id, error: String(error) }); }
      emit({ type: 'will-download-after-production', id, defaultPrevented: event.defaultPrevented, ...read() });
    } catch (error) { emit({ type: 'observer-error', error: String(error) }); }
  });
  emit({ type: 'installed', ...health });
  return { ownerId: contents.id, priorListeners: prior.length };
}
