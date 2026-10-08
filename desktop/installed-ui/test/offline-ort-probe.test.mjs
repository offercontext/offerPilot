import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash, webcrypto } from 'node:crypto';
import { createRequire } from 'node:module';
import { EventEmitter } from 'node:events';
import vm from 'node:vm';
import { discoverInstalledOfflineOrt, installedOfflineOrtURLs, initializeOfflineOrtInRenderer,
  assertOfflineOrtInitialization, assertInstalledResourcesIdentity, probeInstalledOfflineOrt, readOfflineOrtOwner } from '../offline-ort-probe.mjs';

const { contentSecurityPolicy: csp } = createRequire(import.meta.url)('../../capabilities.cjs');
const origin = 'http://127.0.0.1:18420';
const ownerURL = `${origin}/?view=settings`;
const mjsName = 'ort-wasm-simd-threaded.asyncify-fixture.mjs';
const wasmName = 'ort-wasm-simd-threaded.asyncify-fixture.wasm';
const wasmBytes = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]); // real, empty WASM; never an ORT fixture
const mjsBytes = Buffer.from('// Unit fixture only. This is not an ORT runtime.');
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');
const metadata = { mjs: { name: mjsName, bytes: mjsBytes.length, sha256: digest(mjsBytes) },
  wasm: { name: wasmName, bytes: wasmBytes.length, sha256: digest(wasmBytes) } };

async function files(t) {
  const installDir = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-ort-unit-'));
  t.after(() => fs.rm(installDir, { recursive: true, force: true }));
  // Keep the raw mkdtemp spelling as an input: Windows may return RUNNER~1.
  // Disk-observer hooks and expected discovery output use its actual identity.
  const canonicalInstallDir = await fs.realpath(installDir);
  const assetsDir = path.join(canonicalInstallDir, 'resources', 'web', 'assets');
  await fs.mkdir(assetsDir, { recursive: true });
  await fs.writeFile(path.join(assetsDir, mjsName), mjsBytes);
  await fs.writeFile(path.join(assetsDir, wasmName), wasmBytes);
  return { installDir, canonicalInstallDir, assetsDir };
}

// Simulate an OS short-name lookup, not a symlink: all reads still use the real
// fixture directory and real inode/device metadata. The raw alias deliberately
// differs even on Linux, where mkdtemp alone would hide the Windows regression.
function filesystemAlias(f) {
  const aliasRoot = path.join(path.dirname(f.canonicalInstallDir), `ORT~1-${path.basename(f.canonicalInstallDir)}`);
  const map = (value) => {
    const relative = path.relative(aliasRoot, value);
    return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))
      ? path.join(f.canonicalInstallDir, relative) : value;
  };
  const original = { realpath: fs.realpath, lstat: fs.lstat, stat: fs.stat };
  const realpathInputs = [];
  const ports = { ...fs,
    realpath: async (value, ...args) => { realpathInputs.push(value); return original.realpath(map(value), ...args); },
    lstat: (value, ...args) => original.lstat(map(value), ...args),
    stat: (value, ...args) => original.stat(map(value), ...args),
  };
  return { aliasRoot, ports, realpathInputs,
    install(t) { for (const name of Object.keys(original)) t.mock.method(fs, name, ports[name]); } };
}

