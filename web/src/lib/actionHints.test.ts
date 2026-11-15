import { describe, expect, it } from 'vitest';
import { deriveActionHints } from './actionHints';
import { derivePipelineInsights } from './pipelineInsights';
import type { ReminderCandidate } from '@/types/reminders';
import rawCorpus from '../../../contracts/reminder-candidates.v1.json?raw';

const corpus = JSON.parse(rawCorpus) as { cases: Array<{ name: string; now: string; expected: ReminderCandidate[] }> };
describe('action hints', () => {
  it.each(corpus.cases)('uses the same candidate facts for $name', ({ now, expected }) => {
    const input = { apps: [], events: [], offers: [], now, reminderCandidates: expected };
    expect(deriveActionHints(input)).toEqual(derivePipelineInsights(input));
  });
});
