// Read metadata from the installed, payload-matched application. No dependency
// module is executed and no package is fetched. The supported product uses ASAR.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { extractFile, getRawHeader, uncache } from '@electron/asar';
import { hash } from './integrity.mjs';
import { assertDirectoryIdentity, unlinkedDirectory, unlinkedFile, safeRelativePath } from './filesystem-safety.mjs';

const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const SOURCE_FILES = ['desktop/package.json', 'desktop/package-lock.json', 'web/package.json', 'web/package-lock.json'];
const VERSION = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/;
const UPDATER = 'node_modules/electron-updater';
const RUNTIME = 'builder-util-runtime';

export function readPinnedDependencySource(source, commit) {
  assert.match(commit, /^[a-f0-9]{40}$/);
  const root = unlinkedDirectory(source).canonical;
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^GIT_/i.test(key)));
  const git = args => execFileSync('git', ['--no-replace-objects', '-c', 'protocol.allow=never', '-C', root, ...args], {
    env: { ...env, GIT_NO_LAZY_FETCH: '1' }, stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 4 * 1024 * 1024,
  });
  assertDirectoryIdentity(source, git(['rev-parse', '--show-toplevel']).toString('utf8').trim());
  assert.equal(git(['rev-parse', '--verify', 'HEAD']).toString('utf8').trim(), commit, 'dependency source must be exact PIN checkout');
  const files = Object.fromEntries(SOURCE_FILES.map(relative => [relative,
    git(['show', '--no-ext-diff', '--no-textconv', `${commit}:${relative}`])]));
  // A dirty checkout is not allowed to silently supply different package input.
  for (const relative of SOURCE_FILES) {
    assert.equal(fs.readFileSync(unlinkedFile(source, relative), 'utf8').replace(/\r\n/g, '\n'),
      files[relative].toString('utf8').replace(/\r\n/g, '\n'), 'dependency source differs from PIN blob');
  }
  return { commit, files };
}

// Node's upward node_modules lookup, bounded by the installed archive root.
// Refuse unsupported file/exports fallbacks rather than skipping to a good root.
export function nodeModuleCandidates(importer, name) {
  safeRelativePath(importer);
  assert.match(name, /^[a-z][a-z0-9-]*$/);
  const candidates = [];
  let directory = path.posix.dirname(importer);
  while (true) {
    if (path.posix.basename(directory) !== 'node_modules') {
      candidates.push(path.posix.join(directory, 'node_modules', name));
    }
    if (directory === '.') break;
    directory = path.posix.dirname(directory);
  }
  return candidates;
}
function expectedDependencies({ commit, files }) {
  const json = name => JSON.parse(files[name].toString('utf8'));
  const desktop = json('desktop/package.json'), lock = json('desktop/package-lock.json');
  const web = json('web/package.json'), webLock = json('web/package-lock.json');
  assert.equal(lock.lockfileVersion, 3); assert.equal(webLock.lockfileVersion, 3);
  const updater = lock.packages?.[UPDATER];
  assert.match(updater?.version || '', VERSION);
  assert.equal(desktop.dependencies?.['electron-updater'], updater.version);
  assert.equal(lock.packages[''].dependencies?.['electron-updater'], updater.version);
  const runtimePath = nodeModuleCandidates(`${UPDATER}/out/main.js`, RUNTIME).find(name => lock.packages[name]);
  const runtime = lock.packages[runtimePath];
  assert.match(runtime?.version || '', VERSION);
  assert.equal(updater.dependencies?.[RUNTIME], runtime.version, 'updater runtime must be exact pinned dependency');
  const axios = webLock.packages?.['node_modules/axios'];
  assert.match(axios?.version || '', VERSION);
  assert.equal(web.dependencies?.axios, webLock.packages[''].dependencies?.axios);
  for (const record of [updater, runtime, axios]) {
    assert.notEqual(record.dev, true); assert.notEqual(record.link, true);
    assert.match(record.integrity || '', /^sha512-[A-Za-z0-9+/]+={0,2}$/);
  }
  return { commit, updater, runtime, runtimePath, axios,
    sourceSha256: Object.fromEntries(SOURCE_FILES.map(name => [name, sha256(files[name])])) };
}