// Injected factory below tests failure propagation only. It is deliberately NOT
// an ORT module or Windows/browser/CSP success claim. Compilation uses real WASM.
function rendererHarness(options = {}) {
  const document = new EventEmitter();
  document.addEventListener = document.on.bind(document);
  document.removeEventListener = document.removeListener.bind(document);
  const args = { ownerURL, urls: installedOfflineOrtURLs(ownerURL, metadata), assets: metadata, csp, timeoutMs: 200 };
  const calls = { imports: 0, factories: 0, compilation: 0, init: [], options: null };
  const location = { href: ownerURL };
  const dependencies = { crypto: webcrypto, location, document,
    webAssembly: { Module: WebAssembly.Module, compile: async (bytes) => {
      calls.compilation++;
      if (options.compileError) throw new Error('compile refused');
      return options.fakeCompilation ? {} : WebAssembly.compile(bytes);
    } },
    fetch: async (url, config) => {
      calls.fetch = { url, config };
      if (options.fetchHangs) return new Promise(() => {});
      if (options.fetchError) throw new Error('fetch refused');
      return { ok: !options.httpFailure, url: options.redirect ? 'https://example.invalid/' : url,
        redirected: Boolean(options.redirect), headers: { get: () => options.wrongCsp ? "script-src 'self'" : csp },
        arrayBuffer: async () => (options.corruptBytes ? new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]) : wasmBytes).buffer };
    },
    importModule: async (url) => {
      calls.imports++;
      assert.equal(url, args.urls.mjs);
      if (options.importError) throw new Error('module blocked by CSP');
      options.onImport?.();
      return { default: options.missingFactory ? undefined : async (config) => {
        calls.factories++;
        calls.options = config;
        assert.equal(config.numThreads, 1);
        assert.deepEqual(config.wasmBinary, wasmBytes);
        assert.equal(config.locateFile('ort-wasm-simd-threaded.asyncify.wasm'), args.urls.wasm);
        if (options.unexpectedLocate) config.locateFile('https://example.invalid/model.onnx');
        if (options.factoryRejects) throw new Error('factory refused');
        return { calledRun: !options.notInitialized, numThreads: options.wrongThreadCount ? 2 : 1,
          HEAPU8: options.missingHeap ? undefined : new Uint8Array(65536),
          asyncInit() { if (options.violation) document.emit('securitypolicyviolation'); },
          _OrtCreateSession: options.missingExports ? undefined : () => { throw new Error('must not create an inference session'); },
          _OrtInit(...values) { calls.init.push(values); return options.badInit ? 1 : 0; } };
      } };
    } };
  return { args, dependencies, calls, document, location,
    run: () => initializeOfflineOrtInRenderer(args, dependencies) };
}

test('asset discovery selects only unique installed asyncify files and hashes their actual bytes', async (t) => {
  const f = await files(t);
  await fs.writeFile(path.join(f.assetsDir, 'ort-wasm-simd-threaded-standard.wasm'), 'ignored unrelated asset');
  assert.deepEqual(await discoverInstalledOfflineOrt(f.installDir),
    { resourcesPath: path.join(f.canonicalInstallDir, 'resources'), assets: metadata });
});

test('discovery resolves a raw short-name alias while retaining canonical asset paths and byte hashes', async t => {
  const f = await files(t);
  const alias = filesystemAlias(f);
  assert.notEqual(alias.aliasRoot, f.canonicalInstallDir);
  const discovered = await discoverInstalledOfflineOrt(alias.aliasRoot, alias.ports);
  assert.deepEqual(discovered, { resourcesPath: path.join(f.canonicalInstallDir, 'resources'), assets: metadata });
  assert.ok(alias.realpathInputs.includes(alias.aliasRoot), 'raw installation input must really be resolved');
});

test('resource binding resolves both raw and canonical spellings to one real directory identity', async t => {
  const f = await files(t);
  const alias = filesystemAlias(f);
  const raw = path.join(alias.aliasRoot, 'resources');
  const canonical = path.join(f.canonicalInstallDir, 'resources');
  assert.notEqual(raw, canonical, 'plain string comparison must not accidentally pass the regression');
  assert.equal(await assertInstalledResourcesIdentity(raw, canonical, alias.ports), true);
  assert.ok(alias.realpathInputs.includes(raw));
  assert.ok(alias.realpathInputs.includes(canonical));
  assert.equal(await assertInstalledResourcesIdentity(canonical, raw, alias.ports), true);
});

test('an actually different installation remains rejected even when its ORT asset bytes are identical', async t => {
  const first = await files(t);
  const second = await files(t);
  await assert.rejects(assertInstalledResourcesIdentity(path.join(first.installDir, 'resources'),
    path.join(second.installDir, 'resources')), /running app must use selected installation/);
  await assert.rejects(assertInstalledResourcesIdentity('relative/resources', path.join(first.installDir, 'resources')),
    /absolute installed resources/);
});

