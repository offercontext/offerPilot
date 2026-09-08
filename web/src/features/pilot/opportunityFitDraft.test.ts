// @vitest-environment jsdom
import { act, createElement, useSyncExternalStore } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';

import type { OpportunityFitReview } from '@/types/opportunityFitReview';
import {
  classifyOpportunityFitFailure,
  createInitialOpportunityFitDraft,
  createOpportunityFitDraftStore,
  normalizeOpportunityFitAssertions,
  opportunityFitDraftReducer,
  removeOpportunityFitDraftStore,
  shouldRetainOpportunityFitDraft,
  isValidOpportunityFitReview,
  type OpportunityFitDraftStore,
} from './opportunityFitDraft';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const review: OpportunityFitReview = {
  schema_version: 1,
  id: 11,
  application_id: 7,
  resume_id: 3,
  status: 'triage_complete',
  summary: {
    text: 'Candidate has a plausible fit.',
    evidence_refs: [{ source: 'jd', path: '/text', excerpt: 'Build things.' }],
  },
  recommendation: 'advance',
  source_fingerprint_sha256: 'source-hash',
  triage_sha256: 'triage-hash',
  deep_review_sha256: null,
  created_at: '2026-07-22T00:00:00Z',
  deep_reviewed_at: null,
  source: {
    application: { id: 7, company_name: 'Example', position_name: 'Engineer' },
    resume: { id: 3, title: 'Resume', sha256: 'resume-hash' },
    jd: { source_label: 'Pasted JD', sha256: 'jd-hash', text: 'Build things.' },
    candidate_assertions: [{ index: 0, text: 'I shipped production systems.' }],
  },
  triage: {
    summary: { text: 'Candidate has a plausible fit.', evidence_refs: [{ source: 'jd', path: '/text', excerpt: 'Build things.' }] },
    recommendation: 'advance',
    hard_constraints: [],
    fit_signals: [{
      id: 'fit-1',
      statement: 'Has delivery experience.',
      evidence_refs: [{ source: 'resume', path: '/experience/0/highlights/0', excerpt: 'Strong delivery record.' }],
    }],
    gaps: [],
    deadline: { status: 'not_stated', text: '', evidence_refs: [] },
    next_questions: [],
  },
  deep_review: null,
};

const deepReview: NonNullable<OpportunityFitReview['deep_review']> = {
  strengths: [{ id: 'strength-1', statement: 'Strong delivery record.', evidence_refs: [{ source: 'resume', path: '/experience/0/highlights/0', excerpt: 'Strong delivery record.' }] }],
  gaps_to_address: [{
    id: 'gap-1',
    statement: 'Clarify system scale.',
    evidence_refs: [{ source: 'resume', path: '/experience/0', excerpt: 'Strong delivery record.' }],
  }],
  questions_to_clarify: [{ id: 'question-1', statement: 'What was the peak traffic?', evidence_refs: [] }],
  recommended_path: 'prepare_materials',
  next_actions: [{ id: 'action-1', label: 'Open material kit', kind: 'open_material_kit' }],
};

