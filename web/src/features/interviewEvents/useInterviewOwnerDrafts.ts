import { useCallback, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import type { InterviewReviewProposalAttemptState } from '@/components/InterviewReviewProposalDrawer';
import type { InterviewKnowledgeCaptureDraft } from '@/components/InterviewKnowledgeCaptureDrawer';
import type { InterviewPreparationAttemptState, InterviewPreparationDraft } from '@/components/InterviewPreparationProposalDrawer';
import type { InterviewStoryDraft } from '@/components/InterviewStoryDrawer';
import type { AdaptivePracticeOwnerDraft } from '@/types/adaptiveInterviewPractice';
import type { ReviewReadinessOwnerDraft } from '@/features/reviewReadiness/contracts';
import { interviewRetainedWorkSafety } from '@/features/desktopUpdates/retainedWorkSafety';


// One synchronous snapshot per bucket: an update/exit read in the same event
// turn must see pending and unknown work before React commits the next render.
function useRetainedBucket<T>(initial: T): [T, Dispatch<SetStateAction<T>>, MutableRefObject<T>] {
  const [state, setState] = useState(initial);
  const current = useRef(state);
  const update = useCallback((next: SetStateAction<T>) => {
    current.current = typeof next === 'function'
      ? (next as (previous: T) => T)(current.current) : next;
    setState(current.current);
  }, []);
  return [state, update, current];
}

/** The interview domain retains drafts across surface close/reopen. Generation
 * and receipt authorization remain with the canonical CoreTask controller. */
export function useInterviewOwnerDrafts() {
  const [adaptivePracticeDrafts, setAdaptivePracticeDrafts, adaptivePracticeDraftsSnapshotRef] = useRetainedBucket<Record<string, AdaptivePracticeOwnerDraft>>({});
  const [interviewReviewProposalAttempts, setInterviewReviewProposalAttempts, interviewReviewProposalAttemptsSnapshotRef] = useRetainedBucket<Record<number, InterviewReviewProposalAttemptState>>({});
  const [reviewReadinessDrafts, setReviewReadinessDrafts, reviewReadinessDraftsSnapshotRef] = useRetainedBucket<Record<string, ReviewReadinessOwnerDraft>>({});
  const [interviewKnowledgeCaptureDrafts, setInterviewKnowledgeCaptureDrafts, interviewKnowledgeCaptureDraftsSnapshotRef] = useRetainedBucket<Record<number, InterviewKnowledgeCaptureDraft>>({});
  const [interviewPreparationAttempts, setInterviewPreparationAttempts, interviewPreparationAttemptsSnapshotRef] = useRetainedBucket<Record<string, InterviewPreparationAttemptState>>({});
  const [interviewPreparationDrafts, setInterviewPreparationDrafts, interviewPreparationDraftsSnapshotRef] = useRetainedBucket<Record<string, InterviewPreparationDraft>>({});
  const [interviewStoryDrafts, setInterviewStoryDrafts, interviewStoryDraftsRef] = useRetainedBucket<Record<string, InterviewStoryDraft>>({});
  return {
    adaptivePracticeDrafts, setAdaptivePracticeDrafts, adaptivePracticeDraftsRef: adaptivePracticeDraftsSnapshotRef, interviewReviewProposalAttempts, setInterviewReviewProposalAttempts, reviewReadinessDrafts, setReviewReadinessDrafts, reviewReadinessDraftsRef: reviewReadinessDraftsSnapshotRef, interviewKnowledgeCaptureDrafts, setInterviewKnowledgeCaptureDrafts, interviewPreparationAttempts, setInterviewPreparationAttempts, interviewPreparationDrafts, setInterviewPreparationDrafts, interviewStoryDraftsRef, interviewStoryDrafts, setInterviewStoryDrafts,
    readSafety: () => interviewRetainedWorkSafety({
      adaptivePractice: adaptivePracticeDraftsSnapshotRef.current,
      reviewReadiness: reviewReadinessDraftsSnapshotRef.current,
      knowledgeCapture: interviewKnowledgeCaptureDraftsSnapshotRef.current,
      interviewPreparation: interviewPreparationDraftsSnapshotRef.current,
      interviewStory: interviewStoryDraftsRef.current,
      preparationAttempts: interviewPreparationAttemptsSnapshotRef.current,
      reviewAttempts: interviewReviewProposalAttemptsSnapshotRef.current,
    }),
  };
}
