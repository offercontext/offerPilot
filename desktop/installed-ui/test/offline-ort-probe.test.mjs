import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash, webcrypto } from 'node:crypto';
import { createRequire } from 'node:module';
import { EventEmitter } from 'node:events';
import vm from 'node:vm';
import { nativeEffectiveCspObserver } from '../effective-csp-observer.mjs';
import { auditedArchive, nativeObserverFixture } from './fixtures/effective-csp-fixture.mjs';
import { discoverInstalledOfflineOrt, installedOfflineOrtURLs, initializeOfflineOrtInRenderer,
  assertOfflineOrtInitialization, assertInstalledResourcesIdentity, probeInstalledOfflineOrt, readOfflineOrtOwner,
  readOfflineOrtPreloadOwner, safeOfflineOrtRendererDiagnostic, offlineOrtModuleMime } from '../offline-ort-probe.mjs';

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
      if (options.compileThrows) throw options.compileThrows;
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
      if (options.importThrows) throw options.importThrows;
      if (options.importError) throw new Error('module blocked by CSP');
      options.onImport?.();
      if (options.importAfterResponseThrows) throw options.importAfterResponseThrows;
      return { default: options.missingFactory ? undefined : async (config) => {
        calls.factories++;
        calls.options = config;
        assert.equal(config.numThreads, 1);
        assert.deepEqual(config.wasmBinary, wasmBytes);
        assert.equal(config.locateFile('ort-wasm-simd-threaded.asyncify.wasm'), args.urls.wasm);
        if (options.unexpectedLocate) config.locateFile('https://example.invalid/model.onnx');
        if (options.factoryThrows) throw options.factoryThrows;
        if (options.factoryRejects) throw new Error('factory refused');
        return { calledRun: !options.notInitialized, numThreads: options.wrongThreadCount ? 2 : 1,
          HEAPU8: options.missingHeap ? undefined : new Uint8Array(65536),
          asyncInit() { if (options.asyncInitThrows) throw options.asyncInitThrows; if (options.violation) document.emit('securitypolicyviolation'); },
          _OrtCreateSession: options.missingExports ? undefined : () => { throw new Error('must not create an inference session'); },
          _OrtInit(...values) { calls.init.push(values); if (options.initThrows) throw options.initThrows; return options.badInit ? 1 : 0; } };
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
  await auditedArchive(f.canonicalInstallDir, options.archiveOverrides);
  const native = nativeObserverFixture(t, { ownerURL, moduleURL: installedOfflineOrtURLs(ownerURL, metadata).mjs,
    policy: csp, installError: options.nativeInstallError, restoreError: options.nativeRestoreError, beforeRestore: options.beforeNativeRestore });
  const page = new EventEmitter();
  let reloads = 0;
  let disposed = 0;
  const reloadGates = [];
  const reloadResponse = { status: () => 200, url: () => ownerURL,
    request: () => ({ redirectedFrom: () => null }),
    headers: () => ({ 'content-security-policy': options.documentCsp ? "script-src 'self'" : csp }),
    headerValue: async () => Object.hasOwn(options, 'rawDocumentCsp') ? options.rawDocumentCsp : csp };
  Object.assign(page, { url: () => ownerURL, reload: async () => {
    reloads++;
    if (options.cleanupFails && reloads === 2) throw new Error('reload failed');
    if (!options.noNativeDocument) native.emit('document', options.nativeDocument);
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
    if (!options.noNativeModule) native.emit('module', options.nativeModule);
    if (!options.noImportResponse) page.emit('response', { url: request.url, request: () => request,
      status: () => 200, headers: () => ({ 'content-security-policy': options.moduleCsp ? "script-src 'self'" : csp,
        'content-type': Object.hasOwn(options, 'moduleMime') ? options.moduleMime : 'text/javascript; charset=utf-8' }),
      headerValue: async () => Object.hasOwn(options, 'rawModuleCsp') ? options.rawModuleCsp : csp,
      body: options.moduleBody ?? (async () => options.wrongModuleBytes ? Buffer.from('wrong executable module') : mjsBytes) });
  } });
  page.evaluate = async (fn, args) => {
    if (fn === readOfflineOrtPreloadOwner) return Object.hasOwn(options, 'preloadOwnerRole') ? options.preloadOwnerRole : true;
    assert.equal(fn, initializeOfflineOrtInRenderer);
    return fn(args, renderer.dependencies);
  };
  const owner = { ...native.owner, dispose: async () => { disposed++; if (options.ownerDisposeError) throw options.ownerDisposeError; } };
  const app = { browserWindow: async (candidate) => { assert.equal(candidate, page); return owner; },
    evaluate: async (fn, args) => {
      if (fn === nativeEffectiveCspObserver) return native.evaluate(args);
      assert.equal(fn, readOfflineOrtOwner);
      assert.equal(args.owner, owner);
      if (options.ownerReadError) throw options.ownerReadError;
      return { packaged: !options.notPackaged, resourcesPath: options.resourcesPath?.(f) ?? path.join(f.installDir, 'resources'), ownerURLMatched: true,
        ownerArgument: !options.ownerArgumentMissing, additionalArgumentsArray: options.ownerArgumentMissing ? undefined : true,
        twoWindows: true, ownerWindowUnique: true, ownerSessionMatched: true,
        haruURLMatched: true, haruSessionMatched: true, distinctSessions: true,
        contextIsolation: true, sandbox: true, webSecurity: !options.noWebSecurity,
        nodeIntegration: false, devTools: options.devToolsMissing ? undefined : false,
        devToolsOpened: Boolean(options.devToolsOpened), devToolsContentsPresent: false, ...options.security };
    } };
  const beforeReload = async (phase) => {
    reloadGates.push(phase);
    if (options.unsafeReload) throw new Error('pending write or unsaved draft');
  };
  return { ...f, page, renderer, native, reloadGates, run: (extra = {}) => probeInstalledOfflineOrt({
    app, page, installDir: f.installDir, beforeReload, timeoutMs: 200, ...extra }),
  checkCleanup(expectedReloads = 2) {
    assert.equal(reloads, expectedReloads);
    assert.equal(disposed, 1);
    assert.equal(native.registered(), Boolean(options.nativeRestoreError));
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

test('omitted Electron 44.5.1 arguments cannot cause a false failure when real window/session and preload identity pass', async t => {
  const f = await probeHarness(t, { ownerArgumentMissing: true, devToolsMissing: true });
  const result = await f.run();
  assert.equal(result.wasmCompiled, true);
  f.checkCleanup();
});

for (const field of ['twoWindows', 'ownerWindowUnique', 'ownerSessionMatched', 'haruURLMatched', 'haruSessionMatched', 'distinctSessions']) {
  for (const value of [false, undefined]) {
    test(`actual ${field}=${value} fails even when the owner argument and preload role appear valid`, async t => {
      const f = await probeHarness(t, { security: { [field]: value } });
      await assert.rejects(f.run(), new RegExp(field));
      f.checkCleanup(0);
    });
  }
}

for (const preloadOwnerRole of [false, undefined, null, 'owner']) {
  test(`missing or malformed preload owner observation (${preloadOwnerRole}) fails real window/session checks`, async t => {
    const f = await probeHarness(t, { preloadOwnerRole, ownerArgumentMissing: true });
    await assert.rejects(f.run(), /preloadOwnerRole/);
    f.checkCleanup(0);
  });
}

for (const security of [
  { additionalArgumentsArray: true, ownerArgument: false },
  { additionalArgumentsArray: null }, { additionalArgumentsArray: 'invalid-type' },
]) {
  test('returned malformed arguments or an absent owner marker never use the omitted-getter exception', async t => {
    const f = await probeHarness(t, { security });
    await assert.rejects(f.run(), /additionalArgumentsArray|ownerArgument/);
    f.checkCleanup(0);
  });
}

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
  ['wrongModuleBytes', /module byte count/], ['moduleRedirect', /must not redirect/], ['moduleCsp', /module effective production CSP/],
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

function mainOwnerHarness() {
  const ownerSession = {};
  const haruSession = {};
  const prefs = { additionalArguments: ['--offerpilot-owner'], nodeIntegration: false,
    contextIsolation: true, sandbox: true, webSecurity: true, devTools: false };
  const owner = { isDestroyed: () => false, webContents: { session: ownerSession, isDestroyed: () => false,
    getURL: () => ownerURL, isDevToolsOpened: () => false, getLastWebPreferences: () => prefs } };
  const haru = { isDestroyed: () => false, webContents: { session: haruSession,
    isDestroyed: () => false, getURL: () => `${origin}/?desktopSurface=haru` } };
  const windows = [owner, haru];
  const runtime = { app: { isPackaged: true }, BrowserWindow: { getAllWindows: () => windows },
    session: { fromPartition: name => {
      assert.ok(['persist:offerpilot-desktop', 'offerpilot-haru'].includes(name));
      return name === 'persist:offerpilot-desktop' ? ownerSession : haruSession;
    } } };
  const context = vm.createContext({ URL, process: { resourcesPath: '/installed/resources' }, runtime, args: { ownerURL, owner } });
  return { owner, haru, windows, prefs,
    read: () => vm.runInContext(`(${readOfflineOrtOwner.toString()})(runtime, args)`, context) };
}

test('main-process security observation is standalone and binds existing real windows, sessions and preferences', () => {
  const value = mainOwnerHarness().read();
  assert.equal(value.ownerArgument, true);
  assert.equal(value.additionalArgumentsArray, true);
  assert.equal(value.ownerURLMatched, true);
  assert.equal(value.webSecurity, true);
  assert.equal(value.resourcesPath, '/installed/resources');
  for (const key of ['twoWindows', 'ownerWindowUnique', 'ownerSessionMatched', 'haruURLMatched', 'haruSessionMatched', 'distinctSessions']) {
    assert.equal(value[key], true, key);
  }
});

test('Electron 44.5.1 getter omission stays missing without fabricating an owner marker', () => {
  const f = mainOwnerHarness();
  delete f.prefs.additionalArguments;
  delete f.prefs.devTools;
  const value = f.read();
  assert.equal(value.additionalArgumentsArray, undefined);
  assert.equal(value.ownerArgument, false);
  assert.equal(value.devTools, undefined);
  assert.equal(value.ownerWindowUnique, true);
});

for (const [args, arrayState, ownerFlag] of [
  [[], true, false], [['--offerpilot-haru'], true, false],
  [['--offerpilot-owner', '--private-token=do-not-record'], true, true],
  ['--offerpilot-owner', 'invalid-type', false], [{ token: 'do-not-record' }, 'invalid-type', false],
  [null, null, false],
]) {
  test('main-process arguments diagnostic reports shape and owner presence without copying their contents', () => {
    const f = mainOwnerHarness();
    f.prefs.additionalArguments = args;
    const value = f.read();
    assert.equal(value.additionalArgumentsArray, arrayState);
    assert.equal(value.ownerArgument, ownerFlag);
    assert.doesNotMatch(JSON.stringify(value), /do-not-record|--offerpilot|private-token/);
  });
}

for (const [key, mutate] of [
  ['twoWindows', f => f.windows.push(f.haru)],
  ['ownerWindowUnique', f => { f.windows[0] = { ...f.owner }; }],
  ['ownerWindowUnique', f => { f.haru.webContents.getURL = () => ownerURL; }],
  ['ownerURLMatched', f => { f.owner.webContents.isDestroyed = () => true; }],
  ['ownerSessionMatched', f => { f.owner.webContents.session = {}; }],
  ['haruURLMatched', f => { f.haru.webContents.getURL = () => `${origin}/?desktopSurface=haru&extra=1`; }],
  ['haruURLMatched', f => { f.haru.isDestroyed = () => true; }],
  ['haruSessionMatched', f => { f.haru.webContents.session = {}; }],
  ['distinctSessions', f => { f.haru.webContents.session = f.owner.webContents.session; }],
]) {
  test(`main-process ${key} detects altered native identity instead of relying on the page role`, () => {
    const f = mainOwnerHarness();
    mutate(f);
    assert.equal(f.read()[key], false);
  });
}

test('preload role check is serializable and returns only a strict owner boolean', () => {
  for (const role of ['owner', 'haru', undefined, 'private-value', true]) {
    const context = vm.createContext({ window: { offerpilotDesktop: role === undefined ? undefined : { role } } });
    assert.equal(vm.runInContext(`(${readOfflineOrtPreloadOwner.toString()})()`, context), role === 'owner');
  }
});

const diagnosticFields = ['packaged', 'ownerURLMatched', 'twoWindows', 'ownerWindowUnique', 'ownerSessionMatched',
  'haruURLMatched', 'haruSessionMatched', 'distinctSessions', 'contextIsolation', 'sandbox', 'webSecurity',
  'nodeIntegration', 'devToolsOpened', 'devToolsContentsPresent', 'additionalArgumentsArray', 'ownerArgument',
  'devTools', 'preloadOwnerRole', 'resourcesPathsAbsolute', 'resourcesDirectoryChainsUnlinked',
  'resourcesCanonicalPathMatched', 'resourcesDirectories', 'resourcesDeviceMatched', 'resourcesFileMatched'];
function assertSafeDiagnostic(value) {
  assert.deepEqual(Object.keys(value).sort(), ['cleanup', 'effectiveCsp', 'failedCheck', 'observed', 'phase', 'primaryFailure', 'probe', 'reloads', 'renderer', 'schemaVersion']);
  assert.equal(value.probe, 'offline-ort');
  assert.equal(value.schemaVersion, 5);
  assert.ok(['owner-observation', 'owner-validation', 'owner-resources', 'document-csp', 'initialization'].includes(value.phase));
  const reloadChecks = ['not-started', 'before-reload-gate', 'reload-await', 'response-present', 'response-status',
    'response-url', 'response-redirect', 'provisional-csp-read', 'raw-csp-read', 'browser-csp-match', 'native-csp-arm', 'native-effective-csp', 'page-url', 'complete'];
  const errorCategories = [null, 'assertion', 'timeout', 'aborted', 'connection-refused', 'connection-reset',
    'target-closed', 'protocol-error', 'type-error', 'other'];
  const checks = [...diagnosticFields, ...reloadChecks, 'owner-observation', 'document-csp', 'initialization', 'diagnostic-recording', 'response-observer-source-audit', 'response-observer-install', 'native-runtime-arm', 'native-runtime-effective-csp'];
  assert.ok(value.failedCheck === null || checks.includes(value.failedCheck));
  if (value.primaryFailure !== null) {
    assert.deepEqual(Object.keys(value.primaryFailure).sort(), ['check', 'errorCategory']);
    assert.ok(checks.includes(value.primaryFailure.check));
    assert.ok(errorCategories.includes(value.primaryFailure.errorCategory));
    assert.equal(value.failedCheck, value.primaryFailure.check);
  }
  assert.deepEqual(Object.keys(value.reloads).sort(), ['beforeProbe', 'releaseProbe']);
  for (const reload of Object.values(value.reloads)) {
    assert.deepEqual(Object.keys(reload).sort(), ['check', 'errorCategory', 'observed', 'rawHeaderError', 'result']);
    assert.ok(reloadChecks.includes(reload.check));
    assert.ok(['not-run', 'running', 'passed', 'failed'].includes(reload.result));
    assert.ok(errorCategories.includes(reload.errorCategory));
    assert.ok(errorCategories.includes(reload.rawHeaderError));
    assert.deepEqual(Object.keys(reload.observed).sort(), ['pageURLMatched', 'provisionalCspMatched', 'provisionalCspPresent',
      'rawCspMatched', 'rawCspPresent', 'rawCspReadSucceeded', 'nativeCspObserved', 'nativeCspMatched', 'redirectAbsent', 'reloadResolved', 'responsePresent', 'responseStatus200', 'responseURLMatched', 'safeReloadGatePassed'].sort());
    for (const observed of Object.values(reload.observed)) assert.ok([true, false, 'not-observed'].includes(observed));
  }
  assert.deepEqual(Object.keys(value.cleanup).sort(), ['nativeObserver', 'observers', 'ownerHandle']);
  for (const [key, cleanup] of Object.entries(value.cleanup)) {
    assert.deepEqual(Object.keys(cleanup).sort(), ['check', 'errorCategory', 'result']);
    assert.equal(cleanup.check, { observers: 'observer-detach', nativeObserver: 'native-observer-restore', ownerHandle: 'owner-handle-dispose' }[key]);
    assert.ok(['not-run', 'passed', 'failed'].includes(cleanup.result));
    assert.ok(errorCategories.includes(cleanup.errorCategory));
  }
  if (value.renderer !== null) assert.deepEqual(safeOfflineOrtRendererDiagnostic(value.renderer), value.renderer);
  assert.deepEqual(Object.keys(value.effectiveCsp).sort(), ['module', 'native', 'responseObserverAuditPassed']);
  assert.equal(typeof value.effectiveCsp.responseObserverAuditPassed, 'boolean');
  const modulePolicy = value.effectiveCsp.module;
  assert.deepEqual(Object.keys(modulePolicy).sort(), ['bodyRead', 'browserCspMatched', 'browserCspPresent', 'byteCountMatched', 'mimeType', 'rawCspMatched', 'rawCspPresent', 'rawCspReadSucceeded', 'rawHeaderError', 'redirectAbsent', 'responseObserved', 'sha256Matched', 'status200', 'verificationError']);
  for (const [key, observed] of Object.entries(modulePolicy)) {
    if (key === 'mimeType') assert.ok(['not-observed', 'missing', 'invalid', 'text/javascript', 'application/javascript',
      'text/html', 'text/plain', 'application/octet-stream', 'application/json', 'legacy-javascript', 'other'].includes(observed));
    else if (key === 'bodyRead') assert.ok(['not-started', 'pending', 'completed', 'timed-out', 'failed'].includes(observed));
    else assert.ok(['rawHeaderError', 'verificationError'].includes(key) ? errorCategories.includes(observed)
      : [null, true, false, 'not-observed'].includes(observed));
  }
  const native = value.effectiveCsp.native;
  if (native !== null) {
    assert.deepEqual(Object.keys(native).sort(), ['failures', 'ownerSessionMatched', 'phase', 'records', 'registered']);
    assert.equal(typeof native.registered, 'boolean');
    assert.equal(typeof native.ownerSessionMatched, 'boolean');
    assert.ok(['inactive', 'beforeProbe', 'runtime', 'releaseProbe'].includes(native.phase));
    assert.ok(native.failures.length <= 5);
    assert.ok(native.failures.every(reason => ['unexpected-response-phase', 'duplicate-target-response',
      'request-evidence-limit', 'invalid-effective-response', 'native-response-observation-error'].includes(reason)));
    assert.deepEqual(Object.keys(native.records).sort(), ['beforeProbe', 'module', 'releaseProbe']);
    for (const record of Object.values(native.records)) {
      assert.deepEqual(Object.keys(record).sort(), ['count', 'cspMatched', 'methodMatched', 'ownerIdMatched', 'ownerObjectMatched',
        'ownerSessionMatched', 'requestIdValid', 'resourceTypeMatched', 'singleCspHeader', 'statusMatched']);
      assert.ok([0, 1, 2, 3].includes(record.count));
      for (const [key, observed] of Object.entries(record)) if (key !== 'count') assert.ok([null, true, false].includes(observed));
    }
  }
  assert.deepEqual(Object.keys(value.observed).sort(), [...diagnosticFields].sort());
  for (const observed of Object.values(value.observed)) assert.ok([true, false, 'undefined', 'null', 'invalid-type'].includes(observed));
  assert.doesNotMatch(JSON.stringify(value), /private-token|do-not-record|\/installed|offerpilot-ort-unit|ws:\/\/|127\.0\.0\.1|script-src|content-security-policy/i);
}

test('safe diagnostics are awaited, detached snapshots that distinguish missing getters from native identity', async t => {
  const f = await probeHarness(t, { ownerArgumentMissing: true, devToolsMissing: true,
    security: { rawPreferences: { token: 'private-token' }, unknownField: 'do-not-record' } });
  const snapshots = [];
  let activeCallback = false;
  const originalReload = f.page.reload;
  f.page.reload = async (...args) => {
    assert.equal(activeCallback, false, 'recording must finish before reload');
    assert.equal(snapshots.at(-1).observed.resourcesFileMatched, true);
    return originalReload(...args);
  };
  await f.run({ onDiagnostic: async diagnostic => {
    activeCallback = true;
    await new Promise(resolve => setTimeout(resolve, 1));
    assertSafeDiagnostic(diagnostic);
    snapshots.push(structuredClone(diagnostic));
    diagnostic.observed.ownerWindowUnique = 'private-token';
    activeCallback = false;
  } });
  assert.equal(snapshots[0].observed.additionalArgumentsArray, 'undefined');
  assert.equal(snapshots[0].observed.ownerArgument, false);
  assert.equal(snapshots[0].observed.devTools, 'undefined');
  assert.equal(snapshots[0].observed.resourcesFileMatched, 'undefined');
  assert.equal(snapshots.at(-1).observed.resourcesFileMatched, true);
  assert.equal(snapshots.at(-1).observed.preloadOwnerRole, true);
  assert.ok(snapshots.every(snapshot => snapshot.observed.ownerWindowUnique === true));
  f.checkCleanup();
});

test('malformed security values are sanitized and diagnostic mutation cannot change a failing assertion', async t => {
  const f = await probeHarness(t, { security: { webSecurity: { token: 'private-token' },
    devTools: 'ws://private-token', resourcesPath: 'do-not-record' } });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: async diagnostic => {
    assertSafeDiagnostic(diagnostic);
    latest = structuredClone(diagnostic);
    diagnostic.observed.webSecurity = true;
  } }), /webSecurity/);
  assert.equal(latest.failedCheck, 'webSecurity');
  assert.equal(latest.observed.webSecurity, 'invalid-type');
  assert.equal(latest.observed.devTools, 'invalid-type');
  f.checkCleanup(0);
});

test('resources diagnostics localize actual identity rejection without retaining either path or raw failure', async t => {
  const wrong = await files(t);
  const f = await probeHarness(t, { resourcesPath: () => path.join(wrong.installDir, 'resources') });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: async diagnostic => {
    assertSafeDiagnostic(diagnostic);
    latest = diagnostic;
  } }), /selected installation/);
  assert.equal(latest.phase, 'owner-resources');
  assert.equal(latest.failedCheck, 'resourcesCanonicalPathMatched');
  assert.equal(latest.observed.resourcesPathsAbsolute, true);
  assert.equal(latest.observed.resourcesDirectoryChainsUnlinked, true);
  assert.equal(latest.observed.resourcesCanonicalPathMatched, false);
  assert.equal(latest.observed.resourcesDirectories, 'undefined');
  f.checkCleanup(0);
});

