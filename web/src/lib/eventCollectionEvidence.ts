import type { ScheduleEvent } from '@/types/event';

export type EventCollectionState = 'loading' | 'error' | 'ready' | 'unknown';

export type EventCollectionEvidence =
  | { state: 'ready'; events: ScheduleEvent[] }
  | { state: Exclude<EventCollectionState, 'ready'>; events?: undefined };

/** Only the successful unfiltered collection can establish that an event is absent. */
export function eventCollectionEvidence({ data, isLoading, isError }: {
  data: unknown;
  isLoading?: boolean;
  isError?: boolean;
}): EventCollectionEvidence {
  if (isLoading) return { state: 'loading' };
  if (isError) return { state: 'error' };
  if (!Array.isArray(data)) return { state: 'unknown' };
  return { state: 'ready', events: data };
}
