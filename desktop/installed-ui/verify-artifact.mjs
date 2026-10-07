import fs from 'node:fs/promises';
import path from 'node:path';
import { PIN, validateRequest, validateMetadata } from './contract.mjs';
import { safeFailure } from './diagnostics.mjs';

const evidence = path.resolve(process.env.UI_EVIDENCE_DIR || 'desktop/installed-ui/evidence');
await fs.mkdir(evidence, { recursive: true });
let stage = 'request-pin';
try {
  validateRequest(JSON.parse(await fs.readFile(new URL('./request.json', import.meta.url), 'utf8')));
  if (process.env.GITHUB_REPOSITORY !== PIN.repository || process.env.GITHUB_REF !== `refs/heads/${PIN.branch}`) {
    throw new Error('unexpected execution repository or branch');
  }
  stage = 'product-build-artifact-metadata';
  const token = process.env.GH_TOKEN;
  if (!token) throw new Error('read token required');
  async function read(suffix) {
    const response = await fetch(`https://api.github.com/repos/${PIN.repository}/actions/${suffix}`, {
      headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${token}`, 'X-GitHub-Api-Version': '2022-11-28' },
      redirect: 'error', signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error('metadata request failed');
    return response.json();
  }
  const result = validateMetadata(...await Promise.all([
    read(`runs/${PIN.runId}`), read(`artifacts/${PIN.artifactId}`),
    read(`runs/${PIN.runId}/artifacts?per_page=100`), read(`runs/${PIN.runId}/jobs?per_page=100`),
    read(`runs/${PIN.fullRegressionRunId}`),
  ]));
  await fs.writeFile(path.join(evidence, 'source.json'), JSON.stringify({ status: 'passed', ...result }, null, 2) + '\n');
  console.log('Pinned product/full-gate identity, build activation run and successful packaging artifact verified independently.');
} catch (error) {
  // Never print raw API bodies, credentials, transport errors, or arbitrary exception text.
  await fs.writeFile(path.join(evidence, 'source.json'), JSON.stringify({ status: 'failed', stage, ...safeFailure(error) }, null, 2) + '\n');
  console.error(`Pinned source validation failed at ${stage}.`);
  process.exitCode = 1;
}