test('native owner read errors expose only the fixed observation failure name', async t => {
  const f = await probeHarness(t, { ownerReadError: new Error('private-token ws://do-not-record') });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: async diagnostic => {
    assertSafeDiagnostic(diagnostic);
    latest = diagnostic;
  } }));
  assert.equal(latest.failedCheck, 'owner-observation');
  assert.ok(Object.values(latest.observed).every(value => value === 'undefined'));
  f.checkCleanup(0);
});

test('failed diagnostic recording cannot suppress a primary security failure or prevent cleanup', async t => {
  const f = await probeHarness(t, { noWebSecurity: true });
  await assert.rejects(f.run({ onDiagnostic: async () => { throw new Error('private-token ws://do-not-record'); } }), error => {
    assert.ok(error instanceof AggregateError);
    assert.match(error.errors[0].message, /webSecurity/);
    assert.equal(error.errors[1].message, 'ORT diagnostic recording failed');
    assert.equal(error.errors.length, 2);
    return true;
  });
  f.checkCleanup(0);
});

for (const failingPhase of ['owner-observation', 'initialization']) {
  test(`diagnostic recording failure at ${failingPhase} fails closed and releases owned handles`, async t => {
    const f = await probeHarness(t);
    let latest;
    await assert.rejects(f.run({ onDiagnostic: async diagnostic => {
      assertSafeDiagnostic(diagnostic);
      latest = diagnostic;
      if (diagnostic.phase === failingPhase) throw new Error('private-token');
    } }), /ORT diagnostic recording failed/);
    assert.equal(latest.failedCheck, 'diagnostic-recording');
    assert.equal(f.renderer.calls.imports, 0);
    f.checkCleanup(failingPhase === 'initialization' ? 2 : 0);
  });
}

