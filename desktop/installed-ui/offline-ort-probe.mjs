import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { auditInstalledResponseObserver, nativeEffectiveCspObserver, assertEffectiveCspObservation } from './effective-csp-observer.mjs';

const { contentSecurityPolicy } = createRequire(import.meta.url)('../capabilities.cjs');
const ASSET_NAMES = Object.freeze({
  mjs: /^ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.mjs$/,
  wasm: /^ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.wasm$/,
});
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const comparablePath = (value) => {
  const resolved = path.resolve(value);
  return process.platform === 'win32' ? resolved.replaceAll('\\', '/').toLowerCase() : resolved;
};

async function assertUnlinkedDirectoryChain(value, filesystem, message) {
  let directory = value;
  while (true) {
    // lstat('link/') follows a directory link. Remove only trailing separators
    // (not lexical '.'/'..' components), including at each ancestor iteration.
    const root = path.parse(directory).root;
    const separator = process.platform === 'win32' ? /[/\\]$/ : /\/$/;
    while (directory.length > root.length && separator.test(directory)) directory = directory.slice(0, -1);
    const stat = await filesystem.lstat(directory);
    assert.ok(stat.isDirectory() && !stat.isSymbolicLink(), message);
    const parent = path.dirname(directory);
    if (parent === directory) return;
    directory = parent;
  }
}

// String normalization cannot identify Windows 8.3 aliases. Resolve BOTH real
// filesystem locations, then bind their actual directory identities as well.
// A symbolic link/junction anywhere in either directory chain is not an
// acceptable substitute for the selected installed directory.
export async function assertInstalledResourcesIdentity(actualPath, expectedPath, filesystem = fs, onCheck = async () => {}) {
  // Only these fixed check names and booleans can leave this filesystem check.
  // Persist a failed check before rethrowing; never serialize paths or errors.
  const checked = async (name, operation) => {
    let result;
    try { result = await operation(); }
    catch (error) { await onCheck(name, false); throw error; }
    await onCheck(name, true);
    return result;
  };
  await checked('resourcesPathsAbsolute', () => {
    for (const value of [actualPath, expectedPath]) {
      assert.ok(typeof value === 'string' && path.isAbsolute(value), 'absolute installed resources path required');
    }
  });
  await checked('resourcesDirectoryChainsUnlinked', async () => {
    for (const value of [actualPath, expectedPath]) {
      await assertUnlinkedDirectoryChain(value, filesystem, 'installed resources identity must not use links');
    }
  });
  const [actual, expected] = await checked('resourcesCanonicalPathMatched', async () => {
    const resolved = await Promise.all([filesystem.realpath(actualPath), filesystem.realpath(expectedPath)]);
    assert.equal(comparablePath(resolved[0]), comparablePath(resolved[1]), 'running app must use selected installation');
    return resolved;
  });
  const [actualStat, expectedStat] = await checked('resourcesDirectories', async () => {
    const stats = await Promise.all([
      filesystem.stat(actual, { bigint: true }), filesystem.stat(expected, { bigint: true }),
    ]);
    assert.ok(stats.every(stat => stat.isDirectory()), 'installed resources identity must be a directory');
    return stats;
  });
  await checked('resourcesDeviceMatched', () => assert.equal(actualStat.dev, expectedStat.dev, 'installed resources device identity mismatch'));
  await checked('resourcesFileMatched', () => assert.equal(actualStat.ino, expectedStat.ino, 'installed resources file identity mismatch'));
  return true;
}

// Only inspect the installed executable assets. No recursive search, developer
// build fallback, user profile, token, model, or owner data is read.
export async function discoverInstalledOfflineOrt(installDir, filesystem = fs) {
  assert.ok(typeof installDir === 'string' && path.isAbsolute(installDir), 'absolute installed directory required');
  await assertUnlinkedDirectoryChain(installDir, filesystem, 'installed asset directories must not be links');
  const root = await filesystem.realpath(installDir);
  let directory = root;
  for (const segment of ['resources', 'web', 'assets']) {
    directory = path.join(directory, segment);
    const stat = await filesystem.lstat(directory);
    assert.ok(stat.isDirectory() && !stat.isSymbolicLink(), 'installed asset directories must not be links');
    assert.equal(comparablePath(await filesystem.realpath(directory)), comparablePath(directory), 'asset directory escaped installation');
  }
  const entries = await filesystem.readdir(directory, { withFileTypes: true });
  const assets = {};
  for (const [kind, pattern] of Object.entries(ASSET_NAMES)) {
    const candidates = entries.filter((entry) => pattern.test(entry.name));
    assert.equal(candidates.length, 1, `exactly one installed asyncify ${kind} asset required`);
    const entry = candidates[0];
    assert.ok(entry.isFile() && !entry.isSymbolicLink(), 'installed ORT asset must be a regular file');
    const filename = path.join(directory, entry.name);
    const stat = await filesystem.lstat(filename);
    assert.ok(stat.isFile() && !stat.isSymbolicLink(), 'installed ORT asset must not be a link');
    assert.equal(comparablePath(await filesystem.realpath(filename)), comparablePath(filename), 'ORT asset escaped installation');
    assert.ok(stat.size >= 8 && stat.size <= (kind === 'mjs' ? 1024 * 1024 : 256 * 1024 * 1024), 'unexpected installed ORT asset size');
    const bytes = await filesystem.readFile(filename);
    assert.equal(bytes.length, stat.size, 'installed ORT asset changed during read');
    assets[kind] = { name: entry.name, bytes: bytes.length, sha256: sha256(bytes) };
  }
  return { resourcesPath: path.join(root, 'resources'), assets };
}

