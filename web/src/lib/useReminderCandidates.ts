import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { listReminderCandidates } from '@/services/reminders';

export const REMINDER_CANDIDATES_QUERY_KEY = ['reminder-candidates'] as const;
const SOURCE_QUERY_ROOTS = new Set(['applications', 'events', 'calendar', 'offers', 'notes', 'interview-notes', 'interview-preparation']);

/** All surfaces share one read model; callers must distinguish loading/error from []. */
export function useReminderCandidates() {
  const client = useQueryClient();
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  useEffect(() => client.getQueryCache().subscribe((event) => {
    if (event.type === 'updated' && SOURCE_QUERY_ROOTS.has(String(event.query.queryKey[0]))
      && (event.action.type === 'invalidate' || event.action.type === 'success')) {
      // Explicit cancellation also fences an initial request with no cached data.
      // invalidateQueries alone coalesces that request and can accept its stale result.
      void client.cancelQueries({ queryKey: REMINDER_CANDIDATES_QUERY_KEY }).then(() =>
        client.invalidateQueries({ queryKey: REMINDER_CANDIDATES_QUERY_KEY }));
    }
  }), [client]);
  const query = useQuery({
    queryKey: [...REMINDER_CANDIDATES_QUERY_KEY, timezone],
    queryFn: ({ signal }) => listReminderCandidates(timezone, signal),
    refetchInterval: 60_000,
    retry: false,
  });
  // While revalidating, cached candidates are not proof of current source facts.
  // Keep loading/error distinct from a successfully empty day on every surface.
  return { ...query, data: query.isFetching || query.isError ? undefined : query.data,
    isPending: query.isPending || query.isFetching };
}