test('normal same-origin API polling is separately counted and omitted Electron preference is tolerated', async (t) => {
  const f = await probeHarness(t, { localPolling: true, devToolsMissing: true });
  const result = await f.run();
  assert.equal(result.expectedLocalBackgroundRequests, 1);
  assert.equal(result.observedUnexpectedRequests, 0);
  f.checkCleanup();
});

test('document CSP diagnostics fail closed on effective browser policy mismatch', async t => {
  const f = await probeHarness(t, { documentCsp: true });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), AggregateError);
  assert.equal(latest.phase, 'document-csp');
  assert.equal(latest.failedCheck, 'browser-csp-match');
  assert.deepEqual(latest.primaryFailure, { check: 'browser-csp-match', errorCategory: 'assertion' });
  for (const reload of Object.values(latest.reloads)) {
    assert.equal(reload.check, 'browser-csp-match');
    assert.equal(reload.result, 'failed');
    assert.equal(reload.errorCategory, 'assertion');
    assert.equal(reload.observed.rawCspPresent, 'not-observed');
    assert.equal(reload.observed.rawCspMatched, 'not-observed');
    assert.equal(reload.observed.provisionalCspPresent, true);
    assert.equal(reload.observed.provisionalCspMatched, false);
  }
  assert.equal(latest.cleanup.ownerHandle.result, 'passed');
  f.checkCleanup();
});