export function installedOfflineOrtURLs(ownerURL, assets) {
  const owner = new URL(ownerURL);
  assert.ok(owner.protocol === 'http:' && owner.hostname === '127.0.0.1' && owner.port
    && !owner.username && !owner.password && owner.pathname === '/'
    && owner.searchParams.get('desktopSurface') !== 'haru', 'installed loopback owner URL required');
  const urls = {};
  for (const kind of ['mjs', 'wasm']) {
    assert.ok(ASSET_NAMES[kind].test(assets[kind]?.name), `invalid installed ${kind} asset name`);
    urls[kind] = new URL(`/assets/${assets[kind].name}`, owner.origin).href;
  }
  return urls;
}

// Serialized into the real Electron main process; no policy is replaced.
export function readOfflineOrtOwner({ app, BrowserWindow, session }, { owner, ownerURL }) {
  const contents = owner.webContents;
  const prefs = contents.getLastWebPreferences();
  const windows = BrowserWindow.getAllWindows();
  const alive = win => !win.isDestroyed() && !win.webContents.isDestroyed();
  const matchingOwners = windows.filter(win => alive(win) && win.webContents.getURL() === ownerURL);
  const others = windows.filter(win => win !== owner);
  const haru = others.length === 1 && alive(others[0]) ? others[0] : null;
  const additionalArguments = prefs.additionalArguments;
  return { packaged: app.isPackaged, resourcesPath: process.resourcesPath,
    ownerURLMatched: alive(owner) && contents.getURL() === ownerURL,
    twoWindows: windows.length === 2,
    ownerWindowUnique: matchingOwners.length === 1 && matchingOwners[0] === owner,
    ownerSessionMatched: contents.session === session.fromPartition('persist:offerpilot-desktop'),
    haruURLMatched: Boolean(haru && haru.webContents.getURL() === new URL('/?desktopSurface=haru', ownerURL).href),
    haruSessionMatched: Boolean(haru && haru.webContents.session === session.fromPartition('offerpilot-haru')),
    distinctSessions: Boolean(haru && contents.session !== haru.webContents.session),
    // v44.5.1 SaveLastPreferences omits additionalArguments (and devTools):
    // https://github.com/electron/electron/blob/v44.5.1/shell/browser/web_contents_preferences.cc#L362-L383
    // Absence is diagnostic only, never fabricated as a present owner marker.
    additionalArgumentsArray: Array.isArray(additionalArguments) ? true
      : additionalArguments === undefined ? undefined : additionalArguments === null ? null : 'invalid-type',
    ownerArgument: Array.isArray(additionalArguments) && additionalArguments.includes('--offerpilot-owner'),
    nodeIntegration: prefs.nodeIntegration, contextIsolation: prefs.contextIsolation,
    sandbox: prefs.sandbox, webSecurity: prefs.webSecurity, devTools: prefs.devTools,
    devToolsOpened: contents.isDevToolsOpened(), devToolsContentsPresent: Boolean(contents.devToolsWebContents) };
}

// Serialized into the already main-process-bound renderer. The production
// preload defaults to owner without a Haru marker, so this is never identity on
// its own: the real window, exact URLs, and both session objects are required.
export function readOfflineOrtPreloadOwner() {
  return window.offerpilotDesktop?.role === 'owner';
}

const OWNER_EXPECTATIONS = Object.freeze({ packaged: true, ownerURLMatched: true,
  twoWindows: true, ownerWindowUnique: true, ownerSessionMatched: true,
  haruURLMatched: true, haruSessionMatched: true, distinctSessions: true,
  contextIsolation: true, sandbox: true, webSecurity: true,
  nodeIntegration: false, devToolsOpened: false, devToolsContentsPresent: false });
const RESOURCE_CHECKS = Object.freeze(['resourcesPathsAbsolute', 'resourcesDirectoryChainsUnlinked',
  'resourcesCanonicalPathMatched', 'resourcesDirectories', 'resourcesDeviceMatched', 'resourcesFileMatched']);
const diagnosticBoolean = value => typeof value === 'boolean' ? value
  : value === undefined ? 'undefined' : value === null ? 'null' : 'invalid-type';

