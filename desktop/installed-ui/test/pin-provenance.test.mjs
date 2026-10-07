import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { parse } from 'yaml';
import { PIN, validateRequest } from '../contract.mjs';

test('current reviewed request remains self-consistent after source/build provenance split',()=>{
  const request=JSON.parse(fs.readFileSync(new URL('../request.json',import.meta.url),'utf8'));
  assert.deepEqual(validateRequest(request),PIN);
  assert.equal(PIN.schema,2);
  assert.match(PIN.commit,/^[a-f0-9]{40}$/);
  assert.match(PIN.buildCommit,/^[a-f0-9]{40}$/);
});
test('source checkout is pinned to product commit and artifact download stays pinned to build run',()=>{
  const workflow=parse(fs.readFileSync(new URL('../../../.github/workflows/desktop-installed-ui.yml',import.meta.url),'utf8'));
  const steps=workflow.jobs['installed-ui'].steps;
  const source=steps.find((item)=>item.uses==='actions/checkout@v4'&&item.with?.path==='.installed-ui-source');
  const artifact=steps.find((item)=>item.uses==='actions/download-artifact@v4');
  assert.equal(source.with.ref,PIN.commit);
  assert.equal(artifact.with['run-id'],PIN.runId);
  assert.equal(artifact.with.name,PIN.artifactName);
  const evidence=steps.find((item)=>item.uses==='actions/upload-artifact@v4');
  assert.equal(evidence.with.name,'windows-installed-ui-evidence-'+PIN.commit.slice(0,8)+'-${{ github.run_id }}-${{ github.run_attempt }}');
  const scope=steps.find((item)=>item.name==='Record honest scope before any validation').run;
  assert.ok(scope.includes(`Product source commit: ${PIN.commit}`));
  assert.ok(scope.includes(`Installer build activation commit: ${PIN.buildCommit}`));
  assert.ok(scope.includes(`Installer build workflow: ${PIN.buildWorkflow}; build run: ${PIN.runId}`));
  assert.ok(scope.includes(`actions/runs/${PIN.fullRegressionRunId} (not certified by this job)`));
});
test('runtime verification fetches independent regression identity but cannot take a caller-supplied pin',()=>{
  const verifier=fs.readFileSync(new URL('../verify-artifact.mjs',import.meta.url),'utf8');
  assert.match(verifier,/read\(`runs\/\$\{PIN\.fullRegressionRunId\}`\)/);
  assert.match(verifier,/validateRequest\(JSON\.parse/);
  assert.match(verifier,/validateMetadata\(\.\.\.await Promise\.all/);
  assert.doesNotMatch(verifier,/process\.argv|process\.env\.(?:BUILD_COMMIT|SOURCE_COMMIT|WORKFLOW|RUN_ID|ARTIFACT_URL)/);
  const smoke=fs.readFileSync(new URL('../smoke.mjs',import.meta.url),'utf8');
  assert.match(smoke,/path\.join\(source, 'desktop', name\)/);
  assert.match(smoke,/sourceCommit: PIN\.commit, buildCommit: PIN\.buildCommit/);
  assert.match(smoke,/fullRegressionRunId: PIN\.fullRegressionRunId/);
});