test('successful reload diagnostics include both header views and resist callback mutation', async t => {
  const f = await probeHarness(t);
  let latest;
  await f.run({ onDiagnostic: value => {
    assertSafeDiagnostic(value);
    latest = structuredClone(value);
    value.reloads.beforeProbe.observed.rawCspMatched = 'private-token';
    value.reloads.releaseProbe.check = 'private-token';
    value.cleanup.ownerHandle.result = 'private-token';
  } });
  assert.equal(latest.primaryFailure, null);
  assert.equal(latest.failedCheck, null);
  for (const reload of Object.values(latest.reloads)) {
    assert.equal(reload.check, 'complete');
    assert.equal(reload.result, 'passed');
    assert.ok(Object.values(reload.observed).every(value => value === true));
  }
  assert.equal(latest.cleanup.observers.result, 'passed');
  assert.equal(latest.cleanup.ownerHandle.result, 'passed');
  f.checkCleanup();
});

test('unsafe reload diagnostic identifies the gate without attempting either reload', async t => {
  const f = await probeHarness(t, { unsafeReload: true });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }));
  assert.deepEqual(latest.primaryFailure, { check: 'before-reload-gate', errorCategory: 'other' });
  assert.equal(latest.reloads.beforeProbe.result, 'failed');
  assert.equal(latest.reloads.beforeProbe.observed.reloadResolved, 'not-observed');
  assert.equal(latest.reloads.releaseProbe.result, 'not-run');
  f.checkCleanup(0);
});

test('missing effective browser CSP fails even if upstream raw CSP matches', async t => {
  const f = await probeHarness(t);
  const originalReload = f.page.reload;
  f.page.reload = async (...args) => ({ ...await originalReload(...args), headers: () => ({}) });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }));
  for (const reload of Object.values(latest.reloads)) {
    assert.equal(reload.observed.provisionalCspPresent, false);
    assert.equal(reload.observed.provisionalCspMatched, false);
    assert.equal(reload.observed.rawCspPresent, 'not-observed');
    assert.equal(reload.observed.rawCspMatched, 'not-observed');
  }
  f.checkCleanup();
});

