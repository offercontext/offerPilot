import { useCallback, useRef, useState } from 'react';
import type { OfferNegotiationDraft } from '@/components/OfferNegotiationDrawer';
import { offerNegotiationRetainedWorkSafety } from '@/features/desktopUpdates/retainedWorkSafety';

type Snapshot = { ui: Record<number, OfferNegotiationDraft>; pilot: Record<number, OfferNegotiationDraft> };

/** UI and Pilot share one domain owner without silently sharing their drafts. */
export function useOfferNegotiationOwnerDrafts() {
  const [snapshot, setSnapshot] = useState<Snapshot>({ ui: {}, pilot: {} });
  const current = useRef(snapshot);
  const update = useCallback((entrypoint: keyof Snapshot, offerId: number, draft: OfferNegotiationDraft | null) => {
    const bucket = { ...current.current[entrypoint] };
    if (draft) bucket[offerId] = draft;
    else delete bucket[offerId];
    current.current = { ...current.current, [entrypoint]: bucket };
    setSnapshot(current.current);
  }, []);
  return {
    drafts: snapshot.ui,
    pilotDrafts: snapshot.pilot,
    updateDraft: useCallback((id: number, draft: OfferNegotiationDraft | null) => update('ui', id, draft), [update]),
    updatePilotDraft: useCallback((id: number, draft: OfferNegotiationDraft | null) => update('pilot', id, draft), [update]),
    readSafety: () => offerNegotiationRetainedWorkSafety({ offerNegotiation: current.current.ui, offerNegotiationPilot: current.current.pilot }),
  };
}
