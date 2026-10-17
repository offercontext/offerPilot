import { describe, expect, it } from 'vitest';
import { applicationJdBaselineForOpen, desktopUpdateTaskGuard, retainedWorkSafety, type RetainedWorkBuckets } from './retainedWorkSafety';
import type { InterviewStoryDraft } from '@/components/InterviewStoryDrawer';
import type { ApplicationJdDraft } from '@/types/applicationJdVersion';
import type { OfferNegotiationDraft } from '@/components/OfferNegotiationDrawer';
import type { ProductActionOwnerDraft } from '@/features/reviewReadiness/contracts';

const clean = { hasDraft: false, activeRun: false, pendingApproval: false };
const buckets = (): RetainedWorkBuckets => ({ adaptivePractice: {}, applicationJd: {}, offerNegotiation: {}, offerNegotiationPilot: {}, reviewReadiness: {}, knowledgeCapture: {}, interviewPreparation: {}, interviewStory: {}, preparationAttempts: {}, reviewAttempts: {} });
const story = (): InterviewStoryDraft => ({
  entrypoint: 'ui', applicationId: null, targetStoryId: null, expectedCurrentVersionId: null, expectedStoryRevision: null,
  selections: [], assertions: [], manualEvidenceBindings: {}, idempotencyKey: 'generated-on-open', attemptId: null,
  proposal: null, editedContent: null, manualContent: { title: '', blocks: [{kind: 'situation', text: '', fact_mode: 'evidence_backed'}], capability_labels: [], applicable_questions: [], fact_gap_codes: [] },
  manualSavePayload: null, proposalInput: null, resultUnknown: false, retryAvailableAt: null, pendingOperation: null,
  attemptGenerationRevision: 0, productActionGeneration: 0, productAction: null, serverConfirmationToken: null, error: null,
});
const offer = (): OfferNegotiationDraft => ({
  attemptKey: 'generated-on-open', confirmationKey: 'generated-on-open', goal: '', concerns: '', scenario: '',
  resultUnknown: false, pendingOperation: null, proposalId: null, selectedBlocks: [], edits: {}, dimensionIds: [1, 2],
  sourceFingerprint: null, previewSnapshot: null, previewInputKey: null,
});
const action = (status: ProductActionOwnerDraft['status']): ProductActionOwnerDraft => ({
  ownerKey: 'owner', operationId: 'op', actionCallId: 'call', actionName: 'save_review_readiness_signal',
  confirmationToken: null, allowedDecisions: [], status, result: {}, originalPayload: {}, pendingDecision: null, resultUnknown: false,
});