describe('opportunityFitDraftReducer', () => {
  it('creates a complete reusable draft state', () => {
    expect(createInitialOpportunityFitDraft(7, 'draft-1')).toEqual({
      applicationId: 7,
      pilotDraftKey: 'draft-1',
      phase: 'collect_input',
      resumeID: undefined,
      jdText: '',
      assertionsText: '',
      review: null,
      actionError: null,
      triageAttemptKey: null,
      triageFailureDisposition: null,
      reviewSource: 'live',
    });
  });

  it('returns the original object for an unknown action', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');

    const next = opportunityFitDraftReducer(state, { type: 'unknown' } as never);

    expect(next).toBe(state);
  });

  it('returns the original object for an invalid phase', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');

    const next = opportunityFitDraftReducer(state, {
      type: 'set_phase',
      phase: 'not-a-phase',
    } as never);

    expect(next).toBe(state);
  });

  it('invalidates the attempt key when input changes', () => {
    const withKey = opportunityFitDraftReducer(
      createInitialOpportunityFitDraft(7, 'draft-1'),
      { type: 'set_attempt_key', key: 'attempt-1' },
    );

    const withResume = opportunityFitDraftReducer(withKey, { type: 'set_resume', resumeID: 3 });
    const withJd = opportunityFitDraftReducer(withKey, { type: 'set_jd', jdText: 'Build things.' });
    const withAssertions = opportunityFitDraftReducer(withKey, {
      type: 'set_assertions',
      assertionsText: 'I have shipped production systems.',
    });

    expect(withResume.triageAttemptKey).toBeNull();
    expect(withJd.triageAttemptKey).toBeNull();
    expect(withAssertions.triageAttemptKey).toBeNull();
  });

  it('stores a review as an available triage result and clears its successful attempt key', () => {
    const withKey = opportunityFitDraftReducer(
      createInitialOpportunityFitDraft(7, 'draft-1'),
      { type: 'set_attempt_key', key: 'attempt-1' },
    );

    const next = opportunityFitDraftReducer(withKey, { type: 'set_review', review });

    expect(next.review).toBe(review);
    expect(next.phase).toBe('triage_ready');
    expect(next.triageAttemptKey).toBeNull();
  });

  it('retains the attempt key across an unknown result and a remount-style retry', () => {
    const initial = createInitialOpportunityFitDraft(7, 'draft-1');
    const loading = opportunityFitDraftReducer(initial, {
      type: 'set_attempt_key',
      key: 'attempt-1',
    });
    const timedOut = opportunityFitDraftReducer(loading, {
      type: 'set_error',
      error: '结果未知',
      disposition: 'unknown',
    });

    expect(timedOut.triageAttemptKey).toBe('attempt-1');
    expect(timedOut.actionError).toBe('结果未知');
  });

  it.each([
    ['triage loading', { phase: 'triage_loading' as const, triageFailureDisposition: null }],
    ['unknown failure', { phase: 'confirm_triage' as const, triageFailureDisposition: 'unknown' as const }],
  ])('retains the draft for %s while its attempt key is unresolved', (_label, patch) => {
    const state = {
      ...createInitialOpportunityFitDraft(7, 'draft-1'),
      triageAttemptKey: 'attempt-1',
      ...patch,
    };
    expect(shouldRetainOpportunityFitDraft(state)).toBe(true);
  });

  it('allows cleanup after success or a definite no-write failure', () => {
    const base = createInitialOpportunityFitDraft(7, 'draft-1');
    expect(shouldRetainOpportunityFitDraft({ ...base, triageAttemptKey: null })).toBe(false);
    expect(shouldRetainOpportunityFitDraft({
      ...base,
      triageAttemptKey: 'attempt-1',
      triageFailureDisposition: 'definite_no_write',
    })).toBe(false);
  });

  it('clears the attempt key only when explicitly requested', () => {
    const withKey = opportunityFitDraftReducer(
      createInitialOpportunityFitDraft(7, 'draft-1'),
      { type: 'set_attempt_key', key: 'attempt-1' },
    );

    const next = opportunityFitDraftReducer(withKey, { type: 'set_attempt_key', key: null });

    expect(next.triageAttemptKey).toBeNull();
  });

  it('does not create a partial state from an illegal current state', () => {
    const illegalState = { phase: 'collect_input' } as never;

    const next = opportunityFitDraftReducer(illegalState, { type: 'set_jd', jdText: 'JD' });

    expect(next).toBe(illegalState);
  });

  it('clears the attempt key for a definite no-write failure', () => {
    const withKey = opportunityFitDraftReducer(
      createInitialOpportunityFitDraft(7, 'draft-1'),
      { type: 'set_attempt_key', key: 'attempt-1' },
    );

    const next = opportunityFitDraftReducer(withKey, {
      type: 'set_error',
      error: '输入无效',
      disposition: 'definite_no_write',
    });

    expect(next.triageAttemptKey).toBeNull();
  });

  it.each([
    [{ response: { status: 422 } }, '422'],
    [{ response: { status: 404 } }, '404'],
    [{ response: { status: 502, data: { error_code: 'opportunity_fit_provider_error' } } }, 'provider 502'],
    [{ response: { status: 502, data: { error_code: 'opportunity_fit_unverifiable' } } }, 'unverifiable 502'],
  ] as const)('classifies %s as definite_no_write', (failure, _label) => {
    expect(classifyOpportunityFitFailure(failure)).toBe('definite_no_write');
  });

  it.each([
    [{ response: { status: 500 } }, '500'],
    [{ response: { status: 502, data: {} } }, 'unclassified 502'],
    [{ response: { status: 502, data: { error_code: 'other' } } }, 'other 502'],
    [{ response: { status: 504 } }, 'gateway 504'],
    [{ response: { status: 200, data: null } }, 'invalid response body'],
    [new Error('timeout'), 'timeout'],
    [new TypeError('offline'), 'offline'],
    [undefined, 'lost response'],
  ] as const)('classifies %s as unknown', (failure, _label) => {
    expect(classifyOpportunityFitFailure(failure)).toBe('unknown');
  });

  it('rejects malformed reviews without changing the draft phase', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');

    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: {},
    } as never);

    expect(next).toBe(state);
    expect(next.review).toBeNull();
    expect(next.phase).toBe('collect_input');
  });

  it('rejects a malformed nested deep review without advancing state', () => {
    const loading = opportunityFitDraftReducer(
      opportunityFitDraftReducer(
        createInitialOpportunityFitDraft(7, 'draft-1'),
        { type: 'set_phase', phase: 'deep_review_loading' },
      ),
      { type: 'set_attempt_key', key: 'attempt-1' },
    );

    const next = opportunityFitDraftReducer(loading, {
      type: 'set_review',
      review: { ...review, deep_review: {} },
    } as never);

    expect(next).toBe(loading);
    expect(next.review).toBeNull();
    expect(next.phase).toBe('deep_review_loading');
    expect(next.triageAttemptKey).toBe('attempt-1');
  });

  it.each([
    ['JD path', { source: 'jd', path: '/requirements', excerpt: 'Build things.' }],
    ['JD excerpt', { source: 'jd', path: '/text', excerpt: 'Invented JD' }],
    ['user assertion path', { source: 'user_assertion', path: '/user_assertions/1/text', excerpt: 'I shipped production systems.' }],
    ['user assertion excerpt', { source: 'user_assertion', path: '/user_assertions/0/text', excerpt: 'Invented fact' }],
    ['resume path', { source: 'resume', path: '/experience/01/highlights/0', excerpt: 'Strong delivery record.' }],
    ['resume content_json path', { source: 'resume', path: '/content_json/experience/0/highlights/0', excerpt: 'Strong delivery record.' }],
  ] as const)('rejects a semantically invalid %s evidence reference', (_label, ref) => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: {
        ...review,
        triage: {
          ...review.triage,
          fit_signals: [{ id: 'fit-1', statement: 'Good fit', evidence_refs: [ref] }],
        },
      },
    } as never);

    expect(next).toBe(state);
  });

  it('rejects candidate assertions whose declared index differs from array position', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: {
        ...review,
        source: {
          ...review.source,
          candidate_assertions: [{ index: 1, text: 'I shipped production systems.' }],
        },
      },
    } as never);

    expect(next).toBe(state);
  });

  it('accepts a valid JD evidence reference in a role gap', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: {
        ...review,
        triage: {
          ...review.triage,
          gaps: [{
            id: 'gap-1',
            requirement: 'Build things.',
            kind: 'required',
            candidate_status: 'unknown',
            evidence_refs: [{ source: 'jd', path: '/text', excerpt: 'Build things.' }],
          }],
        },
      },
    });

    expect(next.review).not.toBeNull();
  });

  it.each([
    ['unknown hard constraint', { hard_constraints: [{
      id: 'constraint-1',
      requirement: 'Build things.',
      status: 'unknown',
      explanation: 'The candidate evidence is unresolved.',
      evidence_refs: [],
    }] }],
    ['unknown gap', { gaps: [{
      id: 'gap-1',
      requirement: 'Build things.',
      kind: 'required',
      candidate_status: 'unknown',
      evidence_refs: [],
    }] }],
  ] as const)('rejects an unresolved %s without JD evidence', (_label, triagePatch) => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: { ...review, triage: { ...review.triage, ...triagePatch } },
    } as never);

    expect(next).toBe(state);
  });

  it('accepts an unknown hard constraint when it has JD evidence', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: {
        ...review,
        triage: {
          ...review.triage,
          hard_constraints: [{
            id: 'constraint-1',
            requirement: 'Build things.',
            status: 'unknown',
            explanation: 'The candidate evidence is unresolved.',
            evidence_refs: [{ source: 'jd', path: '/text', excerpt: 'Build things.' }],
          }],
        },
      },
    });

    expect(next.review).not.toBeNull();
  });

  it('accepts an empty derived summary for a safe no-source result', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const emptyResult = {
      ...review,
      summary: { text: 'No source-backed candidate conclusion is available; clarify before proceeding.', evidence_refs: [] },
      triage: {
        ...review.triage,
        summary: { text: 'No source-backed candidate conclusion is available; clarify before proceeding.', evidence_refs: [] },
        fit_signals: [],
      },
    };

    const next = opportunityFitDraftReducer(state, { type: 'set_review', review: emptyResult });

    expect(next.review).not.toBeNull();
  });

  it('rejects an uncited non-derived summary', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: {
        ...review,
        summary: { text: 'Candidate guarantees every requirement.', evidence_refs: [] },
        triage: {
          ...review.triage,
          summary: { text: 'Candidate guarantees every requirement.', evidence_refs: [] },
        },
      },
    });

    expect(next).toBe(state);
  });

  it('checks resume excerpts against an identity-bound evidence proof', () => {
    const proof = {
      resumeId: 3,
      sha256: 'resume-hash',
      contentJson: { experience: [{ highlights: ['Strong delivery record.'] }] },
    };
    const valid = isValidOpportunityFitReview(review, {
      resumeEvidenceProof: proof,
      requireResumeEvidenceProof: true,
    });
    const invalidExcerpt = isValidOpportunityFitReview({
      ...review,
      triage: {
        ...review.triage,
        fit_signals: [{
          ...review.triage.fit_signals[0],
          evidence_refs: [{ source: 'resume', path: '/experience/0/highlights/0', excerpt: 'Invented' }],
        }],
      },
    }, { resumeEvidenceProof: proof, requireResumeEvidenceProof: true });

    expect(valid).toBe(true);
    expect(invalidExcerpt).toBe(false);
  });

  it.each([
    ['wrong resume id', { resumeId: 4, sha256: 'resume-hash' }],
    ['wrong resume sha', { resumeId: 3, sha256: 'other-hash' }],
  ] as const)('rejects resume evidence with %s proof identity', (_label, identity) => {
    expect(isValidOpportunityFitReview(review, {
      resumeEvidenceProof: {
        ...identity,
        contentJson: { experience: [{ highlights: ['Strong delivery record.'] }] },
      },
      requireResumeEvidenceProof: true,
    })).toBe(false);
  });

  it('retains the failure disposition on the draft state', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const failed = opportunityFitDraftReducer(
      opportunityFitDraftReducer(state, { type: 'set_attempt_key', key: 'attempt-1' }),
      { type: 'set_error', error: 'unknown', disposition: 'unknown' },
    );

    expect(failed.triageFailureDisposition).toBe('unknown');
    expect(failed.triageAttemptKey).toBe('attempt-1');
  });

  it('does not mutate the original draft when normalizing callback input', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const normalized = {
      ...state,
      jdText: state.jdText.trim(),
      assertionsText: normalizeOpportunityFitAssertions('  fact  \n').join('\n'),
    };

    expect(state.assertionsText).toBe('');
    expect(normalized.assertionsText).toBe('fact');
  });

  it('accepts a complete deep review result', () => {
    const next = opportunityFitDraftReducer(createInitialOpportunityFitDraft(7, 'draft-1'), {
      type: 'set_review',
      review: { ...review, status: 'deep_reviewed', deep_review: deepReview },
    });

    expect(next.review?.deep_review).toBe(deepReview);
    expect(next.phase).toBe('deep_review_ready');
  });

  it('rejects a deep review gap without evidence refs', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const invalidDeepReview = {
      ...deepReview,
      gaps_to_address: [{ ...deepReview.gaps_to_address[0], evidence_refs: [] }],
    };

    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: { ...review, status: 'deep_reviewed', deep_review: invalidDeepReview },
    });

    expect(next).toBe(state);
    expect(next.review).toBeNull();
  });

  it('rejects a deep review without a recommended path or with malformed candidate assertions', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const missingPath = { ...deepReview, recommended_path: undefined };
    const malformedSource = {
      ...review.source,
      candidate_assertions: [{ index: '0', text: 'fact' }],
    };

    const missingPathResult = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: { ...review, status: 'deep_reviewed', deep_review: missingPath },
    } as never);
    const malformedSourceResult = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: { ...review, source: malformedSource },
    } as never);

    expect(missingPathResult).toBe(state);
    expect(malformedSourceResult).toBe(state);
  });

  it('rejects a review owned by another application without changing draft state', () => {
    const loading = opportunityFitDraftReducer(
      opportunityFitDraftReducer(
        createInitialOpportunityFitDraft(7, 'draft-1'),
        { type: 'set_phase', phase: 'triage_loading' },
      ),
      { type: 'set_attempt_key', key: 'attempt-1' },
    );

    const next = opportunityFitDraftReducer(loading, {
      type: 'set_review',
      review: { ...review, application_id: 8 },
    });

    expect(next).toBe(loading);
    expect(next.phase).toBe('triage_loading');
    expect(next.review).toBeNull();
    expect(next.triageAttemptKey).toBe('attempt-1');
  });

  it('rejects a review whose frozen application source belongs to another application', () => {
    const loading = opportunityFitDraftReducer(
      opportunityFitDraftReducer(
        createInitialOpportunityFitDraft(7, 'draft-1'),
        { type: 'set_phase', phase: 'triage_loading' },
      ),
      { type: 'set_attempt_key', key: 'attempt-1' },
    );

    const next = opportunityFitDraftReducer(loading, {
      type: 'set_review',
      review: {
        ...review,
        source: { ...review.source, application: { ...review.source.application, id: 8 } },
      },
    });

    expect(next).toBe(loading);
    expect(next.review).toBeNull();
    expect(next.triageAttemptKey).toBe('attempt-1');
  });

  it('rejects a review whose frozen resume source does not match resume_id', () => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');

    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: {
        ...review,
        source: { ...review.source, resume: { ...review.source.resume, id: 4 } },
      },
    });

    expect(next).toBe(state);
    expect(next.review).toBeNull();
  });

  it.each([
    ['hard constraint', { hard_constraints: [{}] }],
    ['fit signal', { fit_signals: [{}] }],
    ['gap', { gaps: [{}] }],
    ['deadline', { deadline: {} }],
    ['question', { next_questions: [{}] }],
    ['invalid evidence ref', { fit_signals: [{ id: 'fit-1', statement: 'Good fit', evidence_refs: [{}] }] }],
  ] as const)('rejects a malformed triage %s payload', (_label, triagePatch) => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: { ...review, triage: { ...review.triage, ...triagePatch } },
    } as never);

    expect(next).toBe(state);
    expect(next.phase).toBe('collect_input');
    expect(next.review).toBeNull();
  });

  it.each([
    [{ ...review, status: 'triage_complete', deep_review: deepReview }, 'triage_complete with deep review'],
    [{ ...review, status: 'deep_reviewed', deep_review: null }, 'deep_reviewed without deep review'],
  ] as const)('rejects inconsistent review status and deep review: %s', (invalidReview, _label) => {
    const state = createInitialOpportunityFitDraft(7, 'draft-1');
    const next = opportunityFitDraftReducer(state, {
      type: 'set_review',
      review: invalidReview,
    });

    expect(next).toBe(state);
    expect(next.phase).toBe('collect_input');
    expect(next.review).toBeNull();
  });
});

