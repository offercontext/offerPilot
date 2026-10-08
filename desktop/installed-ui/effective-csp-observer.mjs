import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { extractFile } from '@electron/asar';

import { AUDITED_DESKTOP_PRODUCT, AUDITED_DESKTOP_MODULE_SHA256, DESKTOP_SOURCE_FILES } from './desktop-source-manifest.mjs';

// All twelve reviewed local modules leave onResponseStarted unoccupied. Verify
// installed bytes before observing that event, without replacing blocking hooks.
export const AUDITED_RESPONSE_MODULES = AUDITED_DESKTOP_MODULE_SHA256;

export async function auditInstalledResponseObserver(installDir) {
  assert.ok(typeof installDir === 'string' && path.isAbsolute(installDir), 'absolute installation required for response observer audit');
  const resources = await fs.realpath(path.join(installDir, 'resources'));
  const archive = path.join(resources, 'app.asar');
  const stat = await fs.lstat(archive);
  assert.ok(stat.isFile() && !stat.isSymbolicLink(), 'installed application archive must not be a link');
  assert.equal(await fs.realpath(archive), archive, 'application archive escaped audited resources');
  const metadata = JSON.parse(extractFile(archive, 'package.json').toString('utf8'));
  assert.equal(metadata.name, 'offerpilot-desktop');
  assert.equal(metadata.main, 'main.cjs');
  assert.equal(metadata.version, '0.1.0-desktop.1');
  for (const [name, expected] of Object.entries(AUDITED_RESPONSE_MODULES)) {
    const source = extractFile(archive, name).toString('utf8').replace(/\r\n/g, '\n');
    assert.equal(createHash('sha256').update(source).digest('hex'), expected, 'installed response policy module differs from audited product');
    assert.equal(source.includes('onResponseStarted'), false, 'response observer event is already occupied by product source');
  }
  return { auditedProductCommit: AUDITED_DESKTOP_PRODUCT,
    verifiedModuleCount: DESKTOP_SOURCE_FILES.length, mainEntryMatched: true, responseStartedUnused: true };
}

