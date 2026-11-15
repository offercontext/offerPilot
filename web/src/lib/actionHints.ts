import type { ConfigType } from 'dayjs';
import type { Application } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import type { Offer } from '@/types/offer';
import type { ReminderCandidate } from '@/types/reminders';
import type { PracticeStats } from '@/types/question';
import {
  derivePipelineInsights,
  type PipelineInsight,
} from './pipelineInsights';

export interface ActionHintInput {
  apps: Application[];
  events: ScheduleEvent[];
  offers: Offer[];
  reminderCandidates?: ReminderCandidate[];
  practiceStats?: PracticeStats | null;
  weeklyTarget?: number;
  now?: ConfigType;
}

/** One presentation adapter for Dashboard, Reminders, and command-driven navigation. */
export function deriveActionHints(input: ActionHintInput): PipelineInsight[] {
  return derivePipelineInsights(input);
}
