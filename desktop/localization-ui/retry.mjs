import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

export async function readHashedJSON(filename, expected) {
  assert.match(expected, /^[a-f0-9]{64}$/);
  const bytes = await fs.readFile(filename);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), expected, 'original evidence file hash must match pinned provenance');
  return JSON.parse(bytes.toString('utf8'));
}
export function validateOriginalEvidence(provenance, evidence) {
  const original = evidence['result.json'];
  assert.equal(original.sourceSHA, provenance.sourceSHA, 'original report source identity');
  assert.equal(original.runURL, provenance.runURL, 'original report run identity');
  assert.equal(original.installer.filename, provenance.installer, 'original installer filename');
  assert.equal(original.installer.sha256, provenance.installerSha256, 'original installer checksum');
  assert.ok(original.stages.some(item => item.name === 'install-new-nsis' && item.status === 'passed'
    && item.nsisActuallyInstalled === true && item.sameBuiltPayloadVerified === true), 'original build-to-install identity proof is required');
  assert.equal(evidence['source-sha256.json'].sourceSHA, provenance.sourceSHA, 'original source manifest identity');
  assert.ok(evidence['source-sha256.json'].files.length > 0, 'original source file manifest must not be empty');
  for (const name of ['web', 'backend']) {
    const tree = evidence[`installed-${name}-sha256.json`];
    assert.equal(tree.sha256, original.installedIdentityBefore[`${name}Tree`].sha256, 'original resource manifest digest');
    assert.equal(tree.files.length, original.installedIdentityBefore[`${name}Tree`].files, 'original resource manifest file count');
    assert.equal(createHash('sha256').update(JSON.stringify(tree.files)).digest('hex'), tree.sha256, 'original resource file list integrity');
  }
  return evidence;
}
export async function loadOriginalEvidence(directory) {
  const provenance = JSON.parse(await fs.readFile(new URL('./retry-provenance.json', import.meta.url), 'utf8'));
  const evidence = {};
  for (const [filename, hash] of Object.entries(provenance.evidenceFiles)) evidence[filename] = await readHashedJSON(path.join(directory, filename), hash);
  validateOriginalEvidence(provenance, evidence);
  return { provenance, evidence };
}
