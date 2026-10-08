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
  reply: (id, result) => ipcRenderer.send(prefix + 'reply', { id, result }),
} : {
  ...common,
  request: request => ipcRenderer.invoke(prefix + 'request', request),
}));
