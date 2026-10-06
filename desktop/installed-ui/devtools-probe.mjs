// This function is also serialized into Electron's main process by app.evaluate.
// It must remain standalone and use no imported helpers or test-only app hooks.
export async function observeDevToolsDisabled({ BrowserWindow }) {
  const windows = BrowserWindow.getAllWindows();
  if (windows.length !== 1) throw new Error('one application window required');
  const contents = windows[0].webContents;
  const beforeOpen = contents.isDevToolsOpened();
  const beforeContents = Boolean(contents.devToolsWebContents);
  let openedEvent = false;
  const onOpened = () => { openedEvent = true; };
  contents.on('devtools-opened', onOpened);
  try {
    // Never turn an already open DevTools window into passing evidence.
    if (!beforeOpen && !beforeContents) contents.openDevTools({ mode: 'detach', activate: false });
    await new Promise((resolve) => setTimeout(resolve, 1000));
    return { beforeOpen, beforeContents, openedEvent,
      afterOpen: contents.isDevToolsOpened(), afterContents: Boolean(contents.devToolsWebContents) };
  } finally {
    contents.removeListener('devtools-opened', onOpened);
  }
}
