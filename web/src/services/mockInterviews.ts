import type {
  MockInterviewAttemptResponse,
  MockInterviewHistoryItem,
  MockInterviewProposalResponse,
  MockInterviewPendingResponse,
} from '@/types/mockInterview';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api' });

export type InterviewStudioContext =
  | { kind: 'application_event'; applicationId: number; eventId: number }
  | { kind: 'quick_practice'; caseId: number };

function attemptBase(context: InterviewStudioContext): string {
  return context.kind === 'quick_practice'
    ? `/interview-practice-cases/${context.caseId}/mock-interview/attempts`
    : base(context.applicationId, context.eventId);
}

function attemptPath(context: InterviewStudioContext, attemptId: number): string {
  return `${attemptBase(context)}/${attemptId}`;
}

function base(applicationId: number, eventId: number): string {
  return `/applications/${applicationId}/events/${eventId}/mock-interview/attempts`;
}

export async function discardMockInterviewAttempt(input: {
  applicationId: number;
  eventId: number;
  attemptId: number;
}): Promise<void> {
  await http.delete(`${base(input.applicationId, input.eventId)}/${input.attemptId}`);
}

export async function startMockInterview(input: {
  applicationId: number;
  eventId: number;
  resumeId: number;
  jdVersionId: number;
  attemptKey: string;
  questionKey: string;
  preparationProposalId?: number;
  preparationItemIds?: string[];
}): Promise<MockInterviewAttemptResponse | MockInterviewPendingResponse> {
  const { data } = await http.post<MockInterviewAttemptResponse | MockInterviewPendingResponse>(base(input.applicationId, input.eventId), {
    resume_id: input.resumeId,
    jd_version_id: input.jdVersionId,
    attempt_idempotency_key: input.attemptKey,
    initial_question_idempotency_key: input.questionKey,
    preparation_proposal_id: input.preparationProposalId,
    preparation_selection: input.preparationProposalId && input.preparationItemIds?.length
      ? { proposal_id: input.preparationProposalId, item_ids: input.preparationItemIds }
      : undefined,
  });
  return data;
}

export async function generateMockInterviewQuestion(input: {
  applicationId: number;
  eventId: number;
  attemptId: number;
  turnNo: number;
  questionKey: string;
}): Promise<MockInterviewAttemptResponse | MockInterviewPendingResponse> {
  const { data } = await http.post<MockInterviewAttemptResponse | MockInterviewPendingResponse>(
    `${base(input.applicationId, input.eventId)}/${input.attemptId}/turns/${input.turnNo}/question`,
    { question_idempotency_key: input.questionKey },
  );
  return data;
}

export async function submitMockInterviewAnswer(input: {
  applicationId: number;
  eventId: number;
  attemptId: number;
  turnNo: number;
  answerText: string;
  turnKey: string;
}): Promise<{ attempt_id: number; attempt_status: string; transcript_fingerprint: string }> {
  const { data } = await http.post(`${base(input.applicationId, input.eventId)}/${input.attemptId}/turns`, {
    turn_no: input.turnNo,
    answer_text: input.answerText,
    turn_idempotency_key: input.turnKey,
  });
  return data;
}

export async function finishMockInterview(input: {
  applicationId: number;
  eventId: number;
  attemptId: number;
  feedbackKey: string;
}): Promise<MockInterviewProposalResponse | MockInterviewPendingResponse> {
  const { data } = await http.post<MockInterviewProposalResponse | MockInterviewPendingResponse>(
    `${base(input.applicationId, input.eventId)}/${input.attemptId}/finish`,
    { feedback_idempotency_key: input.feedbackKey },
  );
  return data;
}

export async function startInterviewStudioAttempt(input: {
  context: InterviewStudioContext;
  resumeId?: number;
  jdVersionId?: number;
  attemptKey: string;
  questionKey: string;
}): Promise<MockInterviewAttemptResponse | MockInterviewPendingResponse> {
  const payload = input.context.kind === 'quick_practice'
    ? {
        attempt_idempotency_key: input.attemptKey,
        initial_question_idempotency_key: input.questionKey,
      }
    : {
        resume_id: input.resumeId,
        jd_version_id: input.jdVersionId,
        attempt_idempotency_key: input.attemptKey,
        initial_question_idempotency_key: input.questionKey,
      };
  const { data } = await http.post<MockInterviewAttemptResponse | MockInterviewPendingResponse>(
    attemptBase(input.context),
    payload,
  );
  return data;
}

export async function submitInterviewStudioAnswer(input: {
  context: InterviewStudioContext;
  attemptId: number;
  turnNo: number;
  answerText: string;
  turnKey: string;
}): Promise<{ attempt_id: number; attempt_status: string; transcript_fingerprint: string }> {
  const { data } = await http.post(`${attemptPath(input.context, input.attemptId)}/turns`, {
    turn_no: input.turnNo,
    answer_text: input.answerText,
    turn_idempotency_key: input.turnKey,
  });
  return data;
}

export async function generateInterviewStudioQuestion(input: {
  context: InterviewStudioContext;
  attemptId: number;
  turnNo: number;
  questionKey: string;
}): Promise<MockInterviewAttemptResponse | MockInterviewPendingResponse> {
  const { data } = await http.post<MockInterviewAttemptResponse | MockInterviewPendingResponse>(
    `${attemptPath(input.context, input.attemptId)}/turns/${input.turnNo}/question`,
    { question_idempotency_key: input.questionKey },
  );
  return data;
}

export async function finishInterviewStudio(input: {
  context: InterviewStudioContext;
  attemptId: number;
  feedbackKey: string;
}): Promise<MockInterviewProposalResponse | MockInterviewPendingResponse> {
  const { data } = await http.post<MockInterviewProposalResponse | MockInterviewPendingResponse>(
    `${attemptPath(input.context, input.attemptId)}/finish`,
    { feedback_idempotency_key: input.feedbackKey },
  );
  return data;
}

export async function discardInterviewStudioAttempt(input: {
  context: InterviewStudioContext;
  attemptId: number;
}): Promise<void> {
  await http.delete(attemptPath(input.context, input.attemptId));
}

export async function listInterviewStudioHistory(context: InterviewStudioContext): Promise<MockInterviewHistoryItem[]> {
  const { data } = await http.get<{ items: MockInterviewHistoryItem[] }>(attemptBase(context));
  return data.items;
}

export async function listMockInterviewHistory(applicationId: number, eventId: number): Promise<MockInterviewHistoryItem[]> {
  const { data } = await http.get<{ items: MockInterviewHistoryItem[] }>(base(applicationId, eventId));
  return data.items;
}

export async function confirmMockInterviewReviewDraft(input: {
  applicationId: number;
  eventId: number;
  attemptId: number;
  proposalId: number;
  confirmationKey: string;
  selectedBlocks: unknown[];
}) {
  const { data } = await http.post(
    `${base(input.applicationId, input.eventId)}/${input.attemptId}/review-drafts`,
    {
      proposal_id: input.proposalId,
      confirmation_idempotency_key: input.confirmationKey,
      selected_blocks: input.selectedBlocks,
    },
  );
  return data as { draft_id: number; status: string };
}