test('file identity differences cannot pass a matching canonical path comparison', async t => {
  const f = await files(t);
  const resource = path.join(f.installDir, 'resources');
  for (const field of ['ino', 'dev']) {
    let reads = 0;
    await assert.rejects(assertInstalledResourcesIdentity(resource, resource, { ...fs, stat: async (...args) => {
      const stat = await fs.stat(...args);
      if (++reads === 2) return { ...stat, [field]: stat[field] + 1n, isDirectory: () => true };
      return stat;
    } }), field === 'ino' ? /file identity mismatch/ : /device identity mismatch/);
  }
});

test('resource, installation-root, and ancestor symlinks/junctions remain rejected despite resolving to the same target', async t => {
  const f = await files(t);
  const type = process.platform === 'win32' ? 'junction' : 'dir';
  const resource = path.join(f.canonicalInstallDir, 'resources');
  const resourceLink = path.join(f.canonicalInstallDir, 'resource-link');
  const rootLink = path.join(f.canonicalInstallDir, 'root-link');
  await fs.symlink(resource, resourceLink, type);
  await fs.symlink(f.canonicalInstallDir, rootLink, type);
  await assert.rejects(assertInstalledResourcesIdentity(resourceLink, resource), /must not use links/);
  for (const suffix of new Set([path.sep, '/', path.sep.repeat(2)])) {
    await assert.rejects(assertInstalledResourcesIdentity(`${resourceLink}${suffix}`, resource), /must not use links/);
    await assert.rejects(assertInstalledResourcesIdentity(resource, `${resourceLink}${suffix}`), /must not use links/);
    await assert.rejects(discoverInstalledOfflineOrt(`${rootLink}${suffix}`), /must not be links/);
  }
  await assert.rejects(assertInstalledResourcesIdentity(path.join(rootLink, 'resources'), resource), /must not use links/);
  await assert.rejects(assertInstalledResourcesIdentity(`${rootLink}${path.sep.repeat(2)}resources`, resource), /must not use links/);
  await assert.rejects(discoverInstalledOfflineOrt(rootLink), /must not be links/);
  const holder = path.join(f.canonicalInstallDir, 'holder');
  const nested = path.join(holder, 'nested-installation', 'resources');
  const holderLink = path.join(f.canonicalInstallDir, 'holder-link');
  await fs.mkdir(nested, { recursive: true });
  await fs.symlink(holder, holderLink, type);
  await assert.rejects(assertInstalledResourcesIdentity(path.join(holderLink, 'nested-installation', 'resources'), nested),
    /must not use links/);
  await assert.rejects(assertInstalledResourcesIdentity(`${holderLink}${path.sep.repeat(2)}nested-installation${path.sep}resources`, nested),
    /must not use links/);
});

test('case-distinct POSIX directories never become the same installed resource identity', { skip: process.platform === 'win32' }, async t => {
  const f = await files(t);
  const upper = path.join(f.canonicalInstallDir, 'Upper', 'resources');
  const lower = path.join(f.canonicalInstallDir, 'upper', 'resources');
  await fs.mkdir(upper, { recursive: true });
  await fs.mkdir(lower, { recursive: true });
  await assert.rejects(assertInstalledResourcesIdentity(upper, lower), /running app must use selected installation/);
});

for (const kind of ['mjs', 'wasm']) {
  test(`missing or ambiguous ${kind} never falls back to a developer build`, async (t) => {
    const f = await files(t);
    const selected = kind === 'mjs' ? mjsName : wasmName;
    await fs.rm(path.join(f.assetsDir, selected));
    await assert.rejects(discoverInstalledOfflineOrt(f.installDir), /exactly one installed asyncify/);
    await fs.writeFile(path.join(f.assetsDir, selected), 'fixture 1');
    await fs.writeFile(path.join(f.assetsDir, `ort-wasm-simd-threaded.asyncify-other.${kind}`), 'fixture 2');
    await assert.rejects(discoverInstalledOfflineOrt(f.installDir), /exactly one installed asyncify/);
  });
}