function archiveReader(archive) {
  uncache(archive); // Never reuse header state after a prior file at this path.
  const header = getRawHeader(archive).header;
  const files = new Set(), directories = new Set(['.']), folded = new Set();
  const visit = (entry, prefix = '') => {
    assert.ok(entry && typeof entry === 'object' && !Object.hasOwn(entry, 'link') && !entry.unpacked,
      'dependency archive must not contain links or unpacked entries');
    if (entry.files) {
      assert.ok(typeof entry.files === 'object' && !Array.isArray(entry.files));
      for (const [segment, child] of Object.entries(entry.files)) {
        safeRelativePath(segment); assert.equal(segment.includes('/'), false);
        const name = prefix ? `${prefix}/${segment}` : segment;
        assert.equal(folded.has(name.toLowerCase()), false, 'archive has ambiguous Windows path aliases');
        folded.add(name.toLowerCase());
        if (child.files) directories.add(name);
        visit(child, name);
      }
    } else { assert.ok(Number.isSafeInteger(entry.size) && entry.size >= 0); files.add(prefix); }
  };
  visit(header);
  const read = name => {
    safeRelativePath(name); assert.ok(files.has(name), 'required installed dependency file missing');
    // ASAR's lookup splits native separators; keep audit keys POSIX and adapt
    // only this validated, exact header member at the library boundary.
    return extractFile(archive, name.split('/').join(path.sep), false);
  };
  return { files, directories, read };
}
function resolvePackage(reader, importer, name) {
  for (const candidate of nodeModuleCandidates(importer, name)) {
    for (const suffix of ['', '.js', '.json', '.node']) {
      assert.equal(reader.files.has(candidate + suffix), false, 'unsupported package file shadow');
    }
    if (!reader.directories.has(candidate)) continue;
    const metadataPath = `${candidate}/package.json`;
    const bytes = reader.read(metadataPath), metadata = JSON.parse(bytes.toString('utf8'));
    assert.equal(metadata.name, name);
    assert.equal(Object.hasOwn(metadata, 'exports'), false, 'unsupported package exports');
    safeRelativePath(metadata.main);
    const main = `${candidate}/${metadata.main}`;
    assert.ok(reader.files.has(main), 'installed package entrypoint missing');
    return { path: candidate, metadataPath, metadataSha256: sha256(bytes), main, metadata };
  }
  throw new Error('installed dependency cannot resolve inside application');
}