test('diagnostic callbacks cannot intervene after either safe-reload gate', async t => {
  const f = await probeHarness(t);
  const originalReload = f.page.reload;
  let callbackCount = 0;
  let gateCallbackCount;
  f.page.reload = async (...args) => {
    assert.equal(callbackCount, gateCallbackCount);
    return originalReload(...args);
  };
  await f.run({ beforeReload: async () => { gateCallbackCount = callbackCount; },
    onDiagnostic: async value => { assertSafeDiagnostic(value); callbackCount++; await Promise.resolve(); } });
  f.checkCleanup();
});

for (const [message, expected] of [
  ['Timeout 60000ms exceeded private-token', 'timeout'],
  ['net::ERR_ABORTED private-token', 'aborted'],
  ['net::ERR_CONNECTION_REFUSED private-token', 'connection-refused'],
  ['read ECONNRESET private-token', 'connection-reset'],
  ['Target page, context or browser has been closed private-token', 'target-closed'],
  ['Protocol error (Page.reload): private-token', 'protocol-error'],
  ['unclassified private-token ws://do-not-record', 'other'],
]) {
  test(`reload API failure exposes only the fixed ${expected} category`, async t => {
    const f = await probeHarness(t);
    const originalReload = f.page.reload;
    let calls = 0;
    f.page.reload = async (...args) => {
      const response = await originalReload(...args);
      if (++calls === 1) throw new Error(message);
      return response;
    };
    let latest;
    await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }));
    assert.deepEqual(latest.primaryFailure, { check: 'reload-await', errorCategory: expected });
    assert.equal(latest.reloads.releaseProbe.result, 'passed');
    f.checkCleanup();
  });
}

for (const [check, alter] of [
  ['response-status', response => ({ ...response, status: () => { throw new TypeError('private-token'); } })],
  ['response-url', response => ({ ...response, url: () => { throw new TypeError('private-token'); } })],
  ['response-redirect', response => ({ ...response, request: () => { throw new TypeError('private-token'); } })],
  ['response-redirect', response => ({ ...response, request: () => ({ redirectedFrom: () => { throw new TypeError('private-token'); } }) })],
]) {
  test(`response accessor rejection remains localized to ${check}`, async t => {
    const f = await probeHarness(t);
    const originalReload = f.page.reload;
    let calls = 0;
    f.page.reload = async (...args) => {
      const response = await originalReload(...args);
      return ++calls === 1 ? alter(response) : response;
    };
    let latest;
    await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), TypeError);
    assert.deepEqual(latest.primaryFailure, { check, errorCategory: 'type-error' });
    assert.equal(latest.reloads.releaseProbe.result, 'passed');
    f.checkCleanup();
  });
}

test('post-reload page URL mismatch keeps its own check when cleanup is safe', async t => {
  const f = await probeHarness(t);
  const originalReload = f.page.reload;
  let calls = 0;
  f.page.reload = async (...args) => {
    const response = await originalReload(...args);
    if (++calls === 1) {
      let firstRead = true;
      f.page.url = () => {
        if (firstRead) { firstRead = false; return `${ownerURL}&private-token=do-not-record`; }
        return ownerURL;
      };
    }
    return response;
  };
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }));
  assert.deepEqual(latest.primaryFailure, { check: 'page-url', errorCategory: 'assertion' });
  assert.equal(latest.reloads.beforeProbe.observed.pageURLMatched, false);
  assert.equal(latest.reloads.releaseProbe.result, 'passed');
  f.checkCleanup();
});

test('cleanup gate failure preserves a successful primary probe without claiming cleanup passed', async t => {
  const f = await probeHarness(t);
  let latest;
  await assert.rejects(f.run({ beforeReload: async kind => {
    if (kind === 'release-probe') throw new Error('private-token unsaved editing');
  }, onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), AggregateError);
  assert.equal(latest.primaryFailure, null);
  assert.equal(latest.failedCheck, null);
  assert.equal(latest.phase, 'initialization');
  assert.equal(latest.reloads.beforeProbe.result, 'passed');
  assert.equal(latest.reloads.releaseProbe.check, 'before-reload-gate');
  assert.equal(latest.reloads.releaseProbe.result, 'failed');
  f.checkCleanup(1);
});

test('cleanup observer and handle errors remain separate from the original CSP rejection', async t => {
  const f = await probeHarness(t, { documentCsp: true,
    ownerDisposeError: new Error('Protocol error private-token ws://do-not-record') });
  const removeListener = f.page.removeListener;
  let rejected = false;
  f.page.removeListener = function (...args) {
    const result = removeListener.apply(this, args);
    if (!rejected) { rejected = true; throw new TypeError('private-token'); }
    return result;
  };
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), error => {
    assert.ok(error instanceof AggregateError);
    assert.equal(error.errors[0].name, 'AssertionError');
    assert.equal(error.errors.length, 4);
    return true;
  });
  assert.deepEqual(latest.primaryFailure, { check: 'browser-csp-match', errorCategory: 'assertion' });
  assert.deepEqual(latest.cleanup.observers, { check: 'observer-detach', result: 'failed', errorCategory: 'type-error' });
  assert.deepEqual(latest.cleanup.ownerHandle, { check: 'owner-handle-dispose', result: 'failed', errorCategory: 'protocol-error' });
  f.checkCleanup();
});

test('a failed final diagnostic callback still fails closed after all cleanup is attempted', async t => {
  const f = await probeHarness(t, { documentCsp: true });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => {
    assertSafeDiagnostic(value);
    latest = value;
    if (value.cleanup.ownerHandle.result === 'passed') throw new Error('private-token');
  } }), error => {
    assert.ok(error instanceof AggregateError);
    assert.equal(error.errors[0].name, 'AssertionError');
    assert.equal(error.errors.at(-1).message, 'ORT diagnostic recording failed');
    return true;
  });
  assert.deepEqual(latest.primaryFailure, { check: 'browser-csp-match', errorCategory: 'assertion' });
  assert.equal(latest.reloads.releaseProbe.result, 'failed');
  assert.equal(latest.cleanup.ownerHandle.result, 'passed');
  f.checkCleanup();
});

