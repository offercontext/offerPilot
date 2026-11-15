import { useCallback, useRef, useState } from 'react';
import type { ApplicationJdDraft } from '@/types/applicationJdVersion';
import { applicationJdBaselineForOpen, applicationJdRetainedWorkSafety, type ApplicationJdDraftBaseline } from '@/features/desktopUpdates/retainedWorkSafety';

/** Retained source-editor state is independent of its mounted visual surface. */
export function useApplicationJdOwnerDrafts() {
  const applicationJdDraftsRef = useRef(new Map<number, ApplicationJdDraft>());
  const applicationJdBaselinesRef = useRef(new Map<number, ApplicationJdDraftBaseline>());
  const [applicationJdDrafts, setApplicationJdDrafts] = useState<Record<number, ApplicationJdDraft>>({});
  const updateApplicationJdDraft = useCallback((applicationId: number, patch: Partial<ApplicationJdDraft> | null) => {
    const previous = applicationJdDraftsRef.current.get(applicationId);
    const current = previous ?? {
      jdText: '',
      sourceUrl: '',
      expectedCurrentVersionId: null,
      idempotencyKey: null,
      resultUnknown: false,
      pendingOperation: null,
    };
    if (patch === null) {
      applicationJdDraftsRef.current.delete(applicationId);
      applicationJdBaselinesRef.current.delete(applicationId);
      setApplicationJdDrafts((state) => {
        const next = { ...state };
        delete next[applicationId];
        return next;
      });
      return;
    }
    const baseline = applicationJdBaselineForOpen(previous, patch);
    if (baseline) applicationJdBaselinesRef.current.set(applicationId, baseline);
    const next = { ...current, ...patch };
    applicationJdDraftsRef.current.set(applicationId, next);
    setApplicationJdDrafts((state) => ({ ...state, [applicationId]: next }));
  }, []);

  return {
    drafts: applicationJdDrafts,
    updateDraft: updateApplicationJdDraft,
    readSafety: () => applicationJdRetainedWorkSafety({
      applicationJd: Object.fromEntries(applicationJdDraftsRef.current),
      applicationJdBaselines: applicationJdBaselinesRef.current,
    }),
  };
}
