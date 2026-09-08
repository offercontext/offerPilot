import type { OpportunityFitReview } from '@/types/opportunityFitReview';

export type OpportunityFitDraftPhase =
  | 'collect_input'
  | 'confirm_triage'
  | 'triage_loading'
  | 'triage_ready'
  | 'confirm_deep_review'
  | 'deep_review_loading'
  | 'deep_review_ready'
  | 'material_handoff';

export interface OpportunityFitDraftState {
  applicationId: number;
  pilotDraftKey: string;
  phase: OpportunityFitDraftPhase;
  resumeID?: number;
  jdText: string;
  assertionsText: string;
  review: OpportunityFitReview | null;
  actionError: string | null;
  triageAttemptKey: string | null;
  triageFailureDisposition: OpportunityFitDraftErrorDisposition | null;
  reviewSource: 'live' | 'historical';
}

export interface OpportunityFitResumeEvidenceProof {
  resumeId: number;
  sha256: string;
  contentJson: unknown;
}

export type OpportunityFitDraftErrorDisposition = 'unknown' | 'definite_no_write';

export type OpportunityFitAssertionsNormalizationErrorCode =
  | 'too_many_assertions'
  | 'assertion_too_long';

export class OpportunityFitAssertionsNormalizationError extends Error {
  readonly code: OpportunityFitAssertionsNormalizationErrorCode;
  readonly index?: number;

  constructor(
    code: OpportunityFitAssertionsNormalizationErrorCode,
    message: string,
    index?: number,
  ) {
    super(message);
    this.name = 'OpportunityFitAssertionsNormalizationError';
    this.code = code;
    this.index = index;
  }
}

export type OpportunityFitDraftAction =
  | { type: 'set_resume'; resumeID?: number }
  | { type: 'set_jd'; jdText: string }
  | { type: 'set_assertions'; assertionsText: string }
  | { type: 'set_phase'; phase: OpportunityFitDraftPhase }
  | { type: 'set_attempt_key'; key: string | null }
  | { type: 'set_review'; review: OpportunityFitReview }
  | { type: 'restore_review'; review: OpportunityFitReview }
  | { type: 'reset_for_new_review' }
  | { type: 'set_error'; error: string; disposition: OpportunityFitDraftErrorDisposition }
  | { type: 'set_error'; error: null; disposition: null };

export interface OpportunityFitDraftStore {
  getState: () => OpportunityFitDraftState;
  dispatch: (action: OpportunityFitDraftAction) => void;
  subscribe: (listener: () => void) => () => void;
}

export function removeOpportunityFitDraftStore(
  stores: Map<string, OpportunityFitDraftStore>,
  key: string,
  expectedStore?: OpportunityFitDraftStore,
): boolean {
  if (expectedStore && stores.get(key) !== expectedStore) return false;
  return stores.delete(key);
}

export function shouldRetainOpportunityFitDraft(state: OpportunityFitDraftState): boolean {
  return Boolean(
    state.triageAttemptKey
    && (state.phase === 'triage_loading' || state.triageFailureDisposition === 'unknown'),
  );
}

const OPPORTUNITY_FIT_DRAFT_PHASES: ReadonlySet<string> = new Set([
  'collect_input',
  'confirm_triage',
  'triage_loading',
  'triage_ready',
  'confirm_deep_review',
  'deep_review_loading',
  'deep_review_ready',
  'material_handoff',
]);

export function isOpportunityFitDraftPhase(value: unknown): value is OpportunityFitDraftPhase {
  return typeof value === 'string' && OPPORTUNITY_FIT_DRAFT_PHASES.has(value);
}