for (const [check, fault] of [
  ['reload-await', () => { throw new Error('Protocol error: private-token ws://do-not-record'); }],
  ['response-present', () => null],
  ['response-status', response => ({ ...response, status: () => 503 })],
  ['response-url', response => ({ ...response, url: () => 'http://private-token/do-not-record' })],
  ['response-redirect', response => ({ ...response, request: () => ({ redirectedFrom: () => ({ private: 'private-token' }) }) })],
  ['provisional-csp-read', response => ({ ...response, headers: () => { throw new Error('Protocol error: private-token'); } })],
  ['browser-csp-match', response => ({ ...response, headers: () => ({}) })],
]) {
  test(`reload diagnostics distinguish ${check} and retain primary when cleanup also fails`, async t => {
    const f = await probeHarness(t, { cleanupFails: true });
    const originalReload = f.page.reload;
    let calls = 0;
    f.page.reload = async (...args) => {
      const response = await originalReload(...args);
      return ++calls === 1 ? fault(response) : response;
    };
    let latest;
    await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), AggregateError);
    assert.equal(latest.failedCheck, check);
    assert.equal(latest.primaryFailure.check, check);
    assert.equal(latest.reloads.beforeProbe.check, check);
    assert.equal(latest.reloads.releaseProbe.check, 'reload-await');
    assert.equal(latest.reloads.releaseProbe.errorCategory, 'other');
    assert.equal(latest.cleanup.ownerHandle.result, 'passed');
    f.checkCleanup();
  });
}

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
    headers: () => ({ 'content-security-policy': csp }), headerValue: async () => csp, body };
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

for (const raw of [null, "upstream-policy-different-from-effective-policy"]) {
  test(`upstream raw policy ${raw === null ? 'absence' : 'difference'} is diagnostic when both effective channels match`, async t => {
    const f = await probeHarness(t, { rawDocumentCsp: raw, rawModuleCsp: raw });
    let latest;
    const result = await f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } });
    assert.deepEqual(result.effectiveCspVerifiedBy, ['browser-response-headers', 'electron-post-override-onResponseStarted']);
    assert.equal(result.upstreamRawCspIsDiagnosticOnly, true);
    assert.equal(result.nativeResponseObserverRestored, true);
    for (const reload of Object.values(latest.reloads)) {
      assert.equal(reload.observed.rawCspMatched, false);
      assert.equal(reload.observed.rawCspPresent, raw !== null);
      assert.equal(reload.observed.provisionalCspMatched, true);
      assert.equal(reload.observed.nativeCspMatched, true);
      assert.equal(reload.result, 'passed');
    }
    assert.equal(latest.effectiveCsp.module.rawCspMatched, false);
    f.checkCleanup();
  });
}
for (const fault of ['reject', 'pending']) {
  test(`raw header ${fault} remains a bounded diagnostic and cannot replace either effective proof`, async t => {
    const f = await probeHarness(t);
    const original = f.page.reload;
    f.page.reload = async (...args) => ({ ...await original(...args), headerValue: () => fault === 'pending'
      ? new Promise(() => {}) : Promise.reject(new Error('net::ERR_ABORTED private-token')) });
    let latest;
    await f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } });
    for (const reload of Object.values(latest.reloads)) {
      assert.equal(reload.observed.rawCspReadSucceeded, false);
      assert.equal(reload.rawHeaderError, fault === 'pending' ? 'timeout' : 'aborted');
      assert.equal(reload.observed.nativeCspMatched, true);
    }
    f.checkCleanup();
  });
}
for (const [name, options] of [
  ['missing document', { noNativeDocument: true }], ['missing module', { noNativeModule: true }],
  ['wrong document owner', { nativeDocument: { webContentsId: 99 } }],
  ['wrong module owner', { nativeModule: { webContentsId: 99 } }],
  ['missing native document CSP', { nativeDocument: { responseHeaders: {} } }],
  ['missing native module CSP', { nativeModule: { responseHeaders: {} } }],
  ['different native document CSP', { nativeDocument: { responseHeaders: { 'Content-Security-Policy': ["script-src 'self'"] } } }],
  ['different native module CSP', { nativeModule: { responseHeaders: { 'Content-Security-Policy': ["script-src 'self'"] } } }],
]) {
  test(`${name} prevents PASS despite exact browser policy`, async t => {
    const f = await probeHarness(t, options);
    await assert.rejects(f.run());
    f.checkCleanup();
  });
}
test('late native event at final restoration fails despite earlier successful observations', async t => {
  const f = await probeHarness(t, { beforeNativeRestore: emit => emit('module') });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), AggregateError);
  assert.equal(latest.primaryFailure, null);
  assert.equal(latest.cleanup.nativeObserver.result, 'failed');
  assert.ok(latest.effectiveCsp.native.failures.includes('unexpected-response-phase'));
  f.checkCleanup();
});
test('native observer unregister rejection cannot be hidden by successful initialization', async t => {
  const f = await probeHarness(t, { nativeRestoreError: new Error('Protocol error private-token') });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), AggregateError);
  assert.equal(latest.primaryFailure, null);
  assert.deepEqual(latest.cleanup.nativeObserver, { check: 'native-observer-restore', result: 'failed', errorCategory: 'protocol-error' });
  f.checkCleanup();
});
test('changed installed product source fails the audit before any listener or reload can be installed', async t => {
  const f = await probeHarness(t, { archiveOverrides: { 'main.cjs': '// changed source cannot own this probe' } });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), /audited product/);
  assert.equal(latest.failedCheck, 'response-observer-source-audit');
  assert.equal(latest.effectiveCsp.responseObserverAuditPassed, false);
  assert.deepEqual(f.native.registrations, []);
  f.checkCleanup(0);
});
test('partial native registration failure is cleaned while preserving its original failure', async t => {
  const f = await probeHarness(t, { nativeInstallError: new Error('Protocol error private-token') });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), /Protocol error/);
  assert.deepEqual(latest.primaryFailure, { check: 'response-observer-install', errorCategory: 'protocol-error' });
  assert.equal(latest.cleanup.nativeObserver.result, 'passed');
  assert.equal(latest.effectiveCsp.native.registered, false);
  f.checkCleanup(0);
});
test('a late duplicate native module response during final asset verification remains sticky FAIL', async t => {
  const f = await probeHarness(t);
  const delivered = duringFinalAssetRead(t, f, () => f.native.emit('module'));
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }));
  delivered();
  assert.equal(latest.failedCheck, 'native-runtime-effective-csp');
  assert.ok(latest.effectiveCsp.native.failures.includes('duplicate-target-response'));
  assert.equal(latest.effectiveCsp.native.records.module.count, 2);
  f.checkCleanup();
});

