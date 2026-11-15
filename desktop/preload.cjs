'use strict';
// Sandboxed preload deliberately has no generic invoke/send, filesystem API,
// backend origin, token, or confirmation API exposed to the page.
const { contextBridge, ipcRenderer } = require('electron');
const prefix = 'offerpilot:haru:';
const role = process.argv.includes('--offerpilot-haru') ? 'haru' : 'owner';
const subscribe = (name, listener) => {
  if (typeof listener !== 'function') return () => {};
  const handler = (_event, value) => listener(value);
  ipcRenderer.on(prefix + name, handler);
  return () => ipcRenderer.removeListener(prefix + name, handler);
};
const common = {
  role,
  getState: () => ipcRenderer.invoke(prefix + 'state'),
  onState: listener => subscribe('state', listener),
  windowAction: action => ipcRenderer.invoke(prefix + 'window', action),
};
contextBridge.exposeInMainWorld('offerpilotDesktop', Object.freeze(role === 'owner' ? {
  ...common,
  publish: snapshot => ipcRenderer.send(prefix + 'publish', snapshot),
  disconnect: () => ipcRenderer.send(prefix + 'disconnect'),
  onCommand: listener => subscribe('command', listener),
  onShowAISettings: listener => subscribe('show-ai-settings', listener),
  onShowPilot: listener => subscribe('show-pilot', listener),
  reply: (id, result) => ipcRenderer.send(prefix + 'reply', { id, result }),
} : {
  ...common,
  dragWindow: phase => ipcRenderer.invoke(prefix + 'drag', phase),
  request: request => ipcRenderer.invoke(prefix + 'request', request),
}));

// Owner-only, finite update operations. No arbitrary paths, feeds or IPC.
if (role === 'owner') {
  const updates = 'offerpilot:updates:';
  const listen = (name, listener) => {
    if (typeof listener !== 'function') return () => {};
    const handler = (_event, value) => listener(value);
    ipcRenderer.on(updates + name, handler);
    return () => ipcRenderer.removeListener(updates + name, handler);
  };
  contextBridge.exposeInMainWorld('offerpilotUpdates', Object.freeze({
    getState: () => ipcRenderer.invoke(updates + 'state'),
    check: () => ipcRenderer.invoke(updates + 'check'),
    download: () => ipcRenderer.invoke(updates + 'download'),
    install: () => ipcRenderer.invoke(updates + 'install'),
    onState: listener => listen('state', listener),
    onPrepareInstall: listener => listen('prepare', listener),
    replyPrepareInstall: (id, snapshot) => ipcRenderer.send(updates + 'prepare-reply', { id, snapshot }),
  }));
}

// Locale is a finite preference, never a generic settings/filesystem channel.
// Haru can read and subscribe; only the trusted owner can persist a change.
const localeChannel = 'offerpilot:locale:';
contextBridge.exposeInMainWorld('offerpilotLocale', Object.freeze({
  getLocale: () => ipcRenderer.invoke(localeChannel + 'get'),
  onLocale: listener => {
    if (typeof listener !== 'function') return () => {};
    const handler = (_event, value) => listener(value);
    ipcRenderer.on(localeChannel + 'changed', handler);
    return () => ipcRenderer.removeListener(localeChannel + 'changed', handler);
  },
  ...(role === 'owner' ? { setLocale: value => ipcRenderer.invoke(localeChannel + 'set', value) } : {}),
}));
