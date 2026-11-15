// @vitest-environment jsdom
import { StrictMode, act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
import { useApplicationJdOwnerDrafts } from '@/features/applicationTasks/useApplicationJdOwnerDrafts';
import { useOfferNegotiationOwnerDrafts } from '@/features/offerNegotiation/useOfferNegotiationOwnerDrafts';
import { useInterviewOwnerDrafts } from '@/features/interviewEvents/useInterviewOwnerDrafts';
import type { OfferNegotiationDraft } from '@/components/OfferNegotiationDrawer';
import { readRetainedOwnerSafety } from './registry';

const clean = { hasDraft: false, activeRun: false, pendingApproval: false };
const offer = (): OfferNegotiationDraft => ({ attemptKey: 'key', confirmationKey: 'confirm', goal: '', concerns: '', scenario: '', resultUnknown: false, pendingOperation: null, proposalId: null, selectedBlocks: [], edits: {}, dimensionIds: [], sourceFingerprint: null, previewSnapshot: null, previewInputKey: null });

describe('retained domain ownership', () => {
  it('reads each registered owner at decision time and fails closed on an unavailable projection', () => {
    let projection = clean;
    const readouts = { 'application.jd': () => projection, 'application.offer_review': () => clean, 'interview.retained': () => clean };
    expect(readRetainedOwnerSafety(readouts)).toEqual(clean);
    projection = { ...clean, activeRun: true };
    expect(readRetainedOwnerSafety(readouts).activeRun).toBe(true);
    expect(readRetainedOwnerSafety({ ...readouts, 'interview.retained': () => { throw new Error('unavailable'); } })).toEqual({ hasDraft: true, activeRun: true, pendingApproval: true });
  });

  it('retains draft isolation, baseline and unknown guards across surface close/reopen in strict mode', () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    const root = createRoot(container);
    let jd!: ReturnType<typeof useApplicationJdOwnerDrafts>;
    let negotiation!: ReturnType<typeof useOfferNegotiationOwnerDrafts>;
    let interview!: ReturnType<typeof useInterviewOwnerDrafts>;
    function Owner({ open }: { open: boolean }) {
      jd = useApplicationJdOwnerDrafts();
      negotiation = useOfferNegotiationOwnerDrafts();
      interview = useInterviewOwnerDrafts();
      return open ? <div>Surface</div> : null;
    }
    const render = (open: boolean) => act(() => root.render(<StrictMode><Owner open={open} /></StrictMode>));
    const read = () => readRetainedOwnerSafety({ 'application.jd': jd.readSafety, 'application.offer_review': negotiation.readSafety, 'interview.retained': interview.readSafety });
    try {
      render(true);
      act(() => {
        jd.updateDraft(1, { jdText: 'saved JD', sourceUrl: '', expectedCurrentVersionId: 3, idempotencyKey: null, resultUnknown: false, pendingOperation: null });
        expect(read()).toEqual(clean);
        jd.updateDraft(1, { jdText: '' });
        expect(read().hasDraft).toBe(true);
        jd.updateDraft(1, { jdText: 'saved JD', resultUnknown: true });
        expect(read().activeRun).toBe(true);
        negotiation.updateDraft(5, { ...offer(), goal: 'UI goal' });
        negotiation.updatePilotDraft(5, { ...offer(), goal: 'Pilot goal' });
        interview.setInterviewPreparationAttempts({ 'event:3': { key: 'pending', result_unknown: false } });
        expect(interview.readSafety().activeRun).toBe(true);
      });
      render(false);
      render(true);
      expect(jd.drafts[1].resultUnknown).toBe(true);
      expect(negotiation.drafts[5].goal).toBe('UI goal');
      expect(negotiation.pilotDrafts[5].goal).toBe('Pilot goal');
      expect(interview.interviewPreparationAttempts['event:3'].key).toBe('pending');
      act(() => {
        jd.updateDraft(1, null);
        negotiation.updateDraft(5, null);
        negotiation.updatePilotDraft(5, null);
        interview.setInterviewPreparationAttempts({});
        expect(read()).toEqual(clean);
        interview.setInterviewReviewProposalAttempts((previous) => ({ ...previous, 8: { key: 'unknown', result_unknown: true, event_id: null } }));
        interview.setInterviewReviewProposalAttempts((previous) => {
          expect(previous[8].key).toBe('unknown');
          expect(read().activeRun).toBe(true);
          return { ...previous, 9: { key: 'second', result_unknown: false, event_id: null } };
        });
        expect(read().activeRun).toBe(true);
      });
      expect(Object.keys(interview.interviewReviewProposalAttempts)).toEqual(['8', '9']);
    } finally { act(() => root.unmount()); }
  });
});
