import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { UITurn } from '@/components/ChatPanel/model';
import type { PendingAction } from '@/types/chat';
import type { PilotPresentationSnapshot } from './contracts';
import { getPilotPresentation } from './service';
import { mergePresentationTurns, withTransportUncertainty } from './model';

/** Called once by the conversation owner; shells only consume its result. */
export function usePilotPresentation(conversationId: number | undefined, turns: UITurn[], pending: PendingAction | null, loading: boolean, confirmationUnknown = false) {
  const [loaded, setLoaded] = useState<{ snapshot: PilotPresentationSnapshot; turns: UITurn[]; pending: PendingAction | null; revision: number } | null>(null);
  const [revision, setRevision] = useState(0);
  const latestSnapshot = useRef<PilotPresentationSnapshot | null>(null);
  const [requestState, setRequestState] = useState<{ conversationId: number; failed: boolean; refreshing: boolean } | null>(null);
  const refreshPresentation = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let current = true;
    if (conversationId !== undefined && !loading) {
      setRequestState((previous) => ({ conversationId, failed: previous?.conversationId === conversationId && previous.failed, refreshing: true }));
      void getPilotPresentation(conversationId, latestSnapshot.current).then((snapshot) => {
        if (current) {
          latestSnapshot.current = snapshot;
          setLoaded({ snapshot, turns, pending, revision });
          setRequestState({ conversationId, failed: false, refreshing: false });
        }
      }).catch(() => {
        // A failed refresh must release the stale projection so the latest
        // persisted messages remain visible through the original owner.
        if (current) {
          latestSnapshot.current = null;
          setLoaded(null);
          setRequestState({ conversationId, failed: true, refreshing: false });
        }
      });
    }
    return () => { current = false; };
  }, [conversationId, turns, pending, loading, revision]);
  const sameConversation = loaded?.snapshot.conversation_id === conversationId;
  const currentSnapshot = !loading && sameConversation && loaded?.turns === turns && loaded?.pending === pending && loaded?.revision === revision;
  const snapshot = useMemo(() => !sameConversation || !loaded ? null : currentSnapshot ? loaded.snapshot : {
    ...loaded.snapshot,
    items: loaded.snapshot.items.map((item) => item.action ? { ...item, action: { ...item.action, available_actions: item.action.available_actions.filter((command) => command === 'refresh') } } : item),
  }, [loaded, sameConversation, currentSnapshot]);
  const displayTurns = useMemo(() => {
    const projected = mergePresentationTurns(turns, snapshot);
    // Keep the operation's DOM identity through an in-flight request while
    // allowing its new user message and streaming reply to remain visible.
    if (snapshot && !currentSnapshot && loaded) {
      const priorIds = new Set(loaded.turns.map((turn) => turn.id));
      projected.push(...turns.filter((turn) => turn.id?.startsWith('transient:') && !priorIds.has(turn.id)));
    }
    return projected.map((turn) => turn.action && turn.action.operation_id === pending?.operation_id
      ? { ...turn, action: withTransportUncertainty(turn.action, confirmationUnknown) } : turn);
  }, [turns, snapshot, currentSnapshot, loaded, pending?.operation_id, confirmationUnknown]);
  return {
    displayTurns,
    presentationSnapshot: snapshot,
    refreshPresentation,
    presentationFailed: requestState?.conversationId === conversationId && requestState?.failed === true,
    presentationRefreshing: requestState?.conversationId === conversationId && requestState?.refreshing === true,
  };
}