describe('normalizeOpportunityFitAssertions', () => {
  it('trims assertions, removes empty lines, and preserves valid input', () => {
    expect(normalizeOpportunityFitAssertions('  one  \r\n\n two ')).toEqual(['one', 'two']);
  });

  it('reports more than ten assertions instead of silently truncating', () => {
    expect(() => normalizeOpportunityFitAssertions(
      Array.from({ length: 11 }, (_, index) => `assertion ${index}`).join('\n'),
    )).toThrowError(expect.objectContaining({ code: 'too_many_assertions' }));
  });

  it('reports an assertion longer than 500 characters instead of silently truncating', () => {
    expect(() => normalizeOpportunityFitAssertions('x'.repeat(501)))
      .toThrowError(expect.objectContaining({ code: 'assertion_too_long', index: 0 }));
  });
});

function DraftStoreView({ store }: { store: OpportunityFitDraftStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);

  return createElement(
    'output',
    { 'data-testid': 'draft-state' },
    state.triageAttemptKey ?? 'no-key',
  );
}

describe('opportunity fit draft external store', () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;

  afterEach(() => {
    act(() => root?.unmount());
    container?.remove();
    root = undefined;
    container = undefined;
  });

  function mount(store: OpportunityFitDraftStore) {
    const mountedContainer = document.createElement('div');
    container = mountedContainer;
    document.body.appendChild(mountedContainer);
    root = createRoot(mountedContainer);
    act(() => root?.render(createElement(DraftStoreView, { store })));
    return mountedContainer;
  }

  it('retains the same unknown-result key across a real unmount and remount', () => {
    const store = createOpportunityFitDraftStore(7, 'draft-1');
    const firstMount = mount(store);

    act(() => {
      store.dispatch({ type: 'set_attempt_key', key: 'attempt-1' });
      store.dispatch({
        type: 'set_error',
        error: '结果未知',
        disposition: 'unknown',
      });
    });
    expect(firstMount.querySelector('[data-testid="draft-state"]')?.textContent).toBe('attempt-1');

    act(() => root?.unmount());
    container?.remove();
    root = undefined;
    container = undefined;

    const secondMount = mount(store);
    expect(secondMount.querySelector('[data-testid="draft-state"]')?.textContent).toBe('attempt-1');

    act(() => store.dispatch({ type: 'set_phase', phase: 'triage_loading' }));
    expect(store.getState().triageAttemptKey).toBe('attempt-1');
    expect(secondMount.querySelector('[data-testid="draft-state"]')?.textContent).toBe('attempt-1');
  });

  it('removes only the expected store during explicit lifecycle cleanup', () => {
    const stores = new Map<string, OpportunityFitDraftStore>();
    const active = createOpportunityFitDraftStore(7, 'active');
    const replacement = createOpportunityFitDraftStore(7, 'replacement');
    stores.set('7:active', active);
    stores.set('7:replacement', replacement);

    expect(removeOpportunityFitDraftStore(stores, '7:active', replacement)).toBe(false);
    expect(stores.has('7:active')).toBe(true);
    expect(removeOpportunityFitDraftStore(stores, '7:active', active)).toBe(true);
    expect(stores.has('7:active')).toBe(false);
    expect(stores.has('7:replacement')).toBe(true);
  });
});