// Standalone for Playwright serialization. Optional dependencies are solely for
// unit boundary/failure tests; the installed caller passes only the first arg.
// Runtime code is imported from its original same-origin URL, never eval/blob.
export async function initializeOfflineOrtInRenderer(args, dependencies = {}) {
  const fetchAsset = dependencies.fetch ?? globalThis.fetch.bind(globalThis);
  const importModule = dependencies.importModule ?? ((url) => import(url));
  const webAssembly = dependencies.webAssembly ?? globalThis.WebAssembly;
  const crypto = dependencies.crypto ?? globalThis.crypto;
  const location = dependencies.location ?? globalThis.location;
  const document = dependencies.document ?? globalThis.document;
  let phase = 'owner-validation';
  let timedOut = false;
  let checkRejected = false;
  const observed = Object.fromEntries(['ownerMatched', 'assetURLsMatched', 'wasmResponseMatched', 'wasmCspMatched',
    'wasmByteCountMatched', 'wasmSha256Matched', 'wasmCompiled', 'moduleImported', 'factoryExportMatched',
    'factoryResolved', 'factoryCalledRun', 'factorySingleThread', 'heapPresent', 'exportsPresent',
    'asyncInitCompleted', 'ortInitReturnedZero', 'ownerUnchanged', 'cspViolationObserved'].map(key => [key, null]));
  observed.sharedArrayBufferAvailable = typeof globalThis.SharedArrayBuffer === 'function';
  observed.crossOriginIsolated = typeof globalThis.crossOriginIsolated === 'boolean' ? globalThis.crossOriginIsolated : null;
  const ensure = (condition, message) => { if (!condition) { checkRejected = true; throw new Error(message); } };
  const snapshot = (outcome, errorCategory = null) => ({ schemaVersion: 1, phase, outcome, errorCategory, observed: { ...observed } });
  // Classify only standard error names, never return message, stack, or an ORT
  // object. A raw exception must not cross Playwright when diagnostics are on.
  const category = error => {
    if (timedOut) return 'timeout';
    if (checkRejected) return 'check-rejected';
    const names = { TypeError: 'type-error', CompileError: 'wasm-compile-error', LinkError: 'wasm-link-error',
      RuntimeError: 'wasm-runtime-error', EvalError: 'eval-error', RangeError: 'range-error',
      AbortError: 'aborted', SecurityError: 'security-error', Error: 'error' };
    try { const name = error?.name; return Object.hasOwn(names, name) ? names[name] : 'other'; } catch { return 'other'; }
  };
  const names = {
    mjs: /^\/assets\/ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.mjs$/,
    wasm: /^\/assets\/ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.wasm$/,
  };
  let violations = 0;
  const violation = () => { violations++; };
  document.addEventListener('securitypolicyviolation', violation);
  const controller = new AbortController();
  let timer;
  try {
    const owner = new URL(args.ownerURL);
    observed.ownerMatched = Boolean(location.href === args.ownerURL && owner.protocol === 'http:' && owner.hostname === '127.0.0.1'
      && owner.port && owner.pathname === '/' && !owner.username && !owner.password
      && owner.searchParams.get('desktopSurface') !== 'haru');
    ensure(observed.ownerMatched, 'ORT renderer owner mismatch');
    phase = 'asset-url-validation';
    observed.assetURLsMatched = true;
    for (const kind of ['mjs', 'wasm']) {
      const url = new URL(args.urls[kind]);
      observed.assetURLsMatched = url.origin === owner.origin && url.protocol === 'http:' && !url.username && !url.password
        && !url.search && !url.hash && names[kind].test(url.pathname);
      ensure(observed.assetURLsMatched, 'ORT asset must be an exact installed self URL');
    }
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => { timedOut = true; controller.abort(); reject(new Error('installed ORT initialization timed out')); }, args.timeoutMs);
    });
    const result = await Promise.race([deadline, (async () => {
      // Authentication is supplied by the existing Electron session handler.
      // Never inspect or copy its token and never follow a redirect.
      phase = 'wasm-fetch';
      const response = await fetchAsset(args.urls.wasm, { credentials: 'same-origin', mode: 'same-origin',
        redirect: 'error', cache: 'no-store', signal: controller.signal });
      phase = 'wasm-response';
      observed.wasmResponseMatched = response.ok === true && response.url === args.urls.wasm && !response.redirected;
      ensure(observed.wasmResponseMatched, 'installed WASM response mismatch');
      phase = 'wasm-csp';
      observed.wasmCspMatched = response.headers.get('content-security-policy') === args.csp;
      ensure(observed.wasmCspMatched, 'installed WASM production CSP mismatch');
      phase = 'wasm-read';
      const bytes = new Uint8Array(await response.arrayBuffer());
      phase = 'wasm-bytes';
      observed.wasmByteCountMatched = bytes.byteLength === args.assets.wasm.bytes;
      ensure(observed.wasmByteCountMatched, 'installed WASM byte count mismatch');
      phase = 'wasm-sha256';
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
        (value) => value.toString(16).padStart(2, '0')).join('');
      observed.wasmSha256Matched = digest === args.assets.wasm.sha256;
      ensure(observed.wasmSha256Matched, 'installed WASM digest mismatch');
      phase = 'wasm-compile';
      const compiled = await webAssembly.compile(bytes);
      observed.wasmCompiled = compiled instanceof webAssembly.Module;
      ensure(observed.wasmCompiled, 'real WebAssembly compilation required');
      phase = 'module-import';
      const imported = await importModule(args.urls.mjs);
      observed.moduleImported = true;
      phase = 'factory-export';
      observed.factoryExportMatched = typeof imported.default === 'function';
      ensure(observed.factoryExportMatched, 'installed ORT factory missing');
      // The locked factory creates numThreads - 1 workers. wasmBinary prevents
      // another fetch. locateFile refuses every asset except this exact WASM.
      phase = 'factory-invoke';
      const ort = await imported.default({ numThreads: 1, wasmBinary: bytes, locateFile: (name) => {
        ensure(name === 'ort-wasm-simd-threaded.asyncify.wasm', 'unexpected ORT runtime asset requested');
        return args.urls.wasm;
      } });
      observed.factoryResolved = true;
      phase = 'factory-ready';
      observed.factoryCalledRun = ort?.calledRun === true;
      observed.factorySingleThread = ort?.numThreads === 1;
      ensure(observed.factoryCalledRun && observed.factorySingleThread, 'ORT factory did not initialize single-threaded');
      phase = 'heap-check';
      observed.heapPresent = ort.HEAPU8 instanceof Uint8Array && ort.HEAPU8.byteLength >= 65536;
      ensure(observed.heapPresent, 'initialized ORT WASM heap missing');
      phase = 'exports-check';
      observed.exportsPresent = typeof ort.asyncInit === 'function' && typeof ort._OrtInit === 'function'
        && typeof ort._OrtCreateSession === 'function';
      ensure(observed.exportsPresent, 'initialized ORT exports missing');
      phase = 'async-init';
      ort.asyncInit();
      observed.asyncInitCompleted = true;
      phase = 'ort-init';
      // Matches locked onnxruntime-web/lib/wasm/wasm-core-impl.ts initOrt:
      // one intra-op thread, warning logging, zero means environment ready.
      const initCode = ort._OrtInit(1, 2);
      observed.ortInitReturnedZero = initCode === 0;
      ensure(observed.ortInitReturnedZero, 'ORT environment initialization failed');
      phase = 'owner-stable';
      observed.ownerUnchanged = location.href === args.ownerURL;
      ensure(observed.ownerUnchanged, 'ORT owner navigated during initialization');
      phase = 'csp-final';
      observed.cspViolationObserved = violations !== 0;
      ensure(violations === 0, 'ORT initialization violated production CSP');
      return { wasmCompiled: true, factoryInitialized: ort.calledRun, numThreads: ort.numThreads,
        heapBytes: ort.HEAPU8.byteLength, ortInitCode: initCode, wasmSha256: digest, cspViolations: violations };
    })()]);
    phase = 'complete';
    return args.captureDiagnostic === true ? { ...result, rendererDiagnostic: snapshot('passed') } : result;
  } catch (error) {
    observed.cspViolationObserved = violations !== 0;
    if (args.captureDiagnostic === true) return { rendererFailure: true, rendererDiagnostic: snapshot('failed', category(error)) };
    throw error;
  } finally {
    clearTimeout(timer);
    controller.abort();
    document.removeEventListener('securitypolicyviolation', violation);
  }
}

