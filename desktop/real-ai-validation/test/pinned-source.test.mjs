import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PIN } from '../contract.mjs';
import { readPinnedSource } from './pinned-source.mjs';

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
  const rejected = source => {
    const result = check(source);
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stdout + result.stderr, /PINNED_PRODUCT_SOURCE_(REQUIRED|UNVERIFIED)/);
  };
  await t.test('correct PIN runs the original complete contract assertions from a shallow helper', () => {
    const result = check(product);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /# pass 1/);
    assert.match(result.stdout, /# fail 0/);
  });
  await t.test('missing, empty, nonrepository, nested and unpinned explicit sources fail closed', async () => {
    const empty = path.join(directory, 'not a repository'); await fs.mkdir(empty);
    for (const source of [path.join(directory, 'missing'), '', empty, path.join(product, 'src'), helper]) rejected(source);
    // No environment is allowed locally only if the helper repo has PIN history.
    rejected(undefined);
  });
  await t.test('wrong HEAD fails even when exact PIN objects remain available', () => {
    const helperHead = git(helper, 'rev-parse', 'HEAD');
    git(product, 'fetch', '--depth=1', '--no-tags', pathToFileURL(root).href, helperHead);
    git(product, 'update-ref', 'HEAD', helperHead);
    try {
      assert.equal(git(product, 'rev-parse', '--verify', `${PIN.commit}^{commit}`), PIN.commit);
      rejected(product);
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