const rendererStageCases = [
  ['fetchError', 'wasm-fetch', 'error'], ['httpFailure', 'wasm-response', 'check-rejected'],
  ['wrongCsp', 'wasm-csp', 'check-rejected'], ['corruptBytes', 'wasm-sha256', 'check-rejected'],
  ['compileError', 'wasm-compile', 'error'], ['fakeCompilation', 'wasm-compile', 'check-rejected'],
  ['importError', 'module-import', 'error'], ['missingFactory', 'factory-export', 'check-rejected'],
  ['factoryRejects', 'factory-invoke', 'error'], ['unexpectedLocate', 'factory-invoke', 'check-rejected'],
  ['notInitialized', 'factory-ready', 'check-rejected'], ['wrongThreadCount', 'factory-ready', 'check-rejected'],
  ['missingHeap', 'heap-check', 'check-rejected'], ['missingExports', 'exports-check', 'check-rejected'],
  ['badInit', 'ort-init', 'check-rejected'], ['violation', 'csp-final', 'check-rejected'],
];
for (const [option, phase, category] of rendererStageCases) {
  test(`renderer diagnostic localizes ${option} without turning rejection into success`, async () => {
    const f = rendererHarness({ [option]: true });
    f.args.captureDiagnostic = true;
    const packet = await f.run();
    assert.equal(packet.rendererFailure, true);
    assert.equal(packet.rendererDiagnostic.phase, phase);
    assert.equal(packet.rendererDiagnostic.errorCategory, category);
    assert.deepEqual(safeOfflineOrtRendererDiagnostic(packet.rendererDiagnostic), packet.rendererDiagnostic);
    assert.throws(() => assertOfflineOrtInitialization(packet, metadata));
    assert.equal(f.document.listenerCount('securitypolicyviolation'), 0);
  });
}
for (const [option, error, phase, category] of [
  ['compileThrows', new WebAssembly.CompileError('private-token'), 'wasm-compile', 'wasm-compile-error'],
  ['importThrows', new TypeError('private-token do-not-record'), 'module-import', 'type-error'],
  ['importThrows', new WebAssembly.CompileError('private-token'), 'module-import', 'wasm-compile-error'],
  ['importThrows', new WebAssembly.LinkError('private-token'), 'module-import', 'wasm-link-error'],
  ['importThrows', new WebAssembly.RuntimeError('private-token'), 'module-import', 'wasm-runtime-error'],
  ['importThrows', new EvalError('private-token unsafe-eval'), 'module-import', 'eval-error'],
  ['factoryThrows', new WebAssembly.LinkError('private-token'), 'factory-invoke', 'wasm-link-error'],
  ['factoryThrows', new WebAssembly.RuntimeError('private-token'), 'factory-invoke', 'wasm-runtime-error'],
  ['asyncInitThrows', new TypeError('private-token'), 'async-init', 'type-error'],
  ['initThrows', new WebAssembly.RuntimeError('private-token'), 'ort-init', 'wasm-runtime-error'],
  ['initThrows', new RangeError('private-token'), 'ort-init', 'range-error'],
  ['factoryThrows', { name: '__proto__', message: 'private-token' }, 'factory-invoke', 'other'],
  ['factoryThrows', { get name() { throw new Error('private-token'); } }, 'factory-invoke', 'other'],
]) {
  test(`renderer ${phase} records only fixed ${category} and never raw error data`, async () => {
    const f = rendererHarness({ [option]: error });
    f.args.captureDiagnostic = true;
    const packet = await f.run();
    assert.equal(packet.rendererFailure, true);
    const diagnostic = packet.rendererDiagnostic;
    assert.equal(diagnostic.phase, phase);
    assert.equal(diagnostic.errorCategory, category);
    assert.equal(diagnostic.observed.wasmCompiled, phase === 'wasm-compile' ? null : true);
    assert.deepEqual(safeOfflineOrtRendererDiagnostic(diagnostic), diagnostic);
    assert.doesNotMatch(JSON.stringify(packet), /private-token|do-not-record|unsafe-eval|\.mjs|stack|message|127\.0\.0\.1/);
  });
}
test('renderer success diagnostic distinguishes compile, factory, async init and ORT environment checks', async () => {
  const f = rendererHarness(); f.args.captureDiagnostic = true;
  const result = await f.run();
  assertOfflineOrtInitialization(result, metadata);
  const diagnostic = result.rendererDiagnostic;
  assert.deepEqual(safeOfflineOrtRendererDiagnostic(diagnostic), diagnostic);
  assert.equal(diagnostic.phase, 'complete');
  assert.equal(diagnostic.outcome, 'passed');
  assert.equal(diagnostic.errorCategory, null);
  for (const field of ['wasmCompiled', 'moduleImported', 'factoryResolved', 'asyncInitCompleted', 'ortInitReturnedZero']) {
    assert.equal(diagnostic.observed[field], true);
  }
  assert.equal(diagnostic.observed.cspViolationObserved, false);
  assert.equal(typeof diagnostic.observed.sharedArrayBufferAvailable, 'boolean');
  assert.ok([null, false, true].includes(diagnostic.observed.crossOriginIsolated));
});
test('renderer timeout retains its fixed active stage and aborts without serializing a rejected exception', async () => {
  const f = rendererHarness({ fetchHangs: true }); f.args.captureDiagnostic = true; f.args.timeoutMs = 10;
  const packet = await f.run();
  assert.equal(packet.rendererFailure, true);
  assert.equal(packet.rendererDiagnostic.phase, 'wasm-fetch');
  assert.equal(packet.rendererDiagnostic.errorCategory, 'timeout');
  assert.equal(packet.rendererDiagnostic.observed.wasmCompiled, null);
  assert.equal(f.calls.fetch.config.signal.aborted, true);
  assert.equal(f.document.listenerCount('securitypolicyviolation'), 0);
});
test('renderer diagnostic preflight fails before fetch and never retains the invalid URL', async () => {
  const f = rendererHarness(); f.args.captureDiagnostic = true; f.args.urls.mjs = 'https://example.invalid/private-token';
  const packet = await f.run();
  assert.equal(packet.rendererDiagnostic.phase, 'asset-url-validation');
  assert.equal(packet.rendererDiagnostic.observed.assetURLsMatched, false);
  assert.equal(f.calls.fetch, undefined);
  assert.doesNotMatch(JSON.stringify(packet), /private-token|example\.invalid/);
});
test('diagnostic sanitizer drops private extras and rejects malformed or incomplete success evidence', async () => {
  const f = rendererHarness(); f.args.captureDiagnostic = true;
  const valid = (await f.run()).rendererDiagnostic;
  const extra = { ...valid, rawError: 'private-token', observed: { ...valid.observed, url: 'private-token' } };
  assert.deepEqual(safeOfflineOrtRendererDiagnostic(extra), valid);
  for (const patch of [{ schemaVersion: 2 }, { phase: 'private-token' }, { errorCategory: 'private-token' },
    { outcome: 'unknown' }, { observed: {} }, { observed: { ...valid.observed, wasmCompiled: 'true' } },
    { observed: { ...valid.observed, wasmCompiled: null } }, { observed: { ...valid.observed, cspViolationObserved: true } },
    { outcome: 'failed' }, { phase: 'module-import' }]) {
    assert.equal(safeOfflineOrtRendererDiagnostic({ ...valid, ...patch }), null);
  }
});
test('orchestration retains renderer failure stage through cleanup and preserves FAIL', async t => {
  const f = await probeHarness(t, { factoryThrows: new WebAssembly.LinkError('private-token ws://do-not-record') });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }),
    /installed ORT initialization failed at factory-invoke/);
  assert.equal(latest.failedCheck, 'initialization');
  assert.equal(latest.renderer.phase, 'factory-invoke');
  assert.equal(latest.renderer.errorCategory, 'wasm-link-error');
  assert.equal(latest.renderer.observed.wasmCompiled, true);
  assert.equal(latest.renderer.observed.moduleImported, true);
  assert.equal(latest.renderer.observed.factoryResolved, null);
  assert.equal(latest.cleanup.nativeObserver.result, 'passed');
  f.checkCleanup();
});
test('serialized renderer failure carries only fixed diagnostic data across the Playwright boundary', async () => {
  const f = rendererHarness({ importThrows: new TypeError('private-token') }); f.args.captureDiagnostic = true;
  const context = vm.createContext({ URL, Uint8Array, AbortController, setTimeout, clearTimeout, args: f.args, dependencies: f.dependencies });
  const result = await vm.runInContext(`(${initializeOfflineOrtInRenderer.toString()})(args, dependencies)`, context);
  assert.equal(result.rendererFailure, true);
  assert.equal(safeOfflineOrtRendererDiagnostic(result.rendererDiagnostic).phase, 'module-import');
  assert.doesNotMatch(JSON.stringify(result), /private-token/);
});
test('diagnostic callback mutation cannot alter renderer success evidence or a renderer rejection', async t => {
  for (const options of [{}, { badInit: true }]) {
    const f = await probeHarness(t, options);
    let latest;
    const run = f.run({ onDiagnostic: value => {
      assertSafeDiagnostic(value);
      latest = structuredClone(value);
      if (value.renderer) {
        value.renderer.phase = 'private-token';
        value.renderer.outcome = 'passed';
        value.renderer.observed.ortInitReturnedZero = true;
      }
    } });
    if (options.badInit) {
      await assert.rejects(run, /initialization failed at ort-init/);
      assert.equal(latest.renderer.observed.ortInitReturnedZero, false);
      assert.equal(latest.renderer.outcome, 'failed');
    } else {
      const result = await run;
      assert.equal(result.rendererDiagnostic.phase, 'complete');
      assert.equal(result.rendererDiagnostic.observed.ortInitReturnedZero, true);
    }
    f.checkCleanup();
  }
});