export function safeOfflineOrtRendererDiagnostic(value) {
  const phases = ['owner-validation', 'asset-url-validation', 'wasm-fetch', 'wasm-response', 'wasm-csp', 'wasm-read',
    'wasm-bytes', 'wasm-sha256', 'wasm-compile', 'module-import', 'factory-export', 'factory-invoke', 'factory-ready',
    'heap-check', 'exports-check', 'async-init', 'ort-init', 'owner-stable', 'csp-final', 'complete'];
  const categories = [null, 'timeout', 'check-rejected', 'type-error', 'wasm-compile-error', 'wasm-link-error',
    'wasm-runtime-error', 'eval-error', 'range-error', 'aborted', 'security-error', 'error', 'other'];
  const fields = ['ownerMatched', 'assetURLsMatched', 'wasmResponseMatched', 'wasmCspMatched', 'wasmByteCountMatched',
    'wasmSha256Matched', 'wasmCompiled', 'moduleImported', 'factoryExportMatched', 'factoryResolved', 'factoryCalledRun',
    'factorySingleThread', 'heapPresent', 'exportsPresent', 'asyncInitCompleted', 'ortInitReturnedZero', 'ownerUnchanged',
    'cspViolationObserved', 'sharedArrayBufferAvailable', 'crossOriginIsolated'];
  if (!value || value.schemaVersion !== 1 || !phases.includes(value.phase) || !categories.includes(value.errorCategory)
    || !['passed', 'failed'].includes(value.outcome) || !value.observed
    || fields.some(key => ![null, true, false].includes(value.observed[key]))) return null;
  if (value.outcome === 'passed' && (value.phase !== 'complete' || value.errorCategory !== null
    || value.observed.cspViolationObserved !== false
    || fields.filter(key => !['cspViolationObserved', 'sharedArrayBufferAvailable', 'crossOriginIsolated'].includes(key))
      .some(key => value.observed[key] !== true))) return null;
  if (value.outcome === 'failed' && (value.phase === 'complete' || value.errorCategory === null)) return null;
  return { schemaVersion: 1, phase: value.phase, outcome: value.outcome, errorCategory: value.errorCategory,
    observed: Object.fromEntries(fields.map(key => [key, value.observed[key]])) };
}

export function assertOfflineOrtInitialization(result, assets) {
  assert.equal(result?.wasmCompiled, true, 'real WASM compilation must succeed');
  assert.equal(result.factoryInitialized, true, 'real ORT factory must initialize');
  assert.equal(result.numThreads, 1, 'ORT must initialize single-threaded');
  assert.ok(Number.isSafeInteger(result.heapBytes) && result.heapBytes >= 65536, 'ORT heap evidence required');
  assert.equal(result.ortInitCode, 0, 'ORT init must return success');
  assert.equal(result.wasmSha256, assets.wasm.sha256, 'renderer WASM must match installed bytes');
  assert.equal(result.cspViolations, 0, 'production CSP violations cannot pass');
}

// Match private exception text only in memory; diagnostics contain fixed enums.
// In particular, the outer recorder normalizes AggregateError to Error, so keep
// the primary failure separately from the later cleanup failures here.
function diagnosticErrorCategory(error) {
  if (error?.name === 'AssertionError' || error?.code === 'ERR_ASSERTION') return 'assertion';
  if (error?.name === 'TimeoutError') return 'timeout';
  if (error?.name === 'TypeError') return 'type-error';
  const message = typeof error?.message === 'string' ? error.message : '';
  if (/\b(?:timed out|timeout)\b/i.test(message)) return 'timeout';
  if (/\b(?:ERR_ABORTED|aborted)\b/i.test(message)) return 'aborted';
  if (/\b(?:ECONNREFUSED|ERR_CONNECTION_REFUSED)\b/i.test(message)) return 'connection-refused';
  if (/\b(?:ECONNRESET|ERR_CONNECTION_RESET)\b/i.test(message)) return 'connection-reset';
  if (/target.*closed|page.*closed|browser.*closed|context.*closed/i.test(message)) return 'target-closed';
  if (/\bprotocol error\b/i.test(message)) return 'protocol-error';
  return 'other';
}

