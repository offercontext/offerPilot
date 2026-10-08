import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';

const { contentSecurityPolicy } = createRequire(import.meta.url)('../capabilities.cjs');
const ASSET_NAMES = Object.freeze({
  mjs: /^ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.mjs$/,
  wasm: /^ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.wasm$/,
});
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const comparablePath = (value) => path.resolve(value).replaceAll('\\', '/').toLowerCase();

// Only inspect the installed executable assets. No recursive search, developer
// build fallback, user profile, token, model, or owner data is read.
export async function discoverInstalledOfflineOrt(installDir, filesystem = fs) {
  assert.ok(typeof installDir === 'string' && path.isAbsolute(installDir), 'absolute installed directory required');
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
export function readOfflineOrtOwner({ app }, { owner, ownerURL }) {
  const contents = owner.webContents;
  const prefs = contents.getLastWebPreferences();
  return { packaged: app.isPackaged, resourcesPath: process.resourcesPath,
    ownerURLMatched: !contents.isDestroyed() && contents.getURL() === ownerURL,
    ownerArgument: prefs.additionalArguments?.includes('--offerpilot-owner') === true,
    nodeIntegration: prefs.nodeIntegration, contextIsolation: prefs.contextIsolation,
    sandbox: prefs.sandbox, webSecurity: prefs.webSecurity, devTools: prefs.devTools,
    devToolsOpened: contents.isDevToolsOpened(), devToolsContentsPresent: Boolean(contents.devToolsWebContents) };
}

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
  const ensure = (condition, message) => { if (!condition) throw new Error(message); };
  const names = {
    mjs: /^\/assets\/ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.mjs$/,
    wasm: /^\/assets\/ort-wasm-simd-threaded\.asyncify(?:-[A-Za-z0-9_-]+)?\.wasm$/,
  };
  const owner = new URL(args.ownerURL);
  ensure(location.href === args.ownerURL && owner.protocol === 'http:' && owner.hostname === '127.0.0.1'
    && owner.port && owner.pathname === '/' && !owner.username && !owner.password
    && owner.searchParams.get('desktopSurface') !== 'haru', 'ORT renderer owner mismatch');
  for (const kind of ['mjs', 'wasm']) {
    const url = new URL(args.urls[kind]);
    ensure(url.origin === owner.origin && url.protocol === 'http:' && !url.username && !url.password
      && !url.search && !url.hash && names[kind].test(url.pathname), 'ORT asset must be an exact installed self URL');
  }
  let violations = 0;
  const violation = () => { violations++; };
  document.addEventListener('securitypolicyviolation', violation);
  const controller = new AbortController();
  let timer;
  try {
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => { controller.abort(); reject(new Error('installed ORT initialization timed out')); }, args.timeoutMs);
    });
    return await Promise.race([deadline, (async () => {
      // Authentication is supplied by the existing Electron session handler.
      // Never inspect or copy its token and never follow a redirect.
      const response = await fetchAsset(args.urls.wasm, { credentials: 'same-origin', mode: 'same-origin',
        redirect: 'error', cache: 'no-store', signal: controller.signal });
      ensure(response.ok && response.url === args.urls.wasm && !response.redirected, 'installed WASM response mismatch');
      ensure(response.headers.get('content-security-policy') === args.csp, 'installed WASM production CSP mismatch');
      const bytes = new Uint8Array(await response.arrayBuffer());
      ensure(bytes.byteLength === args.assets.wasm.bytes, 'installed WASM byte count mismatch');
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
        (value) => value.toString(16).padStart(2, '0')).join('');
      ensure(digest === args.assets.wasm.sha256, 'installed WASM digest mismatch');
      const compiled = await webAssembly.compile(bytes);
      ensure(compiled instanceof webAssembly.Module, 'real WebAssembly compilation required');
      const imported = await importModule(args.urls.mjs);
      ensure(typeof imported.default === 'function', 'installed ORT factory missing');
      // The locked factory creates numThreads - 1 workers. wasmBinary prevents
      // another fetch. locateFile refuses every asset except this exact WASM.
      const ort = await imported.default({ numThreads: 1, wasmBinary: bytes, locateFile: (name) => {
        ensure(name === 'ort-wasm-simd-threaded.asyncify.wasm', 'unexpected ORT runtime asset requested');
        return args.urls.wasm;
      } });
      ensure(ort?.calledRun === true && ort.numThreads === 1, 'ORT factory did not initialize single-threaded');
      ensure(ort.HEAPU8 instanceof Uint8Array && ort.HEAPU8.byteLength >= 65536, 'initialized ORT WASM heap missing');
      ensure(typeof ort.asyncInit === 'function' && typeof ort._OrtInit === 'function'
        && typeof ort._OrtCreateSession === 'function', 'initialized ORT exports missing');
      ort.asyncInit();
      // Matches locked onnxruntime-web/lib/wasm/wasm-core-impl.ts initOrt:
      // one intra-op thread, warning logging, zero means environment ready.
      const initCode = ort._OrtInit(1, 2);
      ensure(initCode === 0, 'ORT environment initialization failed');
      ensure(location.href === args.ownerURL, 'ORT owner navigated during initialization');
      ensure(violations === 0, 'ORT initialization violated production CSP');
      return { wasmCompiled: true, factoryInitialized: ort.calledRun, numThreads: ort.numThreads,
        heapBytes: ort.HEAPU8.byteLength, ortInitCode: initCode, wasmSha256: digest, cspViolations: violations };
    })()]);
  } finally {
    clearTimeout(timer);
    controller.abort();
    document.removeEventListener('securitypolicyviolation', violation);
  }
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

