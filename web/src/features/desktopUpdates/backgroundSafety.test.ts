import { describe, expect, it } from 'vitest';
import { readKnownBackgroundWork } from './backgroundSafety';
import type { KnowledgeSource } from '@/types/knowledge';
import type { ProactiveJob } from '@/types/proactive';

const source = (extraction: string, brief: string) => ({ id: 1, extraction_status: extraction, brief_status: brief } as KnowledgeSource);
const job = (state: ProactiveJob['state'], due_at = 0) => ({ state, due_at } as ProactiveJob);
const read = (sources: KnowledgeSource[], jobs: ProactiveJob[]) => readKnownBackgroundWork({ sources: async () => sources, jobs: async () => jobs });

describe('known background work before installation', () => {
  it('permits settled sources and future scheduled work', async () => {
    expect(await read([source('extracted', 'ready')], [job('pending', Date.now() / 1000 + 3600)])).toBe(false);
  });
  it.each([['pending', 'not_started'], ['processing', 'not_started'], ['extracted', 'pending'], ['extracted', 'processing']])('blocks Knowledge %s / %s', async (extraction, brief) => {
    expect(await read([source(extraction, brief)], [])).toBe(true);
  });
  it.each(['pending', 'leased', 'running', 'result_unknown'] as const)('blocks due or active Proactive %s', async (state) => {
    expect(await read([], [job(state)])).toBe(true);
  });
  it('does not turn an unknown source state or failed read into idle', async () => {
    await expect(read([source('new-status', 'ready')], [])).rejects.toThrow();
    await expect(readKnownBackgroundWork({ sources: async () => { throw new Error('offline'); }, jobs: async () => [] })).rejects.toThrow();
  });
});
