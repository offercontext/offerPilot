import type {
  InterviewAttemptContext,
  InterviewPracticeCase,
  InterviewPracticeCaseListResponse,
} from '@/types/interviewPracticeCase';
import type { MockInterviewAttemptResponse, MockInterviewPendingResponse } from '@/types/mockInterview';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api' });

export async function createInterviewPracticeCase(input: {
  idempotencyKey: string;
  positionName: string;
  jdText: string;
  resumeId: number;
}): Promise<InterviewPracticeCase> {
  const { data } = await http.post<InterviewPracticeCase>('/interview-practice-cases', {
    idempotency_key: input.idempotencyKey,
    position_name: input.positionName,
    jd_text: input.jdText,
    resume_id: input.resumeId,
  });
  return data;
}

export async function listInterviewPracticeCases(): Promise<InterviewPracticeCaseListResponse> {
  const { data } = await http.get<InterviewPracticeCaseListResponse>('/interview-practice-cases');
  return data;
}

export async function getInterviewPracticeCase(caseId: number): Promise<InterviewPracticeCase> {
  const { data } = await http.get<InterviewPracticeCase>(`/interview-practice-cases/${caseId}`);
  return data;
}

export async function archiveInterviewPracticeCase(caseId: number): Promise<InterviewPracticeCase> {
  const { data } = await http.post<InterviewPracticeCase>(`/interview-practice-cases/${caseId}/archive`);
  return data;
}

export async function startQuickPracticeAttempt(input: {
  caseId: number;
  attemptKey: string;
  questionKey: string;
}): Promise<(MockInterviewAttemptResponse & InterviewAttemptContext) | (MockInterviewPendingResponse & InterviewAttemptContext)> {
  const { data } = await http.post(`${`/interview-practice-cases/${input.caseId}/mock-interview/attempts`}`, {
    attempt_idempotency_key: input.attemptKey,
    initial_question_idempotency_key: input.questionKey,
  });
  return data;
}