export async function auditInstalledDependencies({ installDir, payload, source, pin }) {
  const expected = expectedDependencies(readPinnedDependencySource(source, pin.commit));
  const archive = unlinkedFile(installDir, 'resources/app.asar');
  const payloadArchive = unlinkedFile(payload, 'resources/app.asar');
  const archiveSha256 = await hash(archive);
  assert.equal(archiveSha256, await hash(payloadArchive), 'dependency archive differs from installer payload');
  const reader = archiveReader(archive);
  const app = JSON.parse(reader.read('package.json').toString('utf8'));
  assert.equal(app.name, 'offerpilot-desktop'); assert.equal(app.main, 'main.cjs');
  assert.equal(app.dependencies?.['electron-updater'], expected.updater.version);
  const updater = resolvePackage(reader, app.main, 'electron-updater');
  assert.equal(updater.path, UPDATER);
  assert.equal(updater.metadata.version, expected.updater.version, 'installed updater version differs from PIN');
  assert.equal(updater.metadata.dependencies?.[RUNTIME], expected.runtime.version);
  assert.equal(updater.metadata.main, 'out/main.js', 'unsupported updater entrypoint');
  // Resolve from EVERY shipped updater JavaScript location: a conservative
  // superset of direct require sites, with no brittle parsing of source text.
  // This is a static lookup proof; it does not claim the dependency was executed.
  const importers = [...reader.files].filter(name => name.startsWith(`${UPDATER}/out/`)
    && !name.slice(UPDATER.length + 1).includes('/node_modules/') && /\.[cm]?js$/.test(name)).sort();
  assert.ok(importers.includes(updater.main), 'installed updater JavaScript entrypoint missing');
  // Node LOAD_PACKAGE_SELF precedes upward node_modules lookup. The supported
  // updater distribution has one package scope; nested scopes are unexpected
  // and could redirect a same-name dependency through their exports map.
  assert.equal([...reader.files].some(name => name.startsWith(`${UPDATER}/out/`)
    && !name.slice(UPDATER.length + 1).includes('/node_modules/') && name.endsWith('/package.json')), false,
  'unexpected nested updater package scope');
  const resolutions = importers.map(importer => {
    const runtime = resolvePackage(reader, importer, RUNTIME);
    assert.equal(runtime.metadata.version, expected.runtime.version, 'resolved installed runtime version differs from PIN');
    return { importer, packagePath: runtime.path, version: runtime.metadata.version, packageJsonSha256: runtime.metadataSha256, main: runtime.main };
  });
  // Even an unused old nested package is reported as contamination, never hidden
  // by a matching hoisted copy. Installed layout may otherwise be flattened.
  const runtimeCopies = [...reader.files].filter(name => name.endsWith(`/node_modules/${RUNTIME}/package.json`)
    || name === `node_modules/${RUNTIME}/package.json`).sort();
  for (const name of runtimeCopies) {
    const metadata = JSON.parse(reader.read(name).toString('utf8'));
    assert.equal(metadata.name, RUNTIME); assert.equal(metadata.version, expected.runtime.version, 'unexpected installed runtime copy');
  }
  assert.ok(runtimeCopies.length > 0);
  // Vite bundles Axios. A source lock and matching artifact bytes establish
  // provenance, not an independently measured Axios runtime/package version.
  const web = unlinkedDirectory(path.join(installDir, 'resources/web')).canonical;
  const assets = unlinkedDirectory(path.join(web, 'assets')).canonical;
  const javascript = fs.readdirSync(assets).filter(name => name.endsWith('.js')).sort();
  assert.ok(javascript.length > 0, 'installed Vite JavaScript missing');
  const webFiles = ['index.html', ...javascript.map(name => `assets/${name}`)];
  const bundleFiles = [];
  for (const name of webFiles) {
    const installedFile = unlinkedFile(web, name);
    const sha = await hash(installedFile);
    assert.equal(sha, await hash(unlinkedFile(payload, `resources/web/${name}`)), 'installed Vite bytes differ from payload');
    bundleFiles.push({ path: `resources/web/${name}`, sha256: sha });
  }
  assert.equal(await hash(archive), archiveSha256, 'dependency archive changed during audit');
  return { schema: 1, sourceCommit: expected.commit, installerSha256: pin.installerSha256,
    archive: { path: 'resources/app.asar', sha256: archiveSha256, payloadMatched: true },
    sourceSha256: expected.sourceSha256,
    electronUpdater: { version: updater.metadata.version, expectedVersion: expected.updater.version,
      packagePath: updater.path, packageJsonSha256: updater.metadataSha256, lockIntegrity: expected.updater.integrity },
    builderUtilRuntime: { version: resolutions[0].version, expectedVersion: expected.runtime.version,
      lockPackagePath: expected.runtimePath, lockIntegrity: expected.runtime.integrity, installedCopies: runtimeCopies,
      resolution: 'static-node-lookup-from-every-installed-updater-js-location', resolutions },
    axios: { expectedVersion: expected.axios.version, lockIntegrity: expected.axios.integrity,
      directInstalledVersionProven: false, evidence: 'PIN-source-lock-and-installer-payload-matched-Vite-bytes', bundleFiles },
    dependencyCodeExecuted: false };
}