test('asset filesystem dependencies cannot supply traversal, links, or a redirected asset directory', async (t) => {
  const f = await files(t);
  const injected = { ...fs, readdir: async () => [{ name: `../${mjsName}`, isFile: () => true, isSymbolicLink: () => false }] };
  await assert.rejects(discoverInstalledOfflineOrt(f.installDir, injected), /exactly one installed/);
  await assert.rejects(discoverInstalledOfflineOrt(f.installDir, { ...fs, lstat: async () => ({ isDirectory: () => true, isSymbolicLink: () => true }) }), /must not be links/);
  await assert.rejects(discoverInstalledOfflineOrt(f.installDir, { ...fs, realpath: async (value) => value === f.installDir ? value : '/outside' }), /escaped installation/);
  await assert.rejects(discoverInstalledOfflineOrt('relative/install'), /absolute installed directory/);
});

for (const candidate of ['https://example.invalid/', 'http://localhost:18420/', 'file:///tmp/index.html',
  `${origin}/assets/`, `${origin}/?desktopSurface=haru`, 'http://user@127.0.0.1:18420/']) {
  test(`non-owner URL rejected: ${candidate}`, () => assert.throws(() => installedOfflineOrtURLs(candidate, metadata), /owner URL/));
}
for (const name of ['../runtime.mjs', 'https://example.invalid/ort-wasm-simd-threaded.asyncify.mjs', `${mjsName}?x=1`, `${mjsName}#hash`]) {
  test(`non-installed asset path rejected: ${name}`, () => assert.throws(() => installedOfflineOrtURLs(ownerURL,
    { ...metadata, mjs: { ...metadata.mjs, name } }), /invalid installed/));
}

test('renderer validates real compilation, factory completion and ORT init return code in dependency harness', async () => {
  const f = rendererHarness();
  const result = await f.run();
  assertOfflineOrtInitialization(result, metadata);
  assert.equal(f.calls.compilation, 1);
  assert.equal(f.calls.imports, 1);
  assert.deepEqual(f.calls.init, [[1, 2]]);
  assert.equal(f.calls.fetch.config.redirect, 'error');
  assert.equal(f.calls.fetch.config.mode, 'same-origin');
  assert.equal(f.calls.fetch.config.credentials, 'same-origin');
  assert.equal(f.document.listenerCount('securitypolicyviolation'), 0);
});

for (const [option, pattern] of [['compileError', /compile refused/], ['fakeCompilation', /real WebAssembly/],
  ['fetchError', /fetch refused/], ['httpFailure', /response mismatch/], ['redirect', /response mismatch/],
  ['wrongCsp', /CSP mismatch/], ['corruptBytes', /digest mismatch/], ['importError', /blocked by CSP/],
  ['missingFactory', /factory missing/], ['factoryRejects', /factory refused/], ['notInitialized', /single-threaded/],
  ['wrongThreadCount', /single-threaded/], ['missingHeap', /heap missing/], ['missingExports', /exports missing/],
  ['badInit', /initialization failed/], ['unexpectedLocate', /unexpected ORT runtime asset/], ['violation', /violated production CSP/]]) {
  test(`renderer ${option} cannot become successful initialization evidence`, async () => {
    const f = rendererHarness({ [option]: true });
    await assert.rejects(f.run(), pattern);
    assert.equal(f.document.listenerCount('securitypolicyviolation'), 0);
  });
}

test('renderer rejects external URLs and changed owner before any fetch or import', async () => {
  for (const url of ['https://example.invalid/runtime.mjs', `blob:${origin}/fake`, `${origin}/assets/${mjsName}?remote=1`]) {
    const f = rendererHarness();
    f.args.urls.mjs = url;
    await assert.rejects(f.run(), /exact installed self URL/);
    assert.equal(f.calls.fetch, undefined);
  }
  const f = rendererHarness();
  f.location.href = `${origin}/?desktopSurface=haru`;
  await assert.rejects(f.run(), /owner mismatch/);
  assert.equal(f.calls.fetch, undefined);
});

