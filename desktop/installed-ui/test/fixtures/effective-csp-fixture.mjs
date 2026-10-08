import fs from 'node:fs/promises';
import path from 'node:path';
import { createPackage } from '@electron/asar';
import { randomUUID } from 'node:crypto';
import { AUDITED_RESPONSE_MODULES, nativeEffectiveCspObserver } from '../../effective-csp-observer.mjs';

// Synthetic archive contains unexecuted checked-in product source only.
export async function auditedArchive(installDir, overrides = {}) {
  const source = path.join(installDir, 'synthetic-app');
  await fs.mkdir(source, { recursive: true });
  for (const name of Object.keys(AUDITED_RESPONSE_MODULES)) {
    await fs.writeFile(path.join(source, name), overrides[name] ?? await fs.readFile(new URL(`../../../${name}`, import.meta.url)));
  }
  await fs.writeFile(path.join(source, 'package.json'), JSON.stringify({ name: 'offerpilot-desktop', main: 'main.cjs',
    version: '0.1.0-desktop.1', ...overrides.metadata }));
  const resources = path.join(installDir, 'resources');
  await fs.mkdir(resources, { recursive: true });
  await createPackage(source, path.join(resources, 'app.asar'));
}

export function nativeObserverFixture(t, { ownerURL, moduleURL, policy, ...options }) {
  let listener;
  let sequence = 0;
  let restoreError = options.restoreError;
  const registrations = [];
  const desktopSession = { webRequest: new Proxy({ onResponseStarted(filter, callback) {
    if (filter === null) {
      options.beforeRestore?.(emit);
      if (restoreError) throw restoreError;
      listener = undefined;
    } else {
      registrations.push(filter); listener = callback;
      if (options.installError) throw options.installError;
    }
  } }, { get(target, key) {
    if (key !== 'onResponseStarted') throw new Error('production handler must remain untouched');
    return target[key];
  } }) };
  const contents = { id: 42, isDestroyed: () => false, getURL: () => ownerURL, session: desktopSession };
  const owner = { webContents: contents, isDestroyed: () => false };
  const session = { fromPartition: partition => {
    if (partition !== 'persist:offerpilot-desktop') throw new Error('wrong owner partition');
    return desktopSession;
  } };
  const key = randomUUID();
  const call = (operation, extra = {}) => nativeEffectiveCspObserver({ session },
    { key, operation, owner, ownerURL, moduleURL, policy, sourceAuditPassed: true, ...extra });
  const emit = (kind = 'document', overrides = {}) => {
    const details = { id: ++sequence, url: kind === 'document' ? ownerURL : moduleURL,
      method: 'GET', resourceType: kind === 'document' ? 'mainFrame' : 'script', statusCode: 200,
      webContentsId: contents.id, webContents: contents, responseHeaders: { 'Content-Security-Policy': [policy] }, ...overrides };
    listener?.(details);
    return details;
  };
  t.after(() => {
    restoreError = null;
    const slot = Symbol.for('offerpilot.installed-effective-csp-observer');
    const state = globalThis[slot];
    if (state) nativeEffectiveCspObserver({ session }, { operation: 'restore', key: state.key });
  });
  return { owner, contents, session, desktopSession, registrations, call, emit,
    evaluate: (args) => nativeEffectiveCspObserver({ session }, args),
    registered: () => Boolean(listener) };
}