function reloadDiagnostic() {
  return { check: 'not-started', result: 'not-run', errorCategory: null, rawHeaderError: null,
    observed: Object.fromEntries(['safeReloadGatePassed', 'reloadResolved', 'responsePresent', 'responseStatus200',
      'responseURLMatched', 'redirectAbsent', 'provisionalCspPresent', 'provisionalCspMatched',
      'rawCspPresent', 'rawCspMatched', 'rawCspReadSucceeded', 'nativeCspObserved', 'nativeCspMatched',
      'pageURLMatched'].map(key => [key, 'not-observed'])) };
}

// ExtraInfo/raw headers describe the upstream server and may precede Electron's
// override. Keep this read bounded and diagnostic-only; effective policy needs
// BOTH the browser response headers and native post-override observation below.
async function rawCspDiagnostic(response, timeoutMs) {
  let timer;
  try {
    const raw = await Promise.race([response.headerValue('content-security-policy'), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('raw CSP diagnostic timed out')), Math.min(timeoutMs, 5000));
    })]);
    return { present: typeof raw === 'string', matched: raw === contentSecurityPolicy, succeeded: true, error: null };
  } catch (error) { return { present: 'not-observed', matched: 'not-observed', succeeded: false, error: diagnosticErrorCategory(error) }; }
  finally { clearTimeout(timer); }
}

// Keep arbitrary header values in memory. Only this fixed MIME class is evidence;
// this does not substitute for the real browser's module-import decision.
export function offlineOrtModuleMime(value) {
  if (value === undefined || value === null) return 'missing';
  if (typeof value !== 'string' || value.length === 0 || value.length > 512) return 'invalid';
  const essence = value.split(';', 1)[0].trim().toLowerCase();
  if (['text/javascript', 'application/javascript', 'text/html', 'text/plain',
    'application/octet-stream', 'application/json'].includes(essence)) return essence;
  if (['application/ecmascript', 'application/x-ecmascript', 'application/x-javascript',
    'text/ecmascript', 'text/jscript', 'text/livescript', 'text/x-ecmascript', 'text/x-javascript',
    'text/javascript1.0', 'text/javascript1.1', 'text/javascript1.2', 'text/javascript1.3',
    'text/javascript1.4', 'text/javascript1.5'].includes(essence)) return 'legacy-javascript';
  return essence ? 'other' : 'invalid';
}

async function boundedImportedBody(response, timeoutMs) {
  let timer;
  try {
    return await Promise.race([response.body(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('ORT module response body timed out')), Math.min(timeoutMs, 5000));
    })]);
  } finally { clearTimeout(timer); }
}

