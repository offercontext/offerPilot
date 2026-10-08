import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { auditInstalledResponseObserver, nativeEffectiveCspObserver, assertEffectiveCspObservation } from '../effective-csp-observer.mjs';
import { auditedArchive, nativeObserverFixture } from './fixtures/effective-csp-fixture.mjs';
const { contentSecurityPolicy: policy } = createRequire(import.meta.url)('../../capabilities.cjs');
const ownerURL = 'http://127.0.0.1:18420/?view=settings';
const moduleURL = 'http://127.0.0.1:18420/assets/ort-wasm-simd-threaded.asyncify-test.mjs';
const fixture = (t, options = {}) => nativeObserverFixture(t, { ownerURL, moduleURL, policy, ...options });
async function installation(t, overrides) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-response-audit-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await auditedArchive(root, overrides);
  return root;
}

test('installed ASAR audit verifies the exact production entrypoint and complete unoccupied module closure', async t => {
  const root = await installation(t);
  assert.deepEqual(await auditInstalledResponseObserver(root), {
    auditedProductCommit: '590291ce4e33407eb4f13f092298e7398aed394c', verifiedModuleCount: 12,
    mainEntryMatched: true, responseStartedUnused: true,
  });
});
for (const overrides of [{ 'main.cjs': '// changed' }, { 'haru.cjs': "session.webRequest.onResponseStarted(() => {});" },
  ...['updater.cjs', 'update-safety.cjs', 'update-backup.cjs', 'update-install.cjs', 'update-signature.cjs', 'update-integrity.cjs'].map(name => ({ [name]: '// changed updater source' })),
  { metadata: { main: 'another.cjs' } }, { metadata: { name: 'different-product' } }, { metadata: { version: 'future' } }]) {
  test(`altered installed ${Object.keys(overrides)[0]} prevents native observer installation`, async t => {
    await assert.rejects(auditInstalledResponseObserver(await installation(t, overrides)));
  });
}
// Model a Windows short-name lookup on every platform without creating a link.
// Only the input spelling is mapped; realpath still reads the actual directory.
function resourceAlias(t, root, canonicalResources) {
  const aliasRoot = path.join(path.dirname(root), `CSP~1-${path.basename(root)}`);
  const aliasResources = path.join(aliasRoot, 'resources');
  assert.notEqual(aliasResources, canonicalResources);
  const original = fs.realpath;
  let observed = 0;
  t.mock.method(fs, 'realpath', async (file, ...args) => {
    if (file === aliasResources) {
      observed++;
      return original(path.join(root, 'resources'), ...args);
    }
    return original(file, ...args);
  });
  return { aliasRoot, assertObserved: () => assert.equal(observed, 1, 'raw resource alias must actually be resolved') };
}

test('ASAR audit resolves a distinct raw alias before reading the real canonical archive', async t => {
  const root = await installation(t);
  const resources = await fs.realpath(path.join(root, 'resources'));
  const alias = resourceAlias(t, root, resources);
  assert.equal((await auditInstalledResponseObserver(alias.aliasRoot)).responseStartedUnused, true);
  alias.assertObserved();
});

for (const forceAlias of [false, true]) {
  test(`audit rejects a symlinked application archive via ${forceAlias ? 'distinct raw alias' : 'native temp spelling'}`, async t => {
    const nativeRoot = await installation(t);
    // mkdtemp can return RUNNER~1 while the production audit uses runneradmin.
    // Keep that raw root as the audit input; bind the lstat seam to real identity.
    const resources = await fs.realpath(path.join(nativeRoot, 'resources'));
    const alias = forceAlias ? resourceAlias(t, nativeRoot, resources) : null;
    const root = alias?.aliasRoot ?? nativeRoot;
    const archive = path.join(resources, 'app.asar');
    const target = path.join(path.dirname(resources), 'matching.asar');
    await fs.rename(archive, target);
    // File symlinks require a Windows privilege that hosted runners do not grant.
    // The filesystem seam exercises exactly that lstat fact on every platform.
    const original = fs.lstat;
    let observed = 0;
    t.mock.method(fs, 'lstat', async (file, ...args) => {
      if (file === archive) {
        observed++;
        return { isFile: () => true, isSymbolicLink: () => true };
      }
      return original(file, ...args);
    });
    await assert.rejects(auditInstalledResponseObserver(root), {
      code: 'ERR_ASSERTION', message: 'installed application archive must not be a link',
    });
    assert.equal(observed, 1, 'canonical archive lstat must observe the simulated symlink');
    alias?.assertObserved();
  });
}

