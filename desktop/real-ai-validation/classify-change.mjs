import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { PIN, WORKFLOW, demand, safeCode } from './contract.mjs';
import { githubReader } from './github-read.mjs';
export function isAiToolPath(filename) {
  return typeof filename === 'string' && (filename.startsWith('desktop/real-ai-validation/') || filename === WORKFLOW ||
    filename === 'docs/architecture/desktop-real-ai-validation.md');
}
export function classifyPush(event, env, comparison) {
  demand(env.GITHUB_EVENT_NAME === 'push' && env.GITHUB_REPOSITORY === PIN.repository &&
    env.GITHUB_REF === `refs/heads/${PIN.branch}` && env.GITHUB_RUN_ATTEMPT === '1' &&
    event.repository?.full_name === PIN.repository && !event.deleted && !event.forced &&
    /^[0-9a-f]{40}$/.test(event.before || '') && !/^0{40}$/.test(event.before) &&
    /^[0-9a-f]{40}$/.test(event.after || '') && event.after === env.GITHUB_SHA && event.head_commit?.id === event.after,
    'PUSH_RANGE_UNVERIFIED');
  demand(comparison.status === 'ahead' && comparison.base_commit?.sha === event.before &&
    comparison.merge_base_commit?.sha === event.before && Array.isArray(comparison.commits) && comparison.commits.length > 0 &&
    comparison.commits.length < 250 && comparison.ahead_by === comparison.commits.length &&
    comparison.total_commits === comparison.commits.length, 'PUSH_HISTORY_INCOMPLETE');
  let parent = event.before;
  for (const commit of comparison.commits) {
    demand(commit.parents?.length === 1 && commit.parents[0].sha === parent && /^[0-9a-f]{40}$/.test(commit.sha || ''),
      'MERGE_OR_MISSING_HISTORY');
    parent = commit.sha;
  }
  demand(parent === event.after && Array.isArray(comparison.files) && comparison.files.length < 300, 'PUSH_HISTORY_INCOMPLETE');
  for (const file of comparison.files) demand(typeof file.filename === 'string' && !file.filename.startsWith('/') &&
    !file.filename.split('/').includes('..') && ['added', 'modified', 'removed', 'renamed', 'copied', 'changed', 'unchanged'].includes(file.status),
    'PUSH_FILES_UNVERIFIED');
  return { offline: comparison.files.some(file => isAiToolPath(file.filename) || isAiToolPath(file.previous_filename)) };
}
export async function routePush({ env = process.env, read = githubReader(env.GH_TOKEN) } = {}) {
  const event = JSON.parse(await fs.readFile(env.GITHUB_EVENT_PATH, 'utf8'));
  demand(/^[0-9a-f]{40}$/.test(event.before || '') && /^[0-9a-f]{40}$/.test(event.after || ''), 'PUSH_RANGE_UNVERIFIED');
  return classifyPush(event, env, await read(`compare/${event.before}...${event.after}?per_page=100`));
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const result = await routePush();
    await fs.appendFile(process.env.GITHUB_OUTPUT, `offline=${result.offline ? 'true' : 'false'}\n`);
    console.log(result.offline ? 'AI helper changes require fixed-EXE MOCK validation.' : 'No AI helper changes; Windows MOCK job skipped.');
  } catch (error) { console.error(safeCode(error)); process.exitCode = 1; }
}
