import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { readHashedJSON, validateOriginalEvidence } from '../retry.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');
function fixture() {
  const provenance = {
    sourceSHA: 'a'.repeat(40), runURL: 'https://github.com/example/project/actions/runs/123',
    installer: 'original.exe', installerSha256: 'b'.repeat(64),
  };
  const tree = { files: [{ path: 'original.txt', sha256: 'c'.repeat(64) }] };
  tree.sha256 = hash(JSON.stringify(tree.files));
  const evidence = {
    'result.json': {
      sourceSHA: provenance.sourceSHA, runURL: provenance.runURL,
      installer: { filename: provenance.installer, sha256: provenance.installerSha256 },
      stages: [{ name: 'install-new-nsis', status: 'passed', nsisActuallyInstalled: true, sameBuiltPayloadVerified: true }],
      installedIdentityBefore: {
        webTree: { sha256: tree.sha256, files: 1 }, backendTree: { sha256: tree.sha256, files: 1 },
      },
    },
    'source-sha256.json': { sourceSHA: provenance.sourceSHA, files: [{ file: 'desktop/main.cjs', sha256: 'd'.repeat(64) }] },
    'installed-web-sha256.json': structuredClone(tree),
    'installed-backend-sha256.json': structuredClone(tree),
  };
  return { provenance, evidence };
}

test('original evidence is accepted only with the exact pinned JSON bytes', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-retry-evidence-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const filename = path.join(directory, 'original.json');
  const bytes = '{"passed":true}\n';
  await fs.writeFile(filename, bytes);
  assert.deepEqual(await readHashedJSON(filename, hash(bytes)), { passed: true });
  await assert.rejects(readHashedJSON(filename, '0'.repeat(64)), /original evidence file hash/);
  await assert.rejects(readHashedJSON(filename, 'invalid'));
  await fs.writeFile(filename, bytes.replace('\n', '\r\n'));
  await assert.rejects(readHashedJSON(filename, hash(bytes)), /original evidence file hash/);
  await fs.writeFile(filename, '{"passed":false}\n');
  await assert.rejects(readHashedJSON(filename, hash(bytes)), /original evidence file hash/);
});

test('retained proof binds original source, run, installer and actual same-build installation', () => {
  const good = fixture();
  assert.equal(validateOriginalEvidence(good.provenance, good.evidence), good.evidence);
  for (const mutate of [
    value => { value['result.json'].sourceSHA = 'e'.repeat(40); },
    value => { value['result.json'].runURL += '4'; },
    value => { value['result.json'].installer.filename = 'rebuilt.exe'; },
    value => { value['result.json'].installer.sha256 = 'e'.repeat(64); },
    value => { value['result.json'].stages[0].status = 'failed'; },
    value => { value['result.json'].stages[0].nsisActuallyInstalled = false; },
    value => { value['result.json'].stages[0].sameBuiltPayloadVerified = false; },
    value => { value['source-sha256.json'].sourceSHA = 'e'.repeat(40); },
    value => { value['source-sha256.json'].files = []; },
  ]) {
    const sample = fixture();
    mutate(sample.evidence);
    assert.throws(() => validateOriginalEvidence(sample.provenance, sample.evidence), assert.AssertionError);
  }
});

test('retained resource manifests reject changed digest, file count or file bytes', () => {
  for (const name of ['web', 'backend']) {
    for (const mutate of [
      value => { value[`installed-${name}-sha256.json`].sha256 = 'e'.repeat(64); },
      value => { value['result.json'].installedIdentityBefore[`${name}Tree`].files = 2; },
      value => { value[`installed-${name}-sha256.json`].files[0].sha256 = 'e'.repeat(64); },
      value => { value[`installed-${name}-sha256.json`].files[0].path = 'replacement.txt'; },
    ]) {
      const sample = fixture();
      mutate(sample.evidence);
      assert.throws(() => validateOriginalEvidence(sample.provenance, sample.evidence), assert.AssertionError);
    }
  }
});

test('retry separates harness and product identity and has no rebuilt payload fallback', async () => {
  const code = await fs.readFile(new URL('../smoke.mjs', import.meta.url), 'utf8');
  assert.match(code, /assert\.equal\(report\.harnessSHA, process\.env\.GITHUB_SHA,/);
  assert.match(code, /assert\.equal\(sourceSHA, report\.sourceSHA,/);
  assert.match(code, /assert\.equal\(report\.sourceSHA, originalEvidence\.provenance\.sourceSHA,/);
  assert.match(code, /assert\.equal\(process\.env\.LOCALIZATION_BUILD_DIR, undefined,/);
  assert.match(code, /assert\.equal\(report\.installer\.sha256, originalEvidence\.provenance\.installerSha256,/);
  assert.match(code, /originalEvidence \? originalEvidence\.evidence\['result\.json'\]\.installedIdentityBefore\[key\]/);
  assert.match(code, /originalEvidence \? originalEvidence\.evidence\[`installed-\$\{name\}-sha256\.json`\]/);
  assert.match(code, /assert\.deepEqual\(identity, originalEvidence\.evidence\['source-sha256\.json'\],/);
});

test('safe diagnostics precede unchanged security assertions without delaying request guards', async () => {
  const code = await fs.readFile(new URL('../smoke.mjs', import.meta.url), 'utf8');
  const attached = code.slice(code.indexOf('active = { app, number,'));
  assert.equal(attached.indexOf('await '), attached.indexOf('await installRequestGuard(app);'));
  assert.ok(code.indexOf("await launchPhase('main-identity',") < code.indexOf('assert.equal(identity.packaged,'));
  assert.ok(code.indexOf("await launchPhase('renderer-roles',") < code.indexOf('assert.equal(url.origin,'));
  assert.ok(code.indexOf("await launchPhase('runtime-security-assertions',") < code.indexOf('assert.deepEqual(security.details.map('));
  assert.match(code, /nodeIntegration: false, contextIsolation: true,\s+sandbox: true, webSecurity: true, devToolsOpened: false/);
  assert.match(code, /assertDevToolsPreference\(detail\.devTools, security\.runtimeElectron, report\.devToolsConstructorPolicy\)/);
  assert.match(code, /detail\.devToolsProbe = await app\.evaluate\(probeDevToolsDisabled, detail\.windowId\)/);
  assert.match(code, /assertDevToolsProbe\(detail\.devToolsProbe\)/);
  assert.match(code, /\['packaged', 'isolated', 'ownerPartition', 'haruPartition', 'haruEphemeral'\]\) assert\.equal\(security\[name\], true,/);
  assert.match(code, /assert\.deepEqual\(security\.unsafeSwitches, \[\],/);
  assert.match(code, /role: \['owner', 'haru'\]\.includes\(candidate\.role\) \? candidate\.role : 'unexpected'/);
  assert.match(code, /const safeBoolean = value => typeof value === 'boolean' \? value : null/);
  assert.doesNotMatch(code, /report\.failure\.(?:stack|actual|expected|url)\s*=/);
});
