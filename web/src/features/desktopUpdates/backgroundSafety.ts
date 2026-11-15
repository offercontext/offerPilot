import { createApiClient } from '@/services/http';
import type { KnowledgeSource } from '@/types/knowledge';
import type { ProactiveJob } from '@/types/proactive';

const http = createApiClient({ baseURL: '/api', timeout: 4000 });

const extractionStates = new Set(['pending', 'processing', 'extracted', 'failed']);
const briefStates = new Set(['not_started', 'pending', 'processing', 'ready', 'failed', 'outdated']);
const jobStates = new Set(['pending', 'leased', 'running', 'succeeded', 'failed', 'cancelled', 'result_unknown']);

/** Visible background work only. The API excludes deleting sources; backend drain is still required. */
export async function readKnownBackgroundWork(readers = {
  sources: async (): Promise<KnowledgeSource[]> => (await http.get<KnowledgeSource[]>('/knowledge/sources', { params: { include_archived: true } })).data,
  jobs: async (): Promise<ProactiveJob[]> => (await http.get<{ items: ProactiveJob[] }>('/proactive/jobs')).data.items,
}): Promise<boolean> {
  const [sources, jobs] = await Promise.all([readers.sources(), readers.jobs()]);
  if (!Array.isArray(sources) || sources.some((source) => !source || !Number.isSafeInteger(source.id) || source.id <= 0
    || !extractionStates.has(source.extraction_status) || !briefStates.has(source.brief_status))) {
    throw new Error('unknown_knowledge_work');
  }
  if (!Array.isArray(jobs) || jobs.some((job) => !job || !jobStates.has(job.state) || !Number.isFinite(job.due_at))) {
    throw new Error('unknown_proactive_work');
  }
  return sources.some((source) => ['pending', 'processing'].includes(source.extraction_status)
    || ['pending', 'processing'].includes(source.brief_status))
    || jobs.some((job) => ['leased', 'running', 'result_unknown'].includes(job.state)
      || (job.state === 'pending' && job.due_at <= Date.now() / 1000));
}
