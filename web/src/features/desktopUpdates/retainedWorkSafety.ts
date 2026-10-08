import type { InterviewKnowledgeCaptureDraft } from '@/components/InterviewKnowledgeCaptureDrawer';
import type { InterviewPreparationDraft, InterviewPreparationAttemptState } from '@/components/InterviewPreparationProposalDrawer';
import type { InterviewStoryDraft } from '@/components/InterviewStoryDrawer';
import type { OfferNegotiationDraft } from '@/components/OfferNegotiationDrawer';
import { isReviewReadinessDraftPending, isReviewReadinessDraftUnsaved, type ReviewReadinessOwnerDraft } from '@/features/reviewReadiness/contracts';
import type { AdaptivePracticeOwnerDraft } from '@/types/adaptiveInterviewPractice';
import type { ApplicationJdDraft } from '@/types/applicationJdVersion';
import type { InterviewStoryEditableContent } from '@/types/interviewStory';

export type ApplicationJdDraftBaseline = Pick<ApplicationJdDraft, 'jdText' | 'sourceUrl'>;

/** Called only for the editor's first complete, settled open-time snapshot. */
export function applicationJdBaselineForOpen(
  previous: ApplicationJdDraft | undefined,
  patch: Partial<ApplicationJdDraft>,
): ApplicationJdDraftBaseline | undefined {
  if (previous || typeof patch.jdText !== 'string' || typeof patch.sourceUrl !== 'string'
    || !(patch.expectedCurrentVersionId === null || (Number.isSafeInteger(patch.expectedCurrentVersionId) && Number(patch.expectedCurrentVersionId) > 0))
    || patch.idempotencyKey !== null || patch.resultUnknown !== false || patch.pendingOperation !== null) return undefined;
  return { jdText: patch.jdText, sourceUrl: patch.sourceUrl };
}

export interface RetainedWorkBuckets {
  adaptivePractice: Readonly<Record<string, AdaptivePracticeOwnerDraft>>;
  applicationJd: Readonly<Record<number, ApplicationJdDraft>>;
  applicationJdBaselines?: ReadonlyMap<number, ApplicationJdDraftBaseline>;
  offerNegotiation: Readonly<Record<number, OfferNegotiationDraft>>;
  offerNegotiationPilot: Readonly<Record<number, OfferNegotiationDraft>>;
  reviewReadiness: Readonly<Record<string, ReviewReadinessOwnerDraft>>;
  knowledgeCapture: Readonly<Record<number, InterviewKnowledgeCaptureDraft>>;
  interviewPreparation: Readonly<Record<string, InterviewPreparationDraft>>;
  interviewStory: Readonly<Record<string, InterviewStoryDraft>>;
  preparationAttempts: Readonly<Record<string, InterviewPreparationAttemptState>>;
  reviewAttempts: Readonly<Record<number, { result_unknown: boolean }>>;
}

const contentHasText = (content: InterviewStoryEditableContent | null) => Boolean(content && (
  content.title || content.blocks.some((block) => block.text)
  || content.capability_labels.some(Boolean) || content.applicable_questions.some(Boolean)
));

/** IDs, attempt keys, empty draft containers and settled receipts are not edits. */
export function retainedWorkSafety(buckets: RetainedWorkBuckets) {
  let hasDraft = false;
  let activeRun = Object.keys(buckets.preparationAttempts).length > 0 || Object.keys(buckets.reviewAttempts).length > 0;
  let pendingApproval = false;
  for (const draft of Object.values(buckets.adaptivePractice)) {
    hasDraft ||= Boolean(draft.answer || draft.reflection || draft.assessment || draft.startInput || draft.completionInput);
    activeRun ||= draft.pendingOperation !== null || draft.resultUnknown;
  }
  for (const [applicationId, draft] of Object.entries(buckets.applicationJd)) {
    const baseline = buckets.applicationJdBaselines?.get(Number(applicationId));
    hasDraft ||= baseline
      ? draft.jdText !== baseline.jdText || draft.sourceUrl !== baseline.sourceUrl
      : Boolean(draft.jdText || draft.sourceUrl);
    activeRun ||= draft.pendingOperation !== null || draft.resultUnknown;
  }
  for (const draft of [...Object.values(buckets.offerNegotiation), ...Object.values(buckets.offerNegotiationPilot)]) {
    hasDraft ||= Boolean(draft.goal || draft.concerns || draft.scenario || draft.selectedBlocks.length
      || Object.keys(draft.edits).length || draft.previewInputKey || draft.previewSnapshot);
    activeRun ||= draft.pendingOperation !== null || draft.resultUnknown;
  }
  for (const draft of Object.values(buckets.reviewReadiness)) {
    hasDraft ||= isReviewReadinessDraftUnsaved(draft);
    pendingApproval ||= draft.actionDraft?.status === 'proposed';
    activeRun ||= isReviewReadinessDraftPending(draft) && draft.actionDraft?.status !== 'proposed';
    activeRun ||= Boolean(draft.actionDraft?.pendingDecision || draft.actionDraft?.resultUnknown
      || draft.actionDraft?.undoRequest || draft.actionDraft?.undoResultUnknown);
  }
  for (const draft of Object.values(buckets.knowledgeCapture)) {
    activeRun ||= ['ai_generating', 'provider_unknown', 'confirm_unknown'].includes(draft.previewStatus);
    if (draft.previewStatus !== 'confirmed') {
      hasDraft ||= Boolean(draft.selectedFragments.length || draft.canonicalFragments.length || draft.preview || draft.editedBlocks.length);
    }
  }
  for (const draft of Object.values(buckets.interviewPreparation)) {
    const hasSavedReadonlyJd = Number.isSafeInteger(draft.jdVersionId) && Number(draft.jdVersionId) > 0;
    hasDraft ||= Boolean((!hasSavedReadonlyJd && draft.jdText) || draft.assertionsText || draft.knowledgeSelections.length
      || draft.readinessFeedbackSelection?.orderedVersionIds.length);
    activeRun ||= draft.attemptState.result_unknown;
  }
  for (const draft of Object.values(buckets.interviewStory)) {
    const action = draft.productAction;
    activeRun ||= Boolean(draft.resultUnknown || draft.pendingOperation || action?.pendingDecision
      || action?.resultUnknown || action?.undoRequest || action?.undoResultUnknown);
    pendingApproval ||= action?.status === 'proposed';
    if (action?.status === 'committed') continue;
    hasDraft ||= Boolean(draft.selections.length || draft.assertions.some(Boolean)
      || Object.keys(draft.manualEvidenceBindings).length || draft.manualSavePayload || draft.proposalInput || draft.proposal
      || contentHasText(draft.manualContent) || contentHasText(draft.editedContent));
  }
  return { hasDraft, activeRun, pendingApproval };
}

/** These navigation guards count even unchanged mount-time draft buckets. */
export function desktopUpdateTaskGuard(
  taskId: string | undefined,
  guard: { pending: boolean; unsaved: boolean },
  retained: { hasDraft: boolean; activeRun: boolean; pendingApproval: boolean },
) {
  return taskId === 'application.offer_review' || taskId === 'application.interview_prepare'
    ? { pending: guard.pending || retained.activeRun || retained.pendingApproval, unsaved: retained.hasDraft }
    : guard;
}