export function createInitialOpportunityFitDraft(
  applicationId: number,
  pilotDraftKey: string,
): OpportunityFitDraftState {
  return {
    applicationId,
    pilotDraftKey,
    phase: 'collect_input',
    resumeID: undefined,
    jdText: '',
    assertionsText: '',
    review: null,
    actionError: null,
    triageAttemptKey: null,
    triageFailureDisposition: null,
    reviewSource: 'live',
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isOptionalNumber(value: unknown): value is number | undefined {
  return value === undefined || (typeof value === 'number' && Number.isFinite(value));
}

function isOpportunityFitRecommendation(value: unknown): value is OpportunityFitReview['recommendation'] {
  return value === 'advance' || value === 'hold' || value === 'decline';
}

type EvidenceSource = 'jd' | 'resume' | 'user_assertion';

const EMPTY_OPPORTUNITY_FIT_SUMMARY =
  'No source-backed candidate conclusion is available; clarify before proceeding.';

function isCanonicalPathSegment(segment: string): boolean {
  return segment.length > 0
    && segment !== '-'
    && (!/^\d+$/.test(segment) || segment === '0' || !segment.startsWith('0'));
}

function getResumeStringAtPath(content: unknown, path: string): string | undefined {
  let current = content;
  for (const segment of path.slice(1).split('/')) {
    if (!isCanonicalPathSegment(segment)) return undefined;
    if (Array.isArray(current) && /^\d+$/.test(segment)) {
      const index = Number(segment);
      if (index >= current.length) return undefined;
      current = current[index];
    } else if (isRecord(current) && Object.prototype.hasOwnProperty.call(current, segment)) {
      current = current[segment];
    } else {
      return undefined;
    }
  }
  return typeof current === 'string' ? current : undefined;
}

function isValidResumeEvidenceProof(value: unknown): value is OpportunityFitResumeEvidenceProof {
  return isRecord(value)
    && typeof value.resumeId === 'number'
    && Number.isFinite(value.resumeId)
    && typeof value.sha256 === 'string'
    && value.sha256.trim().length > 0
    && Object.prototype.hasOwnProperty.call(value, 'contentJson');
}

function isValidResumeEvidence(
  ref: Record<string, unknown>,
  source: Record<string, unknown>,
  options: OpportunityFitReviewValidationOptions,
): boolean {
  const path = ref.path;
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('/content_json')) return false;
  const segments = path.slice(1).split('/');
  if (segments.some((segment) => !isCanonicalPathSegment(segment))) return false;

  const resume = source.resume;
  if (!isRecord(resume)) return false;
  const proof = options.resumeEvidenceProof;
  if (proof) {
    if (!isValidResumeEvidenceProof(proof)) return false;
    if (proof.resumeId !== resume.id || proof.sha256 !== resume.sha256) return false;
    return getResumeStringAtPath(proof.contentJson, path) === ref.excerpt;
  }
  return !options.requireResumeEvidenceProof;
}

export interface OpportunityFitReviewValidationOptions {
  resumeEvidenceProof?: OpportunityFitResumeEvidenceProof;
  requireResumeEvidenceProof?: boolean;
}

function isValidEvidenceRef(
  value: unknown,
  source: Record<string, unknown>,
  allowJd: boolean,
  options: OpportunityFitReviewValidationOptions,
): value is { source: EvidenceSource; path: string; excerpt: string } {
  if (!isRecord(value)
    || (value.source !== 'jd' && value.source !== 'resume' && value.source !== 'user_assertion')
    || typeof value.path !== 'string'
    || typeof value.excerpt !== 'string'
    || !value.excerpt.trim()) {
    return false;
  }

  if (value.source === 'jd') {
    return allowJd
      && value.path === '/text'
      && isRecord(source.jd)
      && value.excerpt === source.jd.text;
  }
  if (value.source === 'resume') return isValidResumeEvidence(value, source, options);

  if (!/^\/user_assertions\/(0|[1-9]\d*)\/text$/.test(value.path)) return false;
  const index = Number(value.path.split('/')[2]);
  const assertions = source.candidate_assertions;
  return Array.isArray(assertions)
    && isRecord(assertions[index])
    && value.excerpt === assertions[index].text;
}

export function isValidOpportunityFitEvidenceRefs(
  value: unknown,
  source: Record<string, unknown>,
  options: { allowJd: boolean; requireNonEmpty?: boolean } & OpportunityFitReviewValidationOptions,
): boolean {
  return Array.isArray(value)
    && (!options.requireNonEmpty || value.length > 0)
    && value.every((item) => isValidEvidenceRef(item, source, options.allowJd, options));
}

function hasEvidenceSource(value: unknown, source: EvidenceSource): boolean {
  return Array.isArray(value)
    && value.some((item) => isRecord(item) && item.source === source);
}

function isOpportunityFitSummary(
  value: unknown,
  source: Record<string, unknown>,
  options: OpportunityFitReviewValidationOptions,
): boolean {
  return isRecord(value)
    && typeof value.text === 'string'
    && Array.isArray(value.evidence_refs)
    && isValidOpportunityFitEvidenceRefs(value.evidence_refs, source, { allowJd: true, ...options })
    && (value.evidence_refs.length > 0 || value.text === EMPTY_OPPORTUNITY_FIT_SUMMARY);
}

function isOpportunityFitConstraintStatus(value: unknown): boolean {
  return value === 'met' || value === 'unmet' || value === 'unknown';
}

function isOpportunityFitGapKind(value: unknown): boolean {
  return value === 'required' || value === 'preferred';
}

function isValidOpportunityFitTriage(
  value: unknown,
  source: Record<string, unknown>,
  options: OpportunityFitReviewValidationOptions,
): value is OpportunityFitReview['triage'] {
  if (!isRecord(value)) {
    return false;
  }

  return (
    isOpportunityFitSummary(value.summary, source, options)
    && isOpportunityFitRecommendation(value.recommendation)
    && Array.isArray(value.hard_constraints)
    && value.hard_constraints.every((item) => (
      isRecord(item)
      && typeof item.id === 'string'
      && typeof item.requirement === 'string'
      && isOpportunityFitConstraintStatus(item.status)
      && typeof item.explanation === 'string'
      && isValidOpportunityFitEvidenceRefs(item.evidence_refs, source, {
        allowJd: true,
        requireNonEmpty: true,
        ...options,
      })
      && hasEvidenceSource(item.evidence_refs, 'jd')
      && (item.status === 'unknown'
        || (hasEvidenceSource(item.evidence_refs, 'resume')
          || hasEvidenceSource(item.evidence_refs, 'user_assertion')))
    ))
    && Array.isArray(value.fit_signals)
    && value.fit_signals.every((item) => (
      isRecord(item)
      && typeof item.id === 'string'
      && typeof item.statement === 'string'
      && isValidOpportunityFitEvidenceRefs(item.evidence_refs, source, { allowJd: false, requireNonEmpty: true, ...options })
    ))
    && Array.isArray(value.gaps)
    && value.gaps.every((item) => (
      isRecord(item)
      && typeof item.id === 'string'
      && typeof item.requirement === 'string'
      && isOpportunityFitGapKind(item.kind)
      && isOpportunityFitConstraintStatus(item.candidate_status)
      && isValidOpportunityFitEvidenceRefs(item.evidence_refs, source, {
        allowJd: true,
        requireNonEmpty: true,
        ...options,
      })
      && hasEvidenceSource(item.evidence_refs, 'jd')
      && (item.candidate_status === 'unknown'
        || (hasEvidenceSource(item.evidence_refs, 'resume')
          || hasEvidenceSource(item.evidence_refs, 'user_assertion')))
    ))
    && isRecord(value.deadline)
    && (value.deadline.status === 'stated' || value.deadline.status === 'not_stated')
    && typeof value.deadline.text === 'string'
    && (value.deadline.status === 'not_stated'
      ? value.deadline.text === '' && isValidOpportunityFitEvidenceRefs(value.deadline.evidence_refs, source, { allowJd: true, ...options })
      : Boolean(value.deadline.text.trim())
        && isValidOpportunityFitEvidenceRefs(value.deadline.evidence_refs, source, { allowJd: true, requireNonEmpty: true, ...options }))
    && Array.isArray(value.next_questions)
    && value.next_questions.every((item) => typeof item === 'string')
  );
}

function isValidOpportunityFitDeepReview(
  value: unknown,
  source: Record<string, unknown>,
  options: OpportunityFitReviewValidationOptions,
): value is NonNullable<OpportunityFitReview['deep_review']> {
  if (!isRecord(value)) {
    return false;
  }

  return (
    Array.isArray(value.strengths)
    && value.strengths.every((item) => (
      isRecord(item)
      && typeof item.id === 'string'
      && typeof item.statement === 'string'
      && isValidOpportunityFitEvidenceRefs(item.evidence_refs, source, { allowJd: false, requireNonEmpty: true, ...options })
    ))
    && Array.isArray(value.gaps_to_address)
    && value.gaps_to_address.every((item) => (
      isRecord(item)
      && typeof item.id === 'string'
      && typeof item.statement === 'string'
      && isValidOpportunityFitEvidenceRefs(item.evidence_refs, source, { allowJd: true, requireNonEmpty: true, ...options })
    ))
    && Array.isArray(value.questions_to_clarify)
    && value.questions_to_clarify.every((item) => (
      isRecord(item)
      && typeof item.id === 'string'
      && typeof item.statement === 'string'
      && isValidOpportunityFitEvidenceRefs(item.evidence_refs, source, { allowJd: true, ...options })
    ))
    && (value.recommended_path === 'prepare_materials'
      || value.recommended_path === 'clarify_first'
      || value.recommended_path === 'do_not_pursue')
    && Array.isArray(value.next_actions)
    && value.next_actions.every((item) => (
      isRecord(item)
      && typeof item.id === 'string'
      && typeof item.label === 'string'
      && (item.kind === 'open_material_kit'
        || item.kind === 'add_assertion'
        || item.kind === 'record_deadline')
    ))
  );
}

export function isValidOpportunityFitReview(
  value: unknown,
  options: OpportunityFitReviewValidationOptions = {},
): value is OpportunityFitReview {
  if (!isRecord(value)) {
    return false;
  }

  const source = value.source;
  const triage = value.triage;
  const hasValidDeepReview = value.deep_review === null
    || (isRecord(source) && isValidOpportunityFitDeepReview(value.deep_review, source, options));
  return (
    typeof value.id === 'number'
    && Number.isFinite(value.id)
    && typeof value.application_id === 'number'
    && Number.isFinite(value.application_id)
    && (value.resume_id === null || (typeof value.resume_id === 'number' && Number.isFinite(value.resume_id)))
    && (value.status === 'triage_complete' || value.status === 'deep_reviewed')
    && isOpportunityFitRecommendation(value.recommendation)
    && isRecord(source)
    && isOpportunityFitSummary(value.summary, source, options)
    && isRecord(source.application)
    && typeof source.application.id === 'number'
    && Number.isFinite(source.application.id)
    && typeof source.application.company_name === 'string'
    && source.application.company_name.trim().length > 0
    && typeof source.application.position_name === 'string'
    && source.application.position_name.trim().length > 0
    && isRecord(source.resume)
    && typeof source.resume.id === 'number'
    && Number.isFinite(source.resume.id)
    && typeof source.resume.title === 'string'
    && source.resume.title.trim().length > 0
    && typeof source.resume.sha256 === 'string'
    && source.resume.sha256.trim().length > 0
    && isRecord(source.jd)
    && typeof source.jd.source_label === 'string'
    && source.jd.source_label.trim().length > 0
    && typeof source.jd.sha256 === 'string'
    && source.jd.sha256.trim().length > 0
    && typeof source.jd.text === 'string'
    && source.jd.text.trim().length > 0
    && source.application.id === value.application_id
    && value.resume_id !== null
    && source.resume.id === value.resume_id
    && Array.isArray(source.candidate_assertions)
    && source.candidate_assertions.every((item, index) => (
      isRecord(item)
      && typeof item.index === 'number'
      && Number.isInteger(item.index)
      && item.index >= 0
      && item.index === index
      && typeof item.text === 'string'
      && item.text.trim().length > 0
    ))
    && isValidOpportunityFitTriage(triage, source, options)
    && hasValidDeepReview
    && (value.status === 'triage_complete'
      ? value.deep_review === null
      : value.deep_review !== null && hasValidDeepReview)
  );
}

function isValidOpportunityFitDraftState(value: unknown): value is OpportunityFitDraftState {
  if (!isRecord(value)) {
    return false;
  }

  return (
    typeof value.applicationId === 'number'
    && Number.isFinite(value.applicationId)
    && typeof value.pilotDraftKey === 'string'
    && isOpportunityFitDraftPhase(value.phase)
    && isOptionalNumber(value.resumeID)
    && typeof value.jdText === 'string'
    && typeof value.assertionsText === 'string'
    && (value.review === null || isRecord(value.review))
    && (value.actionError === null || typeof value.actionError === 'string')
    && (value.triageAttemptKey === null || typeof value.triageAttemptKey === 'string')
    && (value.triageFailureDisposition === null
      || value.triageFailureDisposition === 'unknown'
      || value.triageFailureDisposition === 'definite_no_write')
    && (value.reviewSource === 'live' || value.reviewSource === 'historical')
  );
}

function isValidOpportunityFitDraftAction(action: OpportunityFitDraftAction): boolean {
  if (!isRecord(action) || typeof action.type !== 'string') {
    return false;
  }

  switch (action.type) {
    case 'set_resume':
      return isOptionalNumber(action.resumeID);
    case 'set_jd':
      return typeof action.jdText === 'string';
    case 'set_assertions':
      return typeof action.assertionsText === 'string';
    case 'set_phase':
      return isOpportunityFitDraftPhase(action.phase);
    case 'set_attempt_key':
      return action.key === null || typeof action.key === 'string';
    case 'set_review':
      return isValidOpportunityFitReview(action.review);
    case 'restore_review':
      return isValidOpportunityFitReview(action.review);
    case 'reset_for_new_review':
      return true;
    case 'set_error':
      return action.error === null
        ? action.disposition === null
        : (action.disposition === 'unknown' || action.disposition === 'definite_no_write');
    default:
      return false;
  }
}

export function classifyOpportunityFitFailure(
  failure: unknown,
): OpportunityFitDraftErrorDisposition {
  if (!isRecord(failure) || !isRecord(failure.response)) {
    return 'unknown';
  }

  const response = failure.response;
  if (response.status === 422 || response.status === 404) {
    return 'definite_no_write';
  }

  if (response.status !== 502 || !isRecord(response.data)) {
    return 'unknown';
  }

  return response.data.error_code === 'opportunity_fit_provider_error'
    || response.data.error_code === 'opportunity_fit_unverifiable'
    ? 'definite_no_write'
    : 'unknown';
}

export function normalizeOpportunityFitAssertions(raw: string): string[] {
  const assertions = raw
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);

  if (assertions.length > 10) {
    throw new OpportunityFitAssertionsNormalizationError(
      'too_many_assertions',
      'At most 10 assertions are allowed.',
    );
  }

  const overlongIndex = assertions.findIndex((value) => value.length > 500);
  if (overlongIndex !== -1) {
    throw new OpportunityFitAssertionsNormalizationError(
      'assertion_too_long',
      'Each assertion must be at most 500 characters.',
      overlongIndex,
    );
  }

  return assertions;
}

