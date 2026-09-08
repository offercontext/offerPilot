import type { ConfigType } from 'dayjs';
import type { Application } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import type { Offer } from '@/types/offer';
import type { PracticeStats } from '@/types/question';
import {
  derivePipelineInsights,
  type PipelineInsight,
} from './pipelineInsights';

export const ACTION_HINT_THRESHOLDS = Object.freeze({
  offerDeadlineDays: 7,
  interviewHours: 72,
  staleApplicationDays: 7,
  questionDue: 1,
});

export interface ActionHintInput {
  apps: Application[];
  events: ScheduleEvent[];
  offers: Offer[];
  practiceStats?: PracticeStats | null;
  weeklyTarget?: number;
  now?: ConfigType;
}

/** Single rule source for Dashboard, Reminders, and command-driven navigation. */
export function deriveActionHints(input: ActionHintInput): PipelineInsight[] {
  return derivePipelineInsights(input);
}