test('renderer timeout aborts fetch and removes its read-only CSP observer', async () => {
  const f = rendererHarness({ fetchHangs: true });
  f.args.timeoutMs = 15;
  await assert.rejects(f.run(), /timed out/);
  assert.equal(f.calls.fetch.config.signal.aborted, true);
  assert.equal(f.document.listenerCount('securitypolicyviolation'), 0);
});

test('result assertions reject incomplete/fabricated-success-shaped runtime evidence', async () => {
  const result = await rendererHarness().run();
  for (const [field, value] of [['wasmCompiled', false], ['factoryInitialized', false], ['numThreads', 2],
    ['heapBytes', 0], ['ortInitCode', 1], ['wasmSha256', 'wrong'], ['cspViolations', 1]]) {
    assert.throws(() => assertOfflineOrtInitialization({ ...result, [field]: value }, metadata));
  }
});

async function probeHarness(t, options = {}) {
  const f = await files(t);
  const page = new EventEmitter();
  let reloads = 0;
  let disposed = 0;
  const reloadGates = [];
  const reloadResponse = { status: () => 200, url: () => ownerURL,
    request: () => ({ redirectedFrom: () => null }),
    headerValue: async () => options.documentCsp ? "script-src 'self'" : csp };
  Object.assign(page, { url: () => ownerURL, reload: async () => {
    reloads++;
    if (options.cleanupFails && reloads === 2) throw new Error('reload failed');
    return reloadResponse;
  } });
  const renderer = rendererHarness({ ...options, onImport: () => {
    const request = { method: () => 'GET', url: () => renderer.args.urls.mjs,
      resourceType: () => 'script', redirectedFrom: () => options.moduleRedirect ? {} : null };
    page.emit('request', request);
    if (options.unexpectedRequest) page.emit('request', { method: () => 'GET', url: () => 'https://example.invalid/model.onnx' });
    if (options.workerCreated) page.emit('worker', {});
    if (options.localPolling) page.emit('request', { method: () => 'GET', url: () => `${origin}/api/logs?limit=20&offset=0` });
    if (options.unknownLocalRead) page.emit('request', { method: () => 'GET', url: () => `${origin}/api/models/download` });
    if (options.localWrite) page.emit('request', { method: () => 'POST', url: () => `${origin}/api/settings` });
    if (!options.noImportResponse) page.emit('response', { url: request.url, request: () => request,
      status: () => 200, headerValue: async () => options.moduleCsp ? "script-src 'self'" : csp,
      body: async () => options.wrongModuleBytes ? Buffer.from('wrong executable module') : mjsBytes });
  } });
  page.evaluate = async (fn, args) => {
    assert.equal(fn, initializeOfflineOrtInRenderer);
    return fn(args, renderer.dependencies);
  };
  const owner = { dispose: async () => { disposed++; } };
  const app = { browserWindow: async (candidate) => { assert.equal(candidate, page); return owner; },
    evaluate: async (fn, args) => {
      assert.equal(fn, readOfflineOrtOwner);
      assert.equal(args.owner, owner);
      return { packaged: !options.notPackaged, resourcesPath: options.resourcesPath?.(f) ?? path.join(f.installDir, 'resources'), ownerURLMatched: true,
        ownerArgument: true, contextIsolation: true, sandbox: true, webSecurity: !options.noWebSecurity,
        nodeIntegration: false, devTools: options.devToolsMissing ? undefined : false,
        devToolsOpened: Boolean(options.devToolsOpened), devToolsContentsPresent: false };
    } };
  const beforeReload = async (phase) => {
    reloadGates.push(phase);
    if (options.unsafeReload) throw new Error('pending write or unsaved draft');
  };
  return { ...f, page, renderer, reloadGates, run: (extra = {}) => probeInstalledOfflineOrt({
    app, page, installDir: f.installDir, beforeReload, timeoutMs: 200, ...extra }),
  checkCleanup(expectedReloads = 2) {
    assert.equal(reloads, expectedReloads);
    assert.equal(disposed, 1);
    for (const name of ['request', 'response', 'worker']) assert.equal(page.listenerCount(name), 0);
  } };
}