// Serialized into Electron. onResponseStarted is a SimpleEvent: it runs after
// onHeadersReceived overrides have been installed and before the same response
// is sent to the renderer (Electron v44.5.1 proxying_url_loader_factory.cc:612–655).
// This callback never edits details, headers, policy, permissions, or requests.
export function nativeEffectiveCspObserver({ session }, args) {
  const slot = Symbol.for('offerpilot.installed-effective-csp-observer');
  const fail = (condition, message) => { if (!condition) throw new Error(message); };
  if (args.operation === 'install') {
    fail(!globalThis[slot], 'effective CSP observer already installed');
    fail(args.sourceAuditPassed === true, 'installed response policy audit required');
    fail(typeof args.key === 'string' && /^[a-f0-9-]{36}$/.test(args.key), 'effective CSP observer identity required');
    const owner = args.owner;
    const contents = owner.webContents;
    const desktopSession = session.fromPartition('persist:offerpilot-desktop');
    const origin = new URL(args.ownerURL);
    const module = new URL(args.moduleURL);
    fail(!owner.isDestroyed() && !contents.isDestroyed() && contents.getURL() === args.ownerURL
      && contents.session === desktopSession, 'effective CSP owner/session mismatch');
    fail(origin.protocol === 'http:' && origin.hostname === '127.0.0.1' && origin.port
      && !origin.username && !origin.password && origin.pathname === '/'
      && !origin.searchParams.has('desktopSurface'), 'effective CSP owner URL invalid');
    fail(module.origin === origin.origin && !module.search && !module.hash && !module.username && !module.password
      && /^\/assets\/ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.mjs$/.test(module.pathname),
    'effective CSP module URL invalid');
    fail(typeof args.policy === 'string' && args.policy.length > 0, 'expected production CSP required');
    const blank = () => ({ count: 0, ownerIdMatched: null, ownerObjectMatched: null, ownerSessionMatched: null,
      methodMatched: null, resourceTypeMatched: null, statusMatched: null, requestIdValid: null,
      singleCspHeader: null, cspMatched: null });
    const state = { key: args.key, contents, desktopSession, webRequest: desktopSession.webRequest,
      ownerURL: args.ownerURL, moduleURL: args.moduleURL, policy: args.policy, phase: 'inactive',
      records: { beforeProbe: blank(), module: blank(), releaseProbe: blank() },
      failures: new Set(), requestIds: new Set(), registered: false };
    state.listener = details => {
      try {
        // Ignore unrelated local app responses before inspecting their headers.
        const document = details.url === state.ownerURL;
        const script = details.url === state.moduleURL;
        if (!document && !script) return;
        const target = document && ['beforeProbe', 'releaseProbe'].includes(state.phase) ? state.phase
          : script && state.phase === 'runtime' ? 'module' : null;
        if (!target) { state.failures.add('unexpected-response-phase'); return; }
        const record = state.records[target];
        record.count = Math.min(record.count + 1, 3);
        if (record.count !== 1) state.failures.add('duplicate-target-response');
        const headers = details.responseHeaders;
        const csp = headers && typeof headers === 'object'
          ? Object.entries(headers).filter(([name]) => name.toLowerCase() === 'content-security-policy') : [];
        const singleCspHeader = csp.length === 1 && Array.isArray(csp[0][1]) && csp[0][1].length === 1;
        const observed = {
          ownerIdMatched: details.webContentsId === contents.id,
          // webContents is optional in Electron. When provided it must be the
          // exact native object; required webContentsId always binds the owner.
          ownerObjectMatched: details.webContents === undefined || details.webContents === contents,
          ownerSessionMatched: contents.session === desktopSession,
          methodMatched: details.method === 'GET',
          resourceTypeMatched: details.resourceType === (document ? 'mainFrame' : 'script'),
          statusMatched: details.statusCode === 200,
          requestIdValid: Number.isSafeInteger(details.id) && details.id >= 0 && !state.requestIds.has(details.id),
          singleCspHeader,
          cspMatched: singleCspHeader && csp[0][1][0] === state.policy,
        };
        if (state.requestIds.size < 8) state.requestIds.add(details.id);
        else state.failures.add('request-evidence-limit');
        for (const [name, value] of Object.entries(observed)) {
          record[name] = record.count === 1 ? value : record[name] === true && value;
          if (!value) state.failures.add('invalid-effective-response');
        }
      } catch { state.failures.add('native-response-observation-error'); }
    };
    globalThis[slot] = state; // Cleanup can recover a partially registered observer.
    state.webRequest.onResponseStarted({ urls: [`${origin.origin}/*`] }, state.listener);
    state.registered = true;
    return { installed: true, productionBlockingHandlersChanged: false };
  }
  const state = globalThis[slot];
  if (!state) {
    if (args.operation === 'restore') return { restored: true, absent: true };
    throw new Error('effective CSP observer missing');
  }
  fail(args.key === state.key, 'effective CSP observer identity mismatch');
  const snapshot = () => ({ registered: state.registered, phase: state.phase,
    ownerSessionMatched: state.contents.session === state.desktopSession,
    failures: [...state.failures], records: Object.fromEntries(Object.entries(state.records).map(([name, value]) => [name, { ...value }])) });
  if (args.operation === 'arm') {
    const valid = args.phase === 'beforeProbe' && state.phase === 'inactive'
      || args.phase === 'runtime' && state.phase === 'beforeProbe'
      || args.phase === 'releaseProbe' && ['beforeProbe', 'runtime'].includes(state.phase);
    fail(valid && state.registered, 'effective CSP phase transition rejected');
    state.phase = args.phase;
    return { armed: true };
  }
  if (args.operation === 'snapshot') return snapshot();
  if (args.operation === 'restore') {
    // Only this previously unused simple event is removed. Never touch the
    // production onHeadersReceived, auth, permission, or navigation listeners.
    state.webRequest.onResponseStarted(null);
    state.registered = false;
    const final = snapshot(); // Atomic close + final observation, no await gap.
    delete globalThis[slot];
    return { restored: true, absent: false, snapshot: final };
  }
  throw new Error('unknown effective CSP observer operation');
}

export function assertEffectiveCspObservation(snapshot, expected = {}) {
  assert.equal(snapshot?.ownerSessionMatched, true, 'native CSP owner session changed');
  assert.deepEqual(snapshot.failures, [], 'native effective CSP observation failed');
  for (const [name, count] of Object.entries(expected)) {
    assert.ok(['beforeProbe', 'module', 'releaseProbe'].includes(name));
    const record = snapshot.records?.[name];
    assert.equal(record?.count, count, `native effective CSP ${name} observation count`);
    if (count === 0) continue;
    for (const field of ['ownerIdMatched', 'ownerObjectMatched', 'ownerSessionMatched', 'methodMatched',
      'resourceTypeMatched', 'statusMatched', 'requestIdValid', 'singleCspHeader', 'cspMatched']) {
      assert.equal(record[field], true, `native effective CSP ${name} ${field}`);
    }
  }
}