for (const [input, expected] of [
  [undefined, 'missing'], [null, 'missing'], ['', 'invalid'], [42, 'invalid'], ['x'.repeat(513), 'invalid'],
  ['text/javascript; charset=utf-8', 'text/javascript'], [' Application/JavaScript ; charset=UTF-8', 'application/javascript'],
  ['application/x-javascript', 'legacy-javascript'], ['text/javascript1.5', 'legacy-javascript'],
  ['text/html; charset=utf-8', 'text/html'], ['text/plain', 'text/plain'],
  ['application/octet-stream', 'application/octet-stream'], ['application/json', 'application/json'],
  ['private-token/unknown; source=do-not-record', 'other'], [' ; private-token', 'invalid'],
]) {
  test(`module MIME observation maps ${expected} to an allowlisted category`, () => {
    assert.equal(offlineOrtModuleMime(input), expected);
  });
}
for (const mimeType of ['text/plain', 'application/octet-stream', 'text/html', undefined, 'private-token/unknown']) {
  test(`module import rejection preserves safe MIME ${mimeType === undefined ? 'missing' : offlineOrtModuleMime(mimeType)} evidence`, async t => {
    const f = await probeHarness(t, { moduleMime: mimeType,
      importAfterResponseThrows: new TypeError('private-token strict module loading failure') });
    let latest;
    await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }),
      /initialization failed at module-import/);
    assert.equal(latest.renderer.errorCategory, 'type-error');
    assert.equal(latest.renderer.observed.moduleImported, null);
    assert.equal(latest.effectiveCsp.module.responseObserved, true);
    assert.equal(latest.effectiveCsp.module.status200, true);
    assert.equal(latest.effectiveCsp.module.redirectAbsent, true);
    assert.equal(latest.effectiveCsp.module.mimeType, offlineOrtModuleMime(mimeType));
    assert.equal(latest.effectiveCsp.module.bodyRead, 'completed');
    assert.equal(latest.effectiveCsp.module.byteCountMatched, true);
    assert.equal(latest.effectiveCsp.module.sha256Matched, true);
    assert.equal(latest.effectiveCsp.module.verificationError, null);
    assert.equal(f.renderer.calls.factories, 0);
    f.checkCleanup();
  });
}
test('the exact already-observed response completes body verification before failed-import cleanup reload', async t => {
  let bodyCompleted = false;
  const f = await probeHarness(t, { moduleBody: async () => {
    await new Promise(resolve => setTimeout(resolve, 20)); bodyCompleted = true; return mjsBytes;
  }, importAfterResponseThrows: new TypeError('private-token') });
  const originalReload = f.page.reload;
  let reloads = 0;
  f.page.reload = (...args) => {
    if (++reloads === 2) assert.equal(bodyCompleted, true, 'cleanup must not discard the response body');
    return originalReload(...args);
  };
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }));
  assert.equal(latest.effectiveCsp.module.bodyRead, 'completed');
  assert.equal(latest.effectiveCsp.module.sha256Matched, true);
  f.checkCleanup();
});
test('HTML fallback bytes stay a mismatch even when renderer import already failed', async t => {
  const f = await probeHarness(t, { moduleMime: 'text/html', moduleBody: async () => Buffer.from('<!doctype html>synthetic fallback'),
    importAfterResponseThrows: new TypeError('private-token') });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), /failed at module-import/);
  assert.equal(latest.effectiveCsp.module.mimeType, 'text/html');
  assert.equal(latest.effectiveCsp.module.byteCountMatched, false);
  assert.equal(latest.effectiveCsp.module.sha256Matched, false);
  assert.equal(latest.effectiveCsp.module.verificationError, 'assertion');
  assert.equal(latest.renderer.phase, 'module-import');
  f.checkCleanup();
});
for (const [bodyRead, moduleBody, expectedError] of [
  ['failed', async () => { throw new Error('Protocol error private-token'); }, 'protocol-error'],
  ['timed-out', () => new Promise(() => {}), 'timeout'],
]) {
  test(`already-observed module body ${bodyRead} remains bounded and cannot suppress the renderer rejection`, async t => {
    const f = await probeHarness(t, { moduleBody, importAfterResponseThrows: new TypeError('private-token') });
    let latest;
    await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), /failed at module-import/);
    assert.equal(latest.effectiveCsp.module.bodyRead, bodyRead);
    assert.equal(latest.effectiveCsp.module.verificationError, expectedError);
    assert.equal(latest.effectiveCsp.module.byteCountMatched, null);
    assert.equal(latest.effectiveCsp.module.sha256Matched, null);
    assert.equal(latest.renderer.phase, 'module-import');
    assert.equal(latest.cleanup.nativeObserver.result, 'passed');
    f.checkCleanup();
  });
}
test('a body timeout also prevents otherwise-successful synthetic initialization from passing', async t => {
  const f = await probeHarness(t, { moduleBody: () => new Promise(() => {}) });
  let latest;
  await assert.rejects(f.run({ onDiagnostic: value => { assertSafeDiagnostic(value); latest = value; } }), /response body timed out/);
  assert.equal(latest.effectiveCsp.module.bodyRead, 'timed-out');
  assert.equal(latest.effectiveCsp.module.verificationError, 'timeout');
  f.checkCleanup();
});