type OpportunityFitDraftInputField = 'resumeID' | 'jdText' | 'assertionsText';

function updateDraftInput(
  state: OpportunityFitDraftState,
  field: OpportunityFitDraftInputField,
  value: OpportunityFitDraftState[OpportunityFitDraftInputField],
): OpportunityFitDraftState {
  return {
    ...state,
    [field]: value,
    actionError: null,
    triageAttemptKey: null,
    triageFailureDisposition: null,
    reviewSource: 'live',
  };
}

export function opportunityFitDraftReducer(
  state: OpportunityFitDraftState,
  action: OpportunityFitDraftAction,
): OpportunityFitDraftState {
  if (!isValidOpportunityFitDraftState(state) || !isValidOpportunityFitDraftAction(action)) {
    return state;
  }

  switch (action.type) {
    case 'set_resume':
      if (state.resumeID === action.resumeID) {
        return state;
      }
      return updateDraftInput(state, 'resumeID', action.resumeID);
    case 'set_jd':
      if (state.jdText === action.jdText) {
        return state;
      }
      return updateDraftInput(state, 'jdText', action.jdText);
    case 'set_assertions':
      if (state.assertionsText === action.assertionsText) {
        return state;
      }
      return updateDraftInput(state, 'assertionsText', action.assertionsText);
    case 'set_phase':
      if (state.phase === action.phase) {
        return state;
      }
      return { ...state, phase: action.phase };
    case 'set_attempt_key':
      if (state.triageAttemptKey === action.key) {
        return state;
      }
      return { ...state, triageAttemptKey: action.key };
    case 'set_review':
      if (action.review.application_id !== state.applicationId) {
        return state;
      }
      return {
        ...state,
        phase: action.review.deep_review === null ? 'triage_ready' : 'deep_review_ready',
        review: action.review,
        actionError: null,
        triageAttemptKey: null,
        triageFailureDisposition: null,
      };
    case 'restore_review':
      if (action.review.application_id !== state.applicationId || state.triageAttemptKey !== null) {
        return state;
      }
      return {
        ...state,
        phase: action.review.deep_review === null ? 'triage_ready' : 'deep_review_ready',
        review: action.review,
        actionError: null,
        triageAttemptKey: null,
        triageFailureDisposition: null,
        reviewSource: 'historical',
      };
    case 'reset_for_new_review':
      return createInitialOpportunityFitDraft(state.applicationId, state.pilotDraftKey);
    case 'set_error':
      if (
        state.actionError === action.error
        && (action.disposition !== 'definite_no_write' || state.triageAttemptKey === null)
      ) {
        return state;
      }
      return {
        ...state,
        actionError: action.error,
        triageFailureDisposition: action.disposition,
        triageAttemptKey: action.disposition === 'definite_no_write'
          ? null
          : state.triageAttemptKey,
      };
    default:
      return assertNever(action);
  }
}

function assertNever(value: never): never {
  throw new Error(`Unexpected OpportunityFitDraftAction: ${String(value)}`);
}

export function createOpportunityFitDraftStore(
  applicationId: number,
  pilotDraftKey: string,
): OpportunityFitDraftStore {
  let state = createInitialOpportunityFitDraft(applicationId, pilotDraftKey);
  const listeners = new Set<() => void>();

  return {
    getState: () => state,
    dispatch: (action) => {
      const nextState = opportunityFitDraftReducer(state, action);
      if (nextState === state) {
        return;
      }
      state = nextState;
      listeners.forEach((listener) => listener());
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