test('orchestration harness verifies actual imported response bytes, gates reloads, and does not claim transcription', async (t) => {
  const f = await probeHarness(t);
  const result = await f.run();
  assert.equal(result.importedModuleBytesVerified, true);
  assert.equal(result.rendererReleasedByReload, true);
  assert.equal(result.whisperTranscriptionValidated, false);
  assert.equal(result.inferenceSessionCreated, false);
  assert.equal(result.modelDownloaded, false);
  assert.deepEqual(f.reloadGates, ['before-probe', 'release-probe']);
  f.checkCleanup();
});

test('orchestration binds a raw owner alias to canonical discovered resources without skipping real initialization assertions', async t => {
  let reportedResources;
  const f = await probeHarness(t, { resourcesPath: () => reportedResources });
  const alias = filesystemAlias(f);
  reportedResources = path.join(alias.aliasRoot, 'resources');
  alias.install(t);
  const result = await f.run();
  assert.equal(result.wasmCompiled, true);
  assert.equal(result.ortInitCode, 0);
  assert.equal(result.importedModuleBytesVerified, true);
  assert.ok(alias.realpathInputs.includes(reportedResources));
  assert.ok(alias.realpathInputs.includes(path.join(f.canonicalInstallDir, 'resources')));
  f.checkCleanup();
});

test('orchestration wrong-installation binding fails before any renderer reload', async t => {
  const wrong = await files(t);
  const f = await probeHarness(t, { resourcesPath: () => path.join(wrong.installDir, 'resources') });
  await assert.rejects(f.run(), /running app must use selected installation/);
  f.checkCleanup(0);
});

test('raw owner alias still reaches and rejects a late event at the canonical asset read', async t => {
  let reportedResources;
  const f = await probeHarness(t, { resourcesPath: () => reportedResources });
  const alias = filesystemAlias(f);
  reportedResources = path.join(alias.aliasRoot, 'resources');
  alias.install(t);
  const assertDelivered = duringFinalAssetRead(t, f, () => {
    f.page.emit('request', { method: () => 'GET', url: () => 'https://example.invalid/late-model.onnx' });
  });
  await assert.rejects(f.run(), /unexpected request/);
  assertDelivered();
  f.checkCleanup();
});

for (const [option, pattern] of [['noImportResponse', /actual installed module import/],
  ['wrongModuleBytes', /module byte count/], ['moduleRedirect', /must not redirect/], ['moduleCsp', /module production CSP/],
  ['unexpectedRequest', /unexpected request/], ['unknownLocalRead', /unexpected request/], ['localWrite', /unexpected request/], ['workerCreated', /must not start workers/],
  ['badInit', /initialization failed/], ['documentCsp', /active document/]]) {
  test(`orchestration ${option} fails closed and attempts safe renderer cleanup`, async (t) => {
    const f = await probeHarness(t, { [option]: true });
    await assert.rejects(f.run(), option === 'documentCsp'
      ? (error) => error instanceof AggregateError && pattern.test(error.errors[0].message) : pattern);
    f.checkCleanup();
  });
}

test('unsafe or missing reload authorization never reloads the owner', async (t) => {
  const f = await probeHarness(t, { unsafeReload: true });
  await assert.rejects(f.run(), /pending write or unsaved draft/);
  f.checkCleanup(0);
  await assert.rejects(f.run({ beforeReload: undefined }), /explicit safe-reload callback required/);
});
for (const option of ['notPackaged', 'noWebSecurity', 'devToolsOpened']) {
  test(`${option} is rejected before any renderer reload`, async (t) => {
    const f = await probeHarness(t, { [option]: true });
    await assert.rejects(f.run());
    f.checkCleanup(0);
  });
}
test('cleanup failure cannot be called successful initialization', async (t) => {
  const f = await probeHarness(t, { cleanupFails: true });
  await assert.rejects(f.run(), (error) => error instanceof AggregateError && /cleanup reload/.test(error.errors[0].message));
  f.checkCleanup();
});