describe('retained draft update guards', () => {
  it('uses actual Offer draft state for update checks without mutating the existing navigation guard', () => {
    const navigationGuard = { pending: false, unsaved: true };
    expect(desktopUpdateTaskGuard('application.offer_review', navigationGuard, clean)).toEqual({ pending: false, unsaved: false });
    expect(navigationGuard.unsaved).toBe(true);
    expect(desktopUpdateTaskGuard('application.material_kit', navigationGuard, clean)).toBe(navigationGuard);
    expect(desktopUpdateTaskGuard('application.offer_review', navigationGuard, { ...clean, hasDraft: true }).unsaved).toBe(true);
  });

  it('does not block merely opening Preparation with an existing readonly JD', () => {
    const value = buckets();
    value.interviewPreparation = { task: {
      attemptState: { key: 'open-only', result_unknown: false }, resumeId: 3,
      jdText: 'Previously saved JD', jdVersionId: 4, assertionsText: '', knowledgeSelections: [],
    } };
    expect(retainedWorkSafety(value)).toEqual(clean);
    expect(desktopUpdateTaskGuard('application.interview_prepare', { pending: false, unsaved: true }, retainedWorkSafety(value))).toEqual({ pending: false, unsaved: false });
  });

  it.each(['assertions', 'selections', 'unknown', 'unversioned-jd'])('still blocks Preparation %s', (kind) => {
    const value = buckets();
    const draft = {
      attemptState: { key: 'open-only', result_unknown: kind === 'unknown' }, resumeId: 3,
      jdText: 'Previously saved JD', jdVersionId: kind === 'unversioned-jd' ? null : 4,
      assertionsText: kind === 'assertions' ? 'New context' : '',
      knowledgeSelections: kind === 'selections' ? [{ note_version_id: 7 }] : [],
    };
    value.interviewPreparation = { task: draft };
    const result = retainedWorkSafety(value);
    expect(result.hasDraft || result.activeRun).toBe(true);
  });

  it('captures the JD open-time baseline and permits an unchanged saved copy', () => {
    const draft: ApplicationJdDraft = { jdText: 'Saved JD', sourceUrl: 'https://example.test/job', expectedCurrentVersionId: 4, idempotencyKey: null, resultUnknown: false, pendingOperation: null };
    const baseline = applicationJdBaselineForOpen(undefined, draft);
    expect(baseline).toEqual({ jdText: draft.jdText, sourceUrl: draft.sourceUrl });
    const value = buckets();
    value.applicationJd = { 1: draft };
    value.applicationJdBaselines = new Map([[1, baseline!]]);
    expect(retainedWorkSafety(value)).toEqual(clean);
    draft.expectedCurrentVersionId = 5;
    expect(retainedWorkSafety(value)).toEqual(clean);
    draft.jdText = '';
    expect(retainedWorkSafety(value).hasDraft).toBe(true);
    draft.jdText = baseline!.jdText;
    draft.sourceUrl = '';
    expect(retainedWorkSafety(value).hasDraft).toBe(true);
  });

  it('never replaces a JD baseline with edited or unresolved content', () => {
    const draft: ApplicationJdDraft = { jdText: 'Saved JD', sourceUrl: '', expectedCurrentVersionId: 4, idempotencyKey: null, resultUnknown: false, pendingOperation: null };
    expect(applicationJdBaselineForOpen(draft, { ...draft, jdText: 'new edit' })).toBeUndefined();
    expect(applicationJdBaselineForOpen(undefined, { jdText: 'partial user edit' })).toBeUndefined();
    expect(applicationJdBaselineForOpen(undefined, { ...draft, pendingOperation: 'save' })).toBeUndefined();
    const value = buckets();
    value.applicationJd = { 1: { ...draft, resultUnknown: true } };
    value.applicationJdBaselines = new Map([[1, { jdText: draft.jdText, sourceUrl: draft.sourceUrl }]]);
    expect(retainedWorkSafety(value).activeRun).toBe(true);
  });

  it.each(['rejected', 'failed'] as const)('preserves unsaved Story edits after a %s action', (status) => {
    const value = buckets();
    const draft = story();
    draft.manualContent.title = 'still unsaved';
    draft.productAction = { ...action(status), actionName: 'confirm_interview_story' };
    value.interviewStory = { ui: draft };
    expect(retainedWorkSafety(value).hasDraft).toBe(true);
  });

  it('does not treat merely opening an empty Story or Offer workspace as unsaved work', () => {
    const value = buckets();
    value.interviewStory = { ui: story() };
    value.offerNegotiation = { 1: offer() };
    value.offerNegotiationPilot = { 2: offer() };
    expect(retainedWorkSafety(value)).toEqual(clean);
  });

  it('reuses Review Readiness domain guards so a committed receipt is not a draft', () => {
    const value = buckets();
    value.reviewReadiness = { owner: {
      ownerKey: 'owner', ownerGeneration: 1, noteId: 1, proposalId: 1, applicationId: 1,
      selectedFocusId: 'old selection', userNote: 'saved note', idempotencyKey: 'old attempt', frozenProposalInput: null,
      proposalUnknown: false, actionDraft: action('committed'),
    } };
    expect(retainedWorkSafety(value)).toEqual(clean);
    value.reviewReadiness.owner.actionDraft!.undoResultUnknown = true;
    expect(retainedWorkSafety(value).activeRun).toBe(true);
  });

  it('allows a committed Story receipt but blocks unresolved undo', () => {
    const value = buckets();
    const draft = story();
    draft.manualContent.title = 'saved story';
    draft.productAction = { ...action('committed'), actionName: 'confirm_interview_story' };
    value.interviewStory = { ui: draft };
    expect(retainedWorkSafety(value)).toEqual(clean);
    draft.productAction.undoResultUnknown = true;
    expect(retainedWorkSafety(value).activeRun).toBe(true);
  });

  it('preserves actual unsaved Story text and Offer inputs', () => {
    const value = buckets();
    const draft = story();
    draft.manualContent.blocks[0].text = 'unsaved experience';
    value.interviewStory = { ui: draft };
    expect(retainedWorkSafety(value).hasDraft).toBe(true);
    value.interviewStory = {};
    value.offerNegotiation = { 1: { ...offer(), concerns: 'unsaved concern' } };
    expect(retainedWorkSafety(value).hasDraft).toBe(true);
  });

  it('blocks unknown/pending work even when every visible input is empty', () => {
    const value = buckets();
    value.interviewStory = { ui: { ...story(), resultUnknown: true } };
    expect(retainedWorkSafety(value).activeRun).toBe(true);
    value.interviewStory = {};
    value.offerNegotiation = { 1: { ...offer(), pendingOperation: 'generate' } };
    expect(retainedWorkSafety(value).activeRun).toBe(true);
    value.offerNegotiation = {};
    value.preparationAttempts = { task: { key: 'in-flight', result_unknown: false } };
    expect(retainedWorkSafety(value).activeRun).toBe(true);
  });

  it('blocks a retained product approval independently from unsaved inputs', () => {
    const value = buckets();
    value.interviewStory = { ui: { ...story(), productAction: { ...action('proposed'), actionName: 'confirm_interview_story' } } };
    expect(retainedWorkSafety(value).pendingApproval).toBe(true);
  });
});
