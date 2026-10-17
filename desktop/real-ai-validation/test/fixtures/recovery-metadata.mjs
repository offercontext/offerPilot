import { RECOVERY } from '../../carry-forward-policy.cjs';
import { PIN, WORKFLOW, PAID_JOB } from '../../contract.mjs';
export function sourceMetadata() {
  const source = RECOVERY.source;
  const run = { id: source.runId, path: WORKFLOW, head_sha: source.requestCommit, event: 'push', head_branch: PIN.branch,
    run_attempt: 1, status: 'completed', conclusion: 'failure', repository: { full_name: PIN.repository, id: 7 }, head_repository: { full_name: PIN.repository, id: 7 } };
  const jobs = { total_count: 1, jobs: [{ id: 19, name: PAID_JOB, run_id: run.id, head_sha: run.head_sha,
    status: 'completed', conclusion: 'failure', steps: [{ name: RECOVERY.sourceUploadStep, status: 'completed', conclusion: 'success' }] }] };
  const artifact = { id: source.artifactId, name: RECOVERY.sourceArtifactName, expired: false, digest: `sha256:${source.digest}`, size_in_bytes: 2109,
    workflow_run: { id: run.id, head_sha: run.head_sha, head_branch: PIN.branch, repository_id: 7, head_repository_id: 7 } };
  return { run, jobs, artifact, artifacts: { total_count: 1, artifacts: [structuredClone(artifact)] } };
}