test('main-process security observation is standalone and reads only existing owner preferences', () => {
  const context = vm.createContext({ process: { resourcesPath: '/installed/resources' }, args: {
    ownerURL, owner: { webContents: { isDestroyed: () => false, getURL: () => ownerURL, isDevToolsOpened: () => false,
      getLastWebPreferences: () => ({ additionalArguments: ['--offerpilot-owner'], nodeIntegration: false,
        contextIsolation: true, sandbox: true, webSecurity: true, devTools: false }) } } } });
  const value = vm.runInContext(`(${readOfflineOrtOwner.toString()})({ app: { isPackaged: true } }, args)`, context);
  assert.equal(value.ownerArgument, true);
  assert.equal(value.ownerURLMatched, true);
  assert.equal(value.webSecurity, true);
  assert.equal(value.resourcesPath, '/installed/resources');
});

test('normal same-origin API polling is separately counted and omitted Electron preference is tolerated', async (t) => {
  const f = await probeHarness(t, { localPolling: true, devToolsMissing: true });
  const result = await f.run();
  assert.equal(result.expectedLocalBackgroundRequests, 1);
  assert.equal(result.observedUnexpectedRequests, 0);
  f.checkCleanup();
});

test('renderer initializer survives Playwright serialization without module-scope dependencies', async () => {
  const f = rendererHarness();
  const context = vm.createContext({ URL, Uint8Array, AbortController, setTimeout, clearTimeout,
    args: f.args, dependencies: f.dependencies });
  const result = await vm.runInContext(`(${initializeOfflineOrtInRenderer.toString()})(args, dependencies)`, context);
  assertOfflineOrtInitialization(result, metadata);
});

// Delay an observable event until the last filesystem verification read. This
// patches Node's test-local fs dependency, not the production probe interface.
function duringFinalAssetRead(t, f, callback) {
  const original = fs.readFile;
  let wasmReads = 0;
  let delivered = false;
  t.mock.method(fs, 'readFile', async function (filename, ...args) {
    const bytes = await original.call(this, filename, ...args);
    if (filename === path.join(f.assetsDir, wasmName) && ++wasmReads === 2) {
      delivered = true;
      callback();
    }
    return bytes;
  });
  return () => assert.equal(delivered, true, 'event must occur during final installed-asset read');
}
function importResponse(f, body = async () => mjsBytes) {
  return { url: () => f.renderer.args.urls.mjs, status: () => 200,
    request: () => ({ resourceType: () => 'script', redirectedFrom: () => null }),
    headerValue: async () => csp, body };
}
for (const [kind, pattern] of [['request', /unexpected request/], ['worker', /must not start workers/],
  ['import', /one actual installed module import/]]) {
  test(`late ${kind} during final disk verification cannot pass an earlier counter assertion`, async (t) => {
    const f = await probeHarness(t);
    const assertDelivered = duringFinalAssetRead(t, f, () => {
      if (kind === 'request') f.page.emit('request', { method: () => 'GET', url: () => 'https://example.invalid/late-model.onnx' });
      else if (kind === 'worker') f.page.emit('worker', {});
      else f.page.emit('response', importResponse(f));
    });
    await assert.rejects(f.run(), pattern);
    assertDelivered();
    f.checkCleanup();
  });
}

test('a module response delivered during final disk verification must finish byte verification', async (t) => {
  const f = await probeHarness(t, { noImportResponse: true });
  let verified = false;
  const assertDelivered = duringFinalAssetRead(t, f, () => {
    f.page.emit('response', importResponse(f, async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
      verified = true;
      return mjsBytes;
    }));
  });
  const result = await f.run();
  assertDelivered();
  assert.equal(verified, true);
  assert.equal(result.observationWindow, 'initialization-through-asset-and-response-verification');
  f.checkCleanup();
});

test('a late response byte-verification failure is awaited rather than discarded', async (t) => {
  const f = await probeHarness(t, { noImportResponse: true });
  const assertDelivered = duringFinalAssetRead(t, f, () => {
    f.page.emit('response', importResponse(f, async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
      throw new Error('late module body unreadable');
    }));
  });
  await assert.rejects(f.run(), /late module body unreadable/);
  assertDelivered();
  f.checkCleanup();
});
