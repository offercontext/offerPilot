import dayjs from 'dayjs';
import { describe, expect, it } from 'vitest';
import { ACTION_HINT_THRESHOLDS, deriveActionHints } from './actionHints';

describe('action hints', () => {
  it('publishes one stable threshold source', () => {
    expect(ACTION_HINT_THRESHOLDS).toEqual({
      offerDeadlineDays: 7,
      interviewHours: 72,
      staleApplicationDays: 7,
      questionDue: 1,
    });
  });

  it('derives explainable hints from the shared pipeline rules', () => {
    const hints = deriveActionHints({ apps: [], events: [], offers: [], now: dayjs('2026-07-27') });
    expect(hints).toEqual([]);
  });
});
