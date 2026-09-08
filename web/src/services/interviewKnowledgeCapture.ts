import axios from 'axios';
import type {
  ConfirmedInterviewKnowledge,
  InterviewKnowledgeCaptureAttempt,
  InterviewKnowledgeNote,
  SelectedFragment,
  CapturePreview,
} from '@/types/interviewKnowledgeCapture';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 130000 });

const SAFE_ERRORS: Record<string, string> = {
  interview_note_not_found: '该复盘已不可用。',
  interview_knowledge_source_changed: '复盘内容已变化，请重新选择原始片段。',
  interview_knowledge_attempt_conflict: '当前沉淀草稿已变化，请重新开始。',
  interview_knowledge_attempt_expired: '沉淀草稿已过期，请重新选择片段。',
  interview_knowledge_selection_invalid: '所选片段无法验证，请重新选择。',
  interview_knowledge_preview_provider_error: 'AI 预览暂不可用，可直接保存选中原文。',
  capture_attempt_confirmed: '该沉淀已保存，可在知识库查看。',
};

export class InterviewKnowledgeCaptureError extends Error {
  readonly code?: string;
  readonly status?: number;
  readonly resultUnknown: boolean;

  constructor(message: string, code?: string, status?: number, resultUnknown = false) {
    super(message);
    this.name = 'InterviewKnowledgeCaptureError';
    this.code = code;
    this.status = status;
    this.resultUnknown = resultUnknown;
  }
}

function toSafeError(error: unknown, operation: 'preview' | 'confirm' | 'delete'): InterviewKnowledgeCaptureError {
  const response = axios.isAxiosError(error)
    ? error.response
    : (error as { response?: { status?: number; data?: unknown } } | null)?.response;
  const data = response?.data as { error_code?: unknown } | undefined;
  const code = typeof data?.error_code === 'string' ? data.error_code : undefined;
  const status = response?.status;
  if (code && SAFE_ERRORS[code]) {
    return new InterviewKnowledgeCaptureError(
      SAFE_ERRORS[code],
      code,
      status,
      code === 'interview_knowledge_preview_provider_error',
    );
  }
  if (operation === 'delete' && status === 409) {
    return new InterviewKnowledgeCaptureError(SAFE_ERRORS.capture_attempt_confirmed, 'capture_attempt_confirmed', status, false);
  }
  if (operation === 'delete') {
    return new InterviewKnowledgeCaptureError('操作结果未知，请重新打开复盘确认状态。', 'capture_delete_unknown', status, true);
  }
  if (status === 502) {
    return new InterviewKnowledgeCaptureError('AI 预览暂不可用，可直接保存选中原文。', 'interview_knowledge_preview_provider_error', status, true);
  }
  if (status === 404) {
    return new InterviewKnowledgeCaptureError(SAFE_ERRORS.interview_note_not_found, 'interview_note_not_found', status, false);
  }
  return new InterviewKnowledgeCaptureError('复盘知识沉淀暂时不可用，请稍后重试。', undefined, status, status === undefined || (status >= 500));
}

export async function createInterviewKnowledgePreview(
  noteID: number,
  attemptKey: string,
  mode: 'direct' | 'ai',
  selectedFragments: SelectedFragment[],
): Promise<InterviewKnowledgeCaptureAttempt> {
  try {
    const { data } = await http.post<InterviewKnowledgeCaptureAttempt>(
      `/notes/${noteID}/knowledge-capture/preview`,
      { attempt_key: attemptKey, mode, selected_fragments: selectedFragments },
    );
    return data;
  } catch (error) {
    throw toSafeError(error, 'preview');
  }
}

export async function confirmInterviewKnowledgeCapture(
  noteID: number,
  input: { attempt_key: string; note_fingerprint: string; title: string; blocks: CapturePreview['blocks'] },
): Promise<ConfirmedInterviewKnowledge> {
  try {
    const { data } = await http.post<ConfirmedInterviewKnowledge>(
      `/notes/${noteID}/knowledge-capture/confirm`,
      input,
    );
    return data;
  } catch (error) {
    throw toSafeError(error, 'confirm');
  }
}

export async function deleteUnconfirmedInterviewKnowledgeAttempt(noteID: number, attemptKey: string): Promise<void> {
  try {
    await http.delete(`/notes/${noteID}/knowledge-capture/attempts/${attemptKey}`);
  } catch (error) {
    throw toSafeError(error, 'delete');
  }
}

export async function listConfirmedInterviewKnowledge(): Promise<InterviewKnowledgeNote[]> {
  try {
    const { data } = await http.get<{ items: InterviewKnowledgeNote[] }>('/knowledge/notes');
    return data.items;
  } catch (error) {
    throw toSafeError(error, 'confirm');
  }
}

export async function getConfirmedInterviewKnowledge(noteID: number): Promise<InterviewKnowledgeNote> {
  try {
    const { data } = await http.get<InterviewKnowledgeNote>(`/knowledge/notes/${noteID}`);
    return data;
  } catch (error) {
    throw toSafeError(error, 'confirm');
  }
}