// Reloads the owner before the probe to verify the active main-document CSP,
// and afterward to release the isolated ORT heap even on partial failure. The caller
// must gate EACH reload through beforeReload: no pending write and no unsaved
// editing operation. Missing authorization to reload fails before any probe.
export async function probeInstalledOfflineOrt({ app, page, installDir, timeoutMs = 60000, setStage = () => {}, beforeReload }) {
  assert.ok(Number.isFinite(timeoutMs) && timeoutMs > 0, 'positive ORT timeout required');
  assert.equal(typeof setStage, 'function');
  assert.equal(typeof beforeReload, 'function', 'explicit safe-reload callback required');
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
    // Convert failures to values immediately to avoid unhandled rejections.
    responseChecks.push((async () => {
      assert.equal(response.status(), 200, 'installed module import must return HTTP 200');
      assert.equal(response.request().redirectedFrom(), null, 'ORT module import must not redirect');
      assert.equal(await response.headerValue('content-security-policy'), contentSecurityPolicy, 'module production CSP mismatch');
      const bytes = await response.body();
      assert.equal(bytes.length, installed.assets.mjs.bytes, 'imported module byte count mismatch');
      assert.equal(sha256(bytes), installed.assets.mjs.sha256, 'imported module must equal installed bytes');
    })().then(() => null, (error) => error));
  };
  const reload = async () => {
    const response = await page.reload({ waitUntil: 'networkidle', timeout: timeoutMs });
    assert.ok(response && response.status() === 200 && response.url() === ownerURL, 'installed owner reload failed');
    assert.equal(response.request().redirectedFrom(), null, 'owner reload must not redirect');
    assert.equal(await response.headerValue('content-security-policy'), contentSecurityPolicy, 'active document must use production CSP');
    assert.equal(page.url(), ownerURL, 'installed owner must not navigate');
  };
  let result;
  let primaryError;
  try {
    setStage('installed-ort-owner');
    const security = await app.evaluate(readOfflineOrtOwner, { owner, ownerURL });
    assert.equal(security.packaged, true, 'installed packaged app required');
    assert.equal(comparablePath(security.resourcesPath), comparablePath(installed.resourcesPath), 'running app must use selected installation');
    for (const key of ['ownerURLMatched', 'ownerArgument', 'contextIsolation', 'sandbox', 'webSecurity']) assert.equal(security[key], true, key);
    for (const key of ['nodeIntegration', 'devToolsOpened', 'devToolsContentsPresent']) assert.equal(security[key], false, key);
    // Electron 44 may omit devTools in getLastWebPreferences. The outer suite
    // independently exercises openDevTools; this probe never opens them.
    if (security.devTools !== undefined) assert.equal(security.devTools, false, 'devTools');
    setStage('installed-ort-document-csp');
    await beforeReload('before-probe');
    reloadRequired = true;
    await reload();
    page.on('request', onRequest);
    page.on('response', onResponse);
    page.on('worker', onWorker);
    setStage('installed-ort-initialize');
    result = await page.evaluate(initializeOfflineOrtInRenderer,
      { ownerURL, urls, assets: installed.assets, csp: contentSecurityPolicy, timeoutMs });
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
  } catch (error) { primaryError = error; }
  const cleanupErrors = [];
  if (!observersDetached) {
    for (const [event, listener] of [['request', onRequest], ['response', onResponse], ['worker', onWorker]]) {
      try { page.removeListener(event, listener); } catch { cleanupErrors.push(new Error('ORT observer cleanup failed')); }
    }
  }
  if (reloadRequired) {
    try { await beforeReload('release-probe'); await reload(); }
    catch { cleanupErrors.push(new Error('ORT renderer cleanup reload failed or was unsafe')); }
  }
  try { await owner.dispose(); } catch { cleanupErrors.push(new Error('ORT owner handle cleanup failed')); }
  if (primaryError && cleanupErrors.length) throw new AggregateError([primaryError, ...cleanupErrors], 'ORT probe and cleanup failed');
  if (primaryError) throw primaryError;
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'ORT probe cleanup failed');
  return { mechanism: 'installed-self-assets-in-real-owner-renderer', ...result, assets: installed.assets,
    mainDocumentProductionCspVerified: true, importedModuleBytesVerified: true,
    observationWindow: 'initialization-through-asset-and-response-verification',
    observedUnexpectedRequests: unexpectedRequests, expectedLocalBackgroundRequests, observedWorkersCreated: workersCreated,
    rendererReleasedByReload: true, securityPolicyChanged: false,
    modelDownloaded: false, microphoneAccessed: false, inferenceSessionCreated: false,
    whisperTranscriptionValidated: false };
}