// Reloads the owner before the probe to verify the active main-document CSP,
// and afterward to release the isolated ORT heap even on partial failure. The caller
// must gate EACH reload through beforeReload: no pending write and no unsaved
// editing operation. Missing authorization to reload fails before any probe.
export async function probeInstalledOfflineOrt({ app, page, installDir, timeoutMs = 60000, setStage = () => {},
  beforeReload, onDiagnostic = async () => {} }) {
  assert.ok(Number.isFinite(timeoutMs) && timeoutMs > 0, 'positive ORT timeout required');
  assert.equal(typeof setStage, 'function');
  assert.equal(typeof beforeReload, 'function', 'explicit safe-reload callback required');
  assert.equal(typeof onDiagnostic, 'function', 'safe diagnostic callback required');
  const installed = await discoverInstalledOfflineOrt(installDir);
  const ownerURL = page.url();
  const urls = installedOfflineOrtURLs(ownerURL, installed.assets);
  const owner = await app.browserWindow(page);
  const responseChecks = [];
  let unexpectedRequests = 0;
  let expectedLocalBackgroundRequests = 0;
  let workersCreated = 0;
  let importedResponses = 0;
  let reloadRequired = false;
  let observersDetached = false;
  const nativeKey = randomUUID();
  let nativeObserverStarted = false;
  let nativeProof = null;
  let responseObserverAuditPassed = false;
  const native = (operation, extra = {}) => app.evaluate(nativeEffectiveCspObserver, { key: nativeKey, operation, ...extra });
  const modulePolicy = { browserCspPresent: null, browserCspMatched: null,
    rawCspPresent: null, rawCspMatched: null, rawCspReadSucceeded: null, rawHeaderError: null,
    responseObserved: false, status200: null, redirectAbsent: null, mimeType: 'not-observed',
    bodyRead: 'not-started', byteCountMatched: null, sha256Matched: null, verificationError: null };
  const onRequest = (request) => {
    if (request.method() === 'GET' && Object.values(urls).includes(request.url())) return;
    try {
      const url = new URL(request.url());
      // Existing read-only app polling may overlap initialization. Observe it
      // without replacing routing, authentication, or the outer network guard.
      if (request.method() === 'GET' && url.origin === new URL(ownerURL).origin
        && !url.username && !url.password
        && ['/api/health', '/api/logs', '/api/proactive/jobs'].includes(url.pathname)) {
        expectedLocalBackgroundRequests++;
        return;
      }
    } catch { /* Invalid URLs remain unexpected. */ }
    unexpectedRequests++;
  };
  const onWorker = () => { workersCreated++; };
  const onResponse = (response) => {
    if (response.url() !== urls.mjs || response.request().resourceType() !== 'script') return;
    importedResponses++;
    modulePolicy.responseObserved = true;
    // Convert failures to values immediately to avoid unhandled rejections.
    responseChecks.push((async () => {
      modulePolicy.status200 = response.status() === 200;
      assert.equal(modulePolicy.status200, true, 'installed module import must return HTTP 200');
      modulePolicy.redirectAbsent = response.request().redirectedFrom() === null;
      assert.equal(modulePolicy.redirectAbsent, true, 'ORT module import must not redirect');
      const headers = response.headers();
      modulePolicy.mimeType = offlineOrtModuleMime(headers['content-type']);
      const effective = headers['content-security-policy'];
      modulePolicy.browserCspPresent = typeof effective === 'string';
      modulePolicy.browserCspMatched = effective === contentSecurityPolicy;
      assert.equal(effective, contentSecurityPolicy, 'module effective production CSP mismatch');
      const raw = await rawCspDiagnostic(response, timeoutMs);
      Object.assign(modulePolicy, { rawCspPresent: raw.present, rawCspMatched: raw.matched,
        rawCspReadSucceeded: raw.succeeded, rawHeaderError: raw.error });
      modulePolicy.bodyRead = 'pending';
      let bytes;
      try {
        bytes = await boundedImportedBody(response, timeoutMs);
        modulePolicy.bodyRead = 'completed';
      } catch (error) {
        modulePolicy.bodyRead = diagnosticErrorCategory(error) === 'timeout' ? 'timed-out' : 'failed';
        throw error;
      }
      modulePolicy.byteCountMatched = bytes.length === installed.assets.mjs.bytes;
      modulePolicy.sha256Matched = sha256(bytes) === installed.assets.mjs.sha256;
      assert.equal(modulePolicy.byteCountMatched, true, 'imported module byte count mismatch');
      assert.equal(modulePolicy.sha256Matched, true, 'imported module must equal installed bytes');
    })().then(() => null, (error) => {
      modulePolicy.verificationError = diagnosticErrorCategory(error);
      return error;
    }));
  };
  let result;
  let rendererDiagnostic = null;
  let primaryError;
  let primaryFailure = null;
  const diagnosticErrors = [];
  const reloads = { beforeProbe: reloadDiagnostic(), releaseProbe: reloadDiagnostic() };
  const cleanup = {
    observers: { check: 'observer-detach', result: 'not-run', errorCategory: null },
    nativeObserver: { check: 'native-observer-restore', result: 'not-run', errorCategory: null },
    ownerHandle: { check: 'owner-handle-dispose', result: 'not-run', errorCategory: null },
  };
  const observed = Object.fromEntries([...Object.keys(OWNER_EXPECTATIONS), 'additionalArgumentsArray', 'ownerArgument',
    'devTools', 'preloadOwnerRole', ...RESOURCE_CHECKS].map(key => [key, 'undefined']));
  let phase = 'owner-observation';
  let currentCheck = 'owner-observation';
  const emit = async (failedCheck = primaryFailure?.check ?? null) => {
    try {
      await onDiagnostic({ probe: 'offline-ort', schemaVersion: 5, phase, observed: { ...observed }, failedCheck,
        primaryFailure: primaryFailure && { ...primaryFailure },
        renderer: rendererDiagnostic && structuredClone(rendererDiagnostic),
        effectiveCsp: { responseObserverAuditPassed, native: nativeProof && structuredClone(nativeProof), module: { ...modulePolicy } },
        reloads: Object.fromEntries(Object.entries(reloads).map(([key, value]) =>
          [key, { ...value, observed: { ...value.observed } }])),
        cleanup: Object.fromEntries(Object.entries(cleanup).map(([key, value]) => [key, { ...value }])) });
    } catch {
      // A failed recorder must fail the probe, but must not replace an actual
      // security failure or copy the callback's potentially private exception.
      if (!diagnosticErrors.length) diagnosticErrors.push(new Error('ORT diagnostic recording failed'));
    }
  };
  const requireDiagnostic = () => {
    if (diagnosticErrors.length) { currentCheck = 'diagnostic-recording'; throw diagnosticErrors[0]; }
  };
  const expect = (security, key, value) => {
    currentCheck = key;
    assert.equal(security[key], value, key);
  };
  const reload = async (kind) => {
    const diagnostic = reloads[kind];
    diagnostic.result = 'running';
    const check = name => {
      diagnostic.check = name;
      // Cleanup diagnostics must not overwrite the primary phase/check.
      if (kind === 'beforeProbe') currentCheck = name;
    };
    try {
      check('native-csp-arm');
      assert.equal((await native('arm', { phase: kind })).armed, true);
      check('before-reload-gate');
      await beforeReload(kind === 'beforeProbe' ? 'before-probe' : 'release-probe');
      diagnostic.observed.safeReloadGatePassed = true;
      if (kind === 'beforeProbe') reloadRequired = true;
      // No diagnostic callback/await may intervene between the gate and reload.
      check('reload-await');
      const response = await page.reload({ waitUntil: 'networkidle', timeout: timeoutMs });
      diagnostic.observed.reloadResolved = true;
      check('response-present');
      diagnostic.observed.responsePresent = Boolean(response);
      assert.ok(response, 'installed owner reload failed');
      check('response-status');
      diagnostic.observed.responseStatus200 = response.status() === 200;
      assert.equal(diagnostic.observed.responseStatus200, true, 'installed owner reload failed');
      check('response-url');
      diagnostic.observed.responseURLMatched = response.url() === ownerURL;
      assert.equal(diagnostic.observed.responseURLMatched, true, 'installed owner reload failed');
      check('response-redirect');
      diagnostic.observed.redirectAbsent = response.request().redirectedFrom() === null;
      assert.equal(diagnostic.observed.redirectAbsent, true, 'owner reload must not redirect');
      check('provisional-csp-read');
      const provisionalCsp = response.headers()['content-security-policy'];
      diagnostic.observed.provisionalCspPresent = typeof provisionalCsp === 'string';
      diagnostic.observed.provisionalCspMatched = provisionalCsp === contentSecurityPolicy;
      check('browser-csp-match');
      assert.equal(provisionalCsp, contentSecurityPolicy, 'active document must use effective production CSP');
      check('raw-csp-read');
      const raw = await rawCspDiagnostic(response, timeoutMs);
      diagnostic.observed.rawCspPresent = raw.present;
      diagnostic.observed.rawCspMatched = raw.matched;
      diagnostic.observed.rawCspReadSucceeded = raw.succeeded;
      diagnostic.rawHeaderError = raw.error;
      check('native-effective-csp');
      nativeProof = await native('snapshot');
      diagnostic.observed.nativeCspObserved = nativeProof.records?.[kind]?.count === 1;
      diagnostic.observed.nativeCspMatched = nativeProof.records?.[kind]?.cspMatched === true;
      assert.equal(nativeProof.registered, true, 'native effective CSP observer must remain registered');
      assertEffectiveCspObservation(nativeProof, { [kind]: 1 });
      check('page-url');
      diagnostic.observed.pageURLMatched = page.url() === ownerURL;
      assert.equal(diagnostic.observed.pageURLMatched, true, 'installed owner must not navigate');
      diagnostic.check = 'complete';
      diagnostic.result = 'passed';
    } catch (error) {
      diagnostic.result = 'failed';
      diagnostic.errorCategory = diagnosticErrorCategory(error);
      throw error;
    }
  };
  try {
    setStage('installed-ort-owner');
    const security = await app.evaluate(readOfflineOrtOwner, { owner, ownerURL });
    for (const key of [...Object.keys(OWNER_EXPECTATIONS), 'additionalArgumentsArray', 'ownerArgument', 'devTools']) {
      observed[key] = diagnosticBoolean(security[key]);
    }
    await emit();
    phase = 'owner-validation';
    for (const [key, value] of Object.entries(OWNER_EXPECTATIONS)) expect(security, key, value);
    // The fixed Electron getter does not expose constructor arguments. Strong
    // identity is instead checked above against real main-process windows and
    // sessions, and below against the production preload. If args are returned,
    // malformed values or a missing owner marker still fail closed.
    if (security.additionalArgumentsArray !== undefined) {
      expect(security, 'additionalArgumentsArray', true);
      expect(security, 'ownerArgument', true);
    }
    // Electron 44 may omit devTools in getLastWebPreferences. The outer suite
    // independently exercises openDevTools; this probe never opens them.
    if (security.devTools !== undefined) expect(security, 'devTools', false);
    requireDiagnostic();
    currentCheck = 'preloadOwnerRole';
    const preloadOwnerRole = await page.evaluate(readOfflineOrtPreloadOwner);
    observed.preloadOwnerRole = diagnosticBoolean(preloadOwnerRole);
    await emit();
    assert.equal(preloadOwnerRole, true, 'preloadOwnerRole');
    requireDiagnostic();
    phase = 'owner-resources';
    await assertInstalledResourcesIdentity(security.resourcesPath, installed.resourcesPath, fs, async (check, passed) => {
      currentCheck = check;
      observed[check] = passed;
      await emit(passed ? null : check);
    });
    requireDiagnostic();
    currentCheck = 'response-observer-source-audit';
    const audit = await auditInstalledResponseObserver(installDir);
    assert.equal(audit.responseStartedUnused, true);
    responseObserverAuditPassed = true;
    currentCheck = 'response-observer-install';
    nativeObserverStarted = true;
    const installedObserver = await native('install', { owner, ownerURL, moduleURL: urls.mjs,
      policy: contentSecurityPolicy, sourceAuditPassed: true });
    assert.equal(installedObserver.installed, true);
    assert.equal(installedObserver.productionBlockingHandlersChanged, false);
    phase = 'document-csp';
    currentCheck = 'document-csp';
    await emit();
    requireDiagnostic();
    setStage('installed-ort-document-csp');
    await reload('beforeProbe');
    page.on('request', onRequest);
    page.on('response', onResponse);
    page.on('worker', onWorker);
    currentCheck = 'native-runtime-arm';
    assert.equal((await native('arm', { phase: 'runtime' })).armed, true);
    phase = 'initialization';
    currentCheck = 'initialization';
    await emit();
    requireDiagnostic();
    setStage('installed-ort-initialize');
    result = await page.evaluate(initializeOfflineOrtInRenderer,
      { ownerURL, urls, assets: installed.assets, csp: contentSecurityPolicy, timeoutMs, captureDiagnostic: true });
    rendererDiagnostic = safeOfflineOrtRendererDiagnostic(result?.rendererDiagnostic);
    assert.ok(rendererDiagnostic, 'installed ORT renderer diagnostic missing or invalid');
    await emit();
    requireDiagnostic();
    if (result.rendererFailure === true) throw new Error('installed ORT initialization failed at ' + rendererDiagnostic.phase);
    assert.equal(rendererDiagnostic.outcome, 'passed', 'installed ORT renderer result must succeed');
    assertOfflineOrtInitialization(result, installed.assets);
    assert.deepEqual((await discoverInstalledOfflineOrt(installDir)).assets, installed.assets, 'installed ORT assets changed during probe');
    // Keep observing throughout every asynchronous asset/response verification.
    // New response checks queued while awaiting a prior batch must be included.
    let checkedResponses = 0;
    while (checkedResponses < responseChecks.length) {
      assert.equal(importedResponses, 1, 'one actual installed module import response required');
      const pending = responseChecks.slice(checkedResponses);
      checkedResponses += pending.length;
      for (const error of await Promise.all(pending)) if (error) throw error;
    }
    currentCheck = 'native-runtime-effective-csp';
    nativeProof = await native('snapshot');
    assert.equal(nativeProof.registered, true);
    assertEffectiveCspObservation(nativeProof, { beforeProbe: 1, module: 1, releaseProbe: 0 });
    // This is a finite observation window, ending after initialization and all
    // collected byte verifications. Close it synchronously before final checks:
    // no await may separate this detach from the counter/owner assertions.
    for (const [event, listener] of [['request', onRequest], ['response', onResponse], ['worker', onWorker]]) {
      page.removeListener(event, listener);
    }
    observersDetached = true;
    assert.equal(importedResponses, 1, 'one actual installed module import response required');
    assert.equal(checkedResponses, responseChecks.length, 'all observed module responses must be verified');
    assert.equal(unexpectedRequests, 0, 'unexpected request during installed ORT initialization');
    assert.equal(workersCreated, 0, 'single-threaded ORT must not start workers');
    assert.equal(page.url(), ownerURL, 'installed ORT owner URL changed');
  } catch (error) {
    primaryError = error;
    primaryFailure = { check: currentCheck, errorCategory: diagnosticErrorCategory(error) };
    await emit();
  }
  // A rejected dynamic import can still have a completed HTTP response. Finish
  // the already-started, bounded read before reload discards that response. This
  // adds no request and never replaces the original renderer rejection.
  if (primaryError) await Promise.all(responseChecks);
  const cleanupErrors = [];
  cleanup.observers.result = 'passed';
  if (!observersDetached) {
    for (const [event, listener] of [['request', onRequest], ['response', onResponse], ['worker', onWorker]]) {
      try { page.removeListener(event, listener); } catch (error) {
        cleanup.observers.result = 'failed';
        cleanup.observers.errorCategory ??= diagnosticErrorCategory(error);
        cleanupErrors.push(new Error('ORT observer cleanup failed'));
      }
    }
  }
  if (reloadRequired) {
    try { await reload('releaseProbe'); }
    catch { cleanupErrors.push(new Error('ORT renderer cleanup reload failed or was unsafe')); }
  }
  if (nativeObserverStarted) {
    try {
      const restored = await native('restore');
      assert.equal(restored.restored, true, 'native CSP observer restoration required');
      assert.equal(restored.absent, false, 'installed native CSP observer cannot disappear before restoration');
      nativeProof = restored.snapshot;
      assert.equal(nativeProof.registered, false, 'native CSP observer must be unregistered');
      if (!primaryError && cleanupErrors.length === 0) {
        assertEffectiveCspObservation(nativeProof, { beforeProbe: 1, module: 1, releaseProbe: 1 });
      }
      cleanup.nativeObserver.result = 'passed';
    } catch (error) {
      cleanup.nativeObserver.result = 'failed';
      cleanup.nativeObserver.errorCategory = diagnosticErrorCategory(error);
      cleanupErrors.push(new Error('ORT native CSP observer cleanup or final observation failed'));
    }
  }
  try { await owner.dispose(); cleanup.ownerHandle.result = 'passed'; } catch (error) {
    cleanup.ownerHandle.result = 'failed';
    cleanup.ownerHandle.errorCategory = diagnosticErrorCategory(error);
    cleanupErrors.push(new Error('ORT owner handle cleanup failed'));
  }
  await emit();
  cleanupErrors.push(...diagnosticErrors.filter(error => error !== primaryError));
  if (primaryError && cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'ORT probe and cleanup failed');
  if (primaryError) throw primaryError;
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'ORT probe cleanup failed');
  return { mechanism: 'installed-self-assets-in-real-owner-renderer', ...result, assets: installed.assets,
    mainDocumentProductionCspVerified: true, importedModuleBytesVerified: true,
    effectiveCspVerifiedBy: ['browser-response-headers', 'electron-post-override-onResponseStarted'],
    upstreamRawCspIsDiagnosticOnly: true, responseObserverAuditPassed,
    nativeEffectiveCsp: nativeProof, nativeResponseObserverRestored: true,
    observationWindow: 'initialization-through-asset-and-response-verification',
    observedUnexpectedRequests: unexpectedRequests, expectedLocalBackgroundRequests, observedWorkersCreated: workersCreated,
    rendererReleasedByReload: true, securityPolicyChanged: false,
    modelDownloaded: false, microphoneAccessed: false, inferenceSessionCreated: false,
    whisperTranscriptionValidated: false };
}