test('read-only observer binds documents and script to exact owner/session and atomically restores its empty slot', t => {
  const f = fixture(t);
  assert.deepEqual(f.call('install'), { installed: true, productionBlockingHandlersChanged: false });
  assert.deepEqual(f.registrations, [{ urls: ['http://127.0.0.1:18420/*'] }]);
  f.call('arm', { phase: 'beforeProbe' });
  const headers = Object.freeze({ 'cOnTent-Security-Policy': Object.freeze([policy]) });
  f.emit('document', { responseHeaders: headers });
  f.call('arm', { phase: 'runtime' });
  f.emit('module', { webContents: undefined }); // documented optional field; id stays mandatory
  f.call('arm', { phase: 'releaseProbe' });
  f.emit();
  const restored = f.call('restore');
  assert.equal(f.registered(), false);
  assert.equal(restored.snapshot.registered, false);
  assertEffectiveCspObservation(restored.snapshot, { beforeProbe: 1, module: 1, releaseProbe: 1 });
  assert.doesNotMatch(JSON.stringify(restored), /127\.0\.0\.1|script-src|18420/);
});

for (const [reason, fault] of [
  ['owner id absent', { webContentsId: undefined }], ['wrong owner id', { webContentsId: 2 }],
  ['wrong native owner object', { webContents: {} }], ['wrong method', { method: 'POST' }],
  ['wrong resource type', { resourceType: 'xhr' }], ['wrong status', { statusCode: 304 }],
  ['absent id', { id: undefined }], ['unsafe id', { id: Number.MAX_SAFE_INTEGER + 1 }],
  ['negative id', { id: -1 }], ['missing headers', { responseHeaders: undefined }],
  ['empty headers', { responseHeaders: {} }], ['wrong CSP', { responseHeaders: { 'Content-Security-Policy': ["script-src 'self'"] } }],
  ['uncombined string header', { responseHeaders: { 'Content-Security-Policy': policy } }],
  ['multiple values', { responseHeaders: { 'Content-Security-Policy': [policy, policy] } }],
  ['duplicate case headers', { responseHeaders: { 'Content-Security-Policy': [policy], 'content-security-policy': [policy] } }],
  ['report-only is insufficient', { responseHeaders: { 'Content-Security-Policy-Report-Only': [policy] } }],
]) {
  test(`native effective policy evidence rejects ${reason}`, t => {
    const f = fixture(t); f.call('install'); f.call('arm', { phase: 'beforeProbe' }); f.emit('document', fault);
    assert.throws(() => assertEffectiveCspObservation(f.call('snapshot'), { beforeProbe: 1 }), /native effective CSP/);
  });
}
for (const reason of ['missing response', 'wrong URL', 'unexpected phase', 'duplicate event', 'session changed', 'reused request id']) {
  test(`native evidence fails closed on ${reason}`, t => {
    const f = fixture(t); f.call('install'); f.call('arm', { phase: 'beforeProbe' });
    if (reason === 'wrong URL') f.emit('document', { url: `${ownerURL}&other=1` });
    if (reason === 'unexpected phase') f.emit('module');
    if (reason === 'duplicate event') { f.emit(); f.emit(); }
    if (reason === 'session changed') { f.contents.session = {}; f.emit(); }
    if (reason === 'reused request id') {
      const first = f.emit(); f.call('arm', { phase: 'runtime' }); f.emit('module', { id: first.id });
    }
    assert.throws(() => assertEffectiveCspObservation(f.call('snapshot'), { beforeProbe: 1 }), /native/);
  });
}
test('unrelated local polling is ignored without inspecting response header content', t => {
  const f = fixture(t); f.call('install'); f.call('arm', { phase: 'beforeProbe' });
  f.emit('document', { url: 'http://127.0.0.1:18420/api/health', responseHeaders: new Proxy({}, {
    ownKeys() { throw new Error('must not inspect unrelated headers'); },
  }) });
  f.emit(); assertEffectiveCspObservation(f.call('snapshot'), { beforeProbe: 1 });
});
test('a late callback at unregister is included in the atomic final snapshot', t => {
  const f = fixture(t, { beforeRestore: emit => emit('module') });
  f.call('install'); f.call('arm', { phase: 'beforeProbe' }); f.emit();
  const result = f.call('restore');
  assert.equal(result.snapshot.registered, false);
  assert.deepEqual(result.snapshot.failures, ['unexpected-response-phase']);
  assert.throws(() => assertEffectiveCspObservation(result.snapshot, { beforeProbe: 1 }));
});
test('registration ownership prevents replacement and mismatched-key cleanup', t => {
  const f = fixture(t); f.call('install');
  assert.throws(() => f.call('install'), /already installed/);
  assert.throws(() => f.call('restore', { key: 'not-owned' }), /identity mismatch/);
  assert.equal(f.registered(), true);
  assert.equal(f.call('restore').restored, true);
});
test('unregister failure leaves the observer visibly registered and never claims restoration', t => {
  const f = fixture(t, { restoreError: new Error('unregister rejected') }); f.call('install');
  assert.throws(() => f.call('restore'), /unregister rejected/);
  assert.equal(f.registered(), true);
  assert.equal(f.call('snapshot').registered, true);
});
test('native observer survives serialization without module-scope dependencies', t => {
  const f = fixture(t);
  const context = vm.createContext({ URL, session: f.session, args: { operation: 'install',
    key: '00000000-0000-0000-0000-000000000001', owner: f.owner, ownerURL, moduleURL, policy, sourceAuditPassed: true } });
  const run = () => vm.runInContext(`(${nativeEffectiveCspObserver.toString()})({ session }, args)`, context);
  assert.equal(run().installed, true);
  context.args.operation = 'restore'; assert.equal(run().restored, true);
});
for (const [name, args] of [
  ['missing source audit', { sourceAuditPassed: false }],
  ['different owner URL', { ownerURL: 'http://127.0.0.1:18420/?view=other' }],
  ['external module', { moduleURL: 'https://example.invalid/runtime.mjs' }],
  ['module query', { moduleURL: `${moduleURL}?changed=1` }],
  ['empty policy', { policy: '' }],
]) {
  test(`install rejects ${name} before occupying the event slot`, t => {
    const f = fixture(t);
    assert.throws(() => f.call('install', args));
    assert.equal(f.registered(), false);
    assert.equal(f.registrations.length, 0);
  });
}
test('invalid phase transitions cannot expand the observation window', t => {
  const f = fixture(t); f.call('install');
  assert.throws(() => f.call('arm', { phase: 'runtime' }), /transition rejected/);
  f.call('arm', { phase: 'beforeProbe' });
  assert.throws(() => f.call('arm', { phase: 'beforeProbe' }), /transition rejected/);
  f.call('arm', { phase: 'releaseProbe' });
  assert.throws(() => f.call('arm', { phase: 'runtime' }), /transition rejected/);
});
test('partial registration rejection remains owned and recoverable without touching production handlers', t => {
  const f = fixture(t, { installError: new Error('partially registered before rejection') });
  assert.throws(() => f.call('install'), /partially registered/);
  assert.equal(f.registered(), true);
  assert.equal(f.call('snapshot').registered, false);
  const restored = f.call('restore');
  assert.equal(restored.restored, true);
  assert.equal(restored.absent, false);
  assert.equal(f.registered(), false);
});
test('reviewed desktop module manifest covers every packaged local CJS and relative import', async () => {
  const { DESKTOP_SOURCE_FILES, AUDITED_DESKTOP_MODULE_SHA256 } = await import('../desktop-source-manifest.mjs');
  const packageJson = JSON.parse(await fs.readFile(new URL('../../package.json', import.meta.url), 'utf8'));
  assert.deepEqual([...DESKTOP_SOURCE_FILES].sort(), packageJson.build.files.filter(name => name.endsWith('.cjs')).sort());
  assert.deepEqual([...DESKTOP_SOURCE_FILES].sort(), Object.keys(AUDITED_DESKTOP_MODULE_SHA256).sort());
  for (const filename of DESKTOP_SOURCE_FILES) {
    const source = await fs.readFile(new URL(`../../${filename}`, import.meta.url), 'utf8');
    for (const match of source.matchAll(/require\(['"](\.\/[^'"]+)['"]\)/g)) {
      assert.ok(DESKTOP_SOURCE_FILES.includes(match[1].slice(2)), 'relative desktop import missing from reviewed closure');
    }
  }
  const updater = await fs.readFile(new URL('../../updater.cjs', import.meta.url), 'utf8');
  assert.match(updater, /const RELEASE_POLICY = null;/);
  const { RELEASE_POLICY, unavailableReason } = createRequire(import.meta.url)('../../updater.cjs');
  assert.equal(RELEASE_POLICY, null);
  assert.equal(unavailableReason({ packaged: true, platform: 'win32', policy: RELEASE_POLICY }), '此验证包尚未配置正式签名更新源。');
});
