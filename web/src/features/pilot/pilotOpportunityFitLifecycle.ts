import { getOpportunityFitErrorMessage } from '@/components/opportunityFitCopy';
import type { OpportunityFitReview } from '@/types/opportunityFitReview';
import {
  classifyOpportunityFitFailure,
  normalizeOpportunityFitAssertions,
  isValidOpportunityFitReview,
  type OpportunityFitDraftState,
  type OpportunityFitDraftStore,
  type OpportunityFitResumeEvidenceProof,
} from './opportunityFitDraft';

export interface PilotTriagePayload {
  resume_id: number;
  jd_text: string;
  jd_source_label: string;
  candidate_assertions: string[];
  idempotency_key: string;
}

interface PilotTriageRunOptions {
  store: OpportunityFitDraftStore;
  applicationId: number;
  pilotDraftKey: string;
  draft: OpportunityFitDraftState;
  existingKey: string | null;
  createReview: (applicationId: number, payload: PilotTriagePayload) => Promise<OpportunityFitReview>;
  resumeEvidenceProof?: OpportunityFitResumeEvidenceProof | null;
  isContextCurrent?: () => boolean;
  onNotFound?: () => void;
}

const requestGenerations = new WeakMap<OpportunityFitDraftStore, number>();
const deepReviewRequestGenerations = new WeakMap<OpportunityFitDraftStore, number>();

interface PilotDeepReviewRunOptions {
  store: OpportunityFitDraftStore;
  applicationId: number;
  pilotDraftKey: string;
  draft: OpportunityFitDraftState;
  review: OpportunityFitReview;
  createReview: (applicationId: number, reviewId: number) => Promise<OpportunityFitReview>;
  resumeEvidenceProof?: OpportunityFitResumeEvidenceProof | null;
  isContextCurrent?: () => boolean;
  onNotFound?: () => void;
}

const INVALID_RESPONSE_ERROR = getOpportunityFitErrorMessage({
  response: { status: 502, data: { error_code: 'opportunity_fit_unverifiable' } },
});
const UNKNOWN_RESULT_ERROR = 'opportunity_fit_result_unknown';
const NOT_FOUND_ERROR = getOpportunityFitErrorMessage({ response: { status: 404 } });

export function isOpportunityFitNotFoundError(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const response = (error as { response?: unknown }).response;
  if (typeof response !== 'object' || response === null) return false;
  return (response as { status?: unknown }).status === 404;
}

function isVerifiedReview(
  value: unknown,
  resumeEvidenceProof?: OpportunityFitResumeEvidenceProof | null,
): value is OpportunityFitReview {
  return isValidOpportunityFitReview(value, {
    resumeEvidenceProof: resumeEvidenceProof ?? undefined,
    requireResumeEvidenceProof: Boolean(resumeEvidenceProof),
  });
}

function isRunCurrent(
  options: PilotTriageRunOptions,
  attemptKey: string,
  generation: number,
  input: Pick<OpportunityFitDraftState, 'applicationId' | 'pilotDraftKey' | 'resumeID' | 'jdText' | 'assertionsText'>,
): boolean {
  const current = options.store.getState();
  return requestGenerations.get(options.store) === generation
    && options.isContextCurrent?.() !== false
    && current.applicationId === input.applicationId
    && current.pilotDraftKey === input.pilotDraftKey
    && current.resumeID === input.resumeID
    && current.jdText === input.jdText
    && current.assertionsText === input.assertionsText
    && current.triageAttemptKey === attemptKey;
}

export function cancelPilotTriage(
  store: OpportunityFitDraftStore,
  options: { preserveAttempt?: boolean } = {},
): boolean {
  const current = store.getState();
  const preserveAttempt = options.preserveAttempt ?? true;

  if (!preserveAttempt && current.triageAttemptKey) {
    requestGenerations.set(store, (requestGenerations.get(store) ?? 0) + 1);
    store.dispatch({ type: 'set_error', error: NOT_FOUND_ERROR, disposition: 'definite_no_write' });
    if (current.phase === 'triage_loading') {
      store.dispatch({ type: 'set_phase', phase: 'confirm_triage' });
    }
    return true;
  }

  if (current.phase !== 'triage_loading' || !current.triageAttemptKey) return false;

  requestGenerations.set(store, (requestGenerations.get(store) ?? 0) + 1);
  store.dispatch({
    type: 'set_error',
    error: UNKNOWN_RESULT_ERROR,
    disposition: 'unknown',
  });
  store.dispatch({ type: 'set_phase', phase: 'confirm_triage' });
  return true;
}

