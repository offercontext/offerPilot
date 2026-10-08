import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import syncFs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PIN } from '../contract.mjs';
import { readPinnedSource, assertSourceDirectoryIdentity } from './pinned-source.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const apiPath = 'src/offerpilot/api.py';
const presentationPath = 'web/src/features/assistantSurface/assistantPresentation.ts';
const cleanEnv = () => Object.fromEntries(Object.entries(process.env)
  .filter(([key]) => !/^GIT_/i.test(key) && !/^NODE_TEST_/i.test(key) && key !== 'AI_PRODUCT_SOURCE'));
const git = (cwd, ...args) => execFileSync('git', ['-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always', '-C', cwd, ...args], {
  encoding: 'utf8', env: cleanEnv(), stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 2 * 1024 * 1024,
}).trim();

// Real file:// depth-one repositories reproduce actions/checkout, including the
// absent PIN object in the helper repo. No network, synthetic commit, or mock Git.
test('cold shallow helper requires its independent verified PIN checkout', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-pinned-source-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const helper = path.join(directory, 'shallow helper'), product = path.join(directory, 'pinned product');
  const selected = Object.hasOwn(process.env, 'AI_PRODUCT_SOURCE') ? process.env.AI_PRODUCT_SOURCE : root;
  // Validate the source of the local fixture before borrowing its Git objects.
  const expectedApi = readPinnedSource(apiPath), expectedPresentation = readPinnedSource(presentationPath);
  assert.equal(Buffer.byteLength(expectedApi), Number(git(selected, 'cat-file', '-s', `${PIN.commit}:${apiPath}`)));
  assert.equal(Buffer.byteLength(expectedPresentation), Number(git(selected, 'cat-file', '-s', `${PIN.commit}:${presentationPath}`)));
  git(directory, 'clone', '--depth=1', '--no-tags', '--no-checkout', pathToFileURL(root).href, helper);
  git(helper, 'checkout', 'HEAD', '--', 'desktop');
  assert.equal(git(helper, 'rev-parse', '--is-shallow-repository'), 'true');
  assert.notEqual(git(helper, 'rev-parse', 'HEAD'), PIN.commit);
  assert.throws(() => git(helper, 'cat-file', '-e', `${PIN.commit}^{commit}`), 'helper must not contain PIN');
  // Exercise current working-tree changes as well as committed CI revisions.
  for (const file of ['live-pilot-completion.test.mjs', 'pinned-source.mjs']) {
    await fs.copyFile(new URL(file, import.meta.url), path.join(helper, 'desktop/real-ai-validation/test', file));
  }
  await fs.mkdir(product);
  git(product, 'init');
  git(product, 'fetch', '--depth=1', '--no-tags', pathToFileURL(selected).href, PIN.commit);
  git(product, 'checkout', '--detach', 'FETCH_HEAD');
  assert.equal(git(product, 'rev-parse', '--is-shallow-repository'), 'true');
  assert.equal(git(product, 'rev-parse', 'HEAD'), PIN.commit);

  const check = source => {
    const env = cleanEnv();
    if (source !== undefined) env.AI_PRODUCT_SOURCE = source;
    return spawnSync(process.execPath, ['--test', '--test-reporter=tap', '--test-name-pattern=^PIN locks complete',
      'test/live-pilot-completion.test.mjs'], {
      cwd: path.join(helper, 'desktop/real-ai-validation'), env, encoding: 'utf8', timeout: 30000,
    });
  };
  const rejected = (source, stage) => {
    const result = check(source);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stdout + result.stderr, /PINNED_PRODUCT_SOURCE_(REQUIRED|UNVERIFIED)/);
    if (stage) assert.ok((result.stdout + result.stderr).includes(`PINNED_PRODUCT_SOURCE_UNVERIFIED:${stage}`));
  };
  await t.test('correct PIN runs the original complete contract assertions from a shallow helper', () => {
    const result = check(product);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /# pass 1/);
    assert.match(result.stdout, /# fail 0/);
  });
  await t.test('native short-name and case aliases bind to the same real cold product directory', async t => {
    const canonical = syncFs.realpathSync.native(product);
    // Same filesystem seam as the installed ORT regression: only input spellings
    // are mapped; native resolution, Git and bigint inode/device reads are real.
    const aliases = [path.join(directory, 'PINNED~1'), path.join(directory, 'PINNED PRODUCT')];
    const original = { native: syncFs.realpathSync.native, lstat: syncFs.lstatSync };
    const mapped = value => aliases.includes(value) ? product : value;
    const nativeInputs = [];
    t.mock.method(syncFs.realpathSync, 'native', (value, ...args) => {
      nativeInputs.push(value); return original.native(mapped(value), ...args);
    });
    t.mock.method(syncFs, 'lstatSync', (value, ...args) => original.lstat(mapped(value), ...args));
    for (const alias of aliases) {
      assert.notEqual(alias, canonical);
      assert.equal(assertSourceDirectoryIdentity(alias, canonical), true);
      assert.equal(assertSourceDirectoryIdentity(canonical, alias), true);
      assert.equal(readPinnedSource(apiPath, { env: { AI_PRODUCT_SOURCE: alias } }), expectedApi);
      assert.ok(nativeInputs.includes(alias), 'raw alias must reach the native resolver');
    }
    t.mock.restoreAll();
    // Windows runs the actual OS aliases too; mkdtemp retains RUNNER~1 on the
    // hosted runner, while Git and native realpath return its long spelling.
    const realSpellings = process.platform === 'win32'
      ? [product, canonical, canonical.toLowerCase(), canonical.replaceAll('\\', '/')]
      : [product, canonical];
    for (const spelling of realSpellings) {
      const result = check(spelling);
      assert.equal(result.status, 0, result.stdout + result.stderr);
    }
  });
  await t.test('different directories, unreliable metadata and root or ancestor links stay rejected', async () => {
    const other = path.join(directory, 'other product'); await fs.mkdir(other);
    assert.throws(() => assertSourceDirectoryIdentity(product, other));
    assert.throws(() => assertSourceDirectoryIdentity(product, path.join(product, 'src')));
    for (const field of ['dev', 'ino']) {
      let calls = 0;
      const ports = { ...syncFs, statSync: (...args) => {
        const stat = syncFs.statSync(...args);
        return ++calls === 2 ? { ...stat, [field]: stat[field] + 1n, isDirectory: () => true } : stat;
      } };
      assert.throws(() => assertSourceDirectoryIdentity(product, product, ports));
    }
    for (const invalid of [{ ino: 0n }, { ino: undefined }, { ino: 1 }, { dev: undefined }, { dev: -1n }]) {
      const ports = { ...syncFs, statSync: (...args) => ({ ...syncFs.statSync(...args), ...invalid, isDirectory: () => true }) };
      assert.throws(() => assertSourceDirectoryIdentity(product, product, ports), /reliable directory identity/);
    }
    const type = process.platform === 'win32' ? 'junction' : 'dir';
    const link = path.join(directory, 'product link'), parentLink = path.join(directory, 'ancestor link');
    await fs.symlink(product, link, type); await fs.symlink(directory, parentLink, type);
    for (const source of [link, link + path.sep, path.join(parentLink, 'pinned product')]) {
      rejected(source, 'ROOT_IDENTITY');
      assert.throws(() => readPinnedSource(apiPath, { env: { AI_PRODUCT_SOURCE: source } }),
        /PINNED_PRODUCT_SOURCE_UNVERIFIED:ROOT_IDENTITY/);
    }
  });
  await t.test('missing, empty, nonrepository, nested and unpinned explicit sources fail closed', async () => {
    const empty = path.join(directory, 'not a repository'); await fs.mkdir(empty);
    rejected(path.join(directory, 'missing'), 'SOURCE_REALPATH'); rejected('');
    for (const source of [empty, path.join(product, 'src')]) rejected(source, 'ROOT_IDENTITY');
    rejected(helper, 'PIN_OBJECT');
    // No environment is allowed locally only if the helper repo has PIN history.
    rejected(undefined, 'PIN_OBJECT');
  });
  await t.test('wrong HEAD fails even when exact PIN objects remain available', () => {
    const helperHead = git(helper, 'rev-parse', 'HEAD');
    git(product, 'fetch', '--depth=1', '--no-tags', pathToFileURL(root).href, helperHead);
    git(product, 'update-ref', 'HEAD', helperHead);
    try {
      assert.equal(git(product, 'rev-parse', '--verify', `${PIN.commit}^{commit}`), PIN.commit);
      rejected(product, 'PIN_HEAD');
    } finally { git(product, 'update-ref', 'HEAD', PIN.commit); }
  });
  await t.test('source bytes come from the PIN blob, never the working tree or arbitrary paths', async () => {
    await fs.writeFile(path.join(product, apiPath), 'untrusted working tree API');
    await fs.writeFile(path.join(product, presentationPath), 'untrusted working tree presentation');
    const options = { env: { AI_PRODUCT_SOURCE: product } };
    assert.equal(readPinnedSource(apiPath, options), expectedApi);
    assert.equal(readPinnedSource(presentationPath, options), expectedPresentation);
    for (const file of ['../api.py', `${apiPath}/../api.py`, '/etc/passwd', 'HEAD:src/offerpilot/api.py',
      'src\\offerpilot\\api.py', 'src/offerpilot/desktop.py']) {
      assert.throws(() => readPinnedSource(file, options), /PINNED_SOURCE_PATH_DENIED/);
    }
    assert.equal(check(product).status, 0);
  });
  await t.test('unset local source uses its own PIN history independently of cwd', async () => {
    // Install current helper contracts too: the product predates its packaging PIN.
    // No source override is supplied and the process cwd is a different repo.
    await fs.copyFile(new URL('pinned-source.mjs', import.meta.url),
      path.join(product, 'desktop/real-ai-validation/test/pinned-source.mjs'));
    for (const file of ['desktop/real-ai-validation/contract.mjs', 'desktop/installed-ui/contract.mjs']) {
      await fs.copyFile(path.join(root, file), path.join(product, file));
    }
    const moduleUrl = pathToFileURL(path.join(product, 'desktop/real-ai-validation/test/pinned-source.mjs')).href;
    const read = env => spawnSync(process.execPath, ['--input-type=module', '-e',
      `import { readPinnedSource } from ${JSON.stringify(moduleUrl)}; process.stdout.write(readPinnedSource(${JSON.stringify(apiPath)}));`], {
      cwd: helper, env, encoding: 'utf8', maxBuffer: 2 * 1024 * 1024, timeout: 30000,
    });
    git(product, 'update-ref', 'HEAD', git(helper, 'rev-parse', 'HEAD'));
    try {
      const result = read(cleanEnv());
      assert.equal(result.status, 0, result.stderr);
      assert.equal(result.stdout, expectedApi);
      // Invalid explicit sources must not fall back even when the local module
      // is anchored in a repository that does contain the complete PIN history.
      for (const source of [path.join(directory, 'missing'), '', helper]) {
        const invalid = read({ ...cleanEnv(), AI_PRODUCT_SOURCE: source });
        assert.equal(invalid.status, 1, invalid.stdout + invalid.stderr);
        assert.match(invalid.stderr, /PINNED_PRODUCT_SOURCE_(REQUIRED|UNVERIFIED)/);
      }
    } finally { git(product, 'update-ref', 'HEAD', PIN.commit); }
  });
});