export function restorePilotHistoricalReview(
  store: OpportunityFitDraftStore,
  review: OpportunityFitReview,
): boolean {
  const current = store.getState();
  if (
    current.review !== null
    || current.triageAttemptKey !== null
    || current.phase === 'triage_loading'
    || current.resumeID !== undefined
    || current.jdText.trim()
    || current.assertionsText.trim()
    || !isVerifiedReview(review)
    || review.application_id !== current.applicationId
  ) {
    return false;
  }

  store.dispatch({ type: 'set_resume', resumeID: review.source.resume.id });
  store.dispatch({ type: 'set_jd', jdText: review.source.jd.text });
  store.dispatch({
    type: 'set_assertions',
    assertionsText: review.source.candidate_assertions.map((item) => item.text).join('\n'),
  });
  store.dispatch({ type: 'restore_review', review });
  return store.getState().review === review;
}

export async function runPilotTriage(options: PilotTriageRunOptions): Promise<void> {
  const candidateAssertions = normalizeOpportunityFitAssertions(options.draft.assertionsText);
  const normalizedInput = {
    applicationId: options.applicationId,
    pilotDraftKey: options.pilotDraftKey,
    resumeID: options.draft.resumeID,
    jdText: options.draft.jdText.trim(),
    assertionsText: candidateAssertions.join('\n'),
  };
  const triageAttemptKey = options.existingKey ?? options.draft.triageAttemptKey ?? crypto.randomUUID();
  const generation = (requestGenerations.get(options.store) ?? 0) + 1;
  requestGenerations.set(options.store, generation);

  options.store.dispatch({ type: 'set_attempt_key', key: triageAttemptKey });
  options.store.dispatch({ type: 'set_jd', jdText: normalizedInput.jdText });
  options.store.dispatch({ type: 'set_assertions', assertionsText: normalizedInput.assertionsText });
  options.store.dispatch({ type: 'set_attempt_key', key: triageAttemptKey });
  options.store.dispatch({ type: 'set_phase', phase: 'triage_loading' });

  const payload: PilotTriagePayload = {
    resume_id: normalizedInput.resumeID!,
    jd_text: normalizedInput.jdText,
    jd_source_label: '用户粘贴 JD',
    candidate_assertions: candidateAssertions,
    idempotency_key: triageAttemptKey,
  };

  try {
    const result = await options.createReview(options.applicationId, payload);
    if (!isRunCurrent(options, triageAttemptKey, generation, normalizedInput)) return;
    if (!isVerifiedReview(result, options.resumeEvidenceProof)) {
      options.store.dispatch({
        type: 'set_error',
        error: INVALID_RESPONSE_ERROR,
        disposition: 'unknown',
      });
      options.store.dispatch({ type: 'set_phase', phase: 'confirm_triage' });
      return;
    }
    options.store.dispatch({ type: 'set_review', review: result });
  } catch (error) {
    if (!isRunCurrent(options, triageAttemptKey, generation, normalizedInput)) return;
    if (isOpportunityFitNotFoundError(error)) {
      options.onNotFound?.();
      return;
    }
    const disposition = classifyOpportunityFitFailure(error);
    options.store.dispatch({
      type: 'set_error',
      error: getOpportunityFitErrorMessage(error),
      disposition,
    });
    options.store.dispatch({ type: 'set_phase', phase: 'confirm_triage' });
  }
}

function isDeepReviewRunCurrent(
  options: PilotDeepReviewRunOptions,
  generation: number,
): boolean {
  const current = options.store.getState();
  return deepReviewRequestGenerations.get(options.store) === generation
    && options.isContextCurrent?.() !== false
    && current.applicationId === options.applicationId
    && current.pilotDraftKey === options.pilotDraftKey
    && current.resumeID === options.draft.resumeID
    && current.jdText === options.draft.jdText
    && current.assertionsText === options.draft.assertionsText
    && current.review?.id === options.review.id
    && current.phase === 'deep_review_loading';
}

export async function runPilotDeepReview(options: PilotDeepReviewRunOptions): Promise<void> {
  const generation = (deepReviewRequestGenerations.get(options.store) ?? 0) + 1;
  deepReviewRequestGenerations.set(options.store, generation);

  options.store.dispatch({ type: 'set_error', error: null, disposition: null });
  options.store.dispatch({ type: 'set_phase', phase: 'deep_review_loading' });

  try {
    const result = await options.createReview(options.applicationId, options.review.id);
    if (!isDeepReviewRunCurrent(options, generation)) return;
    if (!isVerifiedReview(result, options.resumeEvidenceProof)) {
      options.store.dispatch({
        type: 'set_error',
        error: INVALID_RESPONSE_ERROR,
        disposition: 'unknown',
      });
      options.store.dispatch({ type: 'set_phase', phase: 'triage_ready' });
      return;
    }
    options.store.dispatch({ type: 'set_review', review: result });
  } catch (error) {
    if (!isDeepReviewRunCurrent(options, generation)) return;
    if (isOpportunityFitNotFoundError(error)) {
      options.onNotFound?.();
      return;
    }
    const disposition = classifyOpportunityFitFailure(error);
    options.store.dispatch({
      type: 'set_error',
      error: getOpportunityFitErrorMessage(error),
      disposition,
    });
    options.store.dispatch({ type: 'set_phase', phase: 'triage_ready' });
  }
}
