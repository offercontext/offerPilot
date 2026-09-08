import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiDelete, apiGet, apiPost, createApiClient } = vi.hoisted(() => ({
  apiDelete: vi.fn(),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  createApiClient: vi.fn(),
}));

vi.mock('./http', () => ({ createApiClient }));
createApiClient.mockReturnValue({ delete: apiDelete, get: apiGet, post: apiPost });

const {
  createInterviewKnowledgePreview,
  deleteUnconfirmedInterviewKnowledgeAttempt,
} = await import('./interviewKnowledgeCapture');

beforeEach(() => {
  apiDelete.mockReset();
  apiGet.mockReset();
  apiPost.mockReset();
});

describe('interview knowledge capture service', () => {
  it('uses canonical preview and delete API contracts', async () => {
    apiPost.mockResolvedValue({ data: { attempt_key: 'a' } });
    apiDelete.mockResolvedValue({ status: 204 });
    const fragments = [{ fragment_id: 'f-1', path: '/questions', start: 0, end: 1, text: '问' }] as const;

    await createInterviewKnowledgePreview(7, 'attempt-1', 'direct', [...fragments]);
    await deleteUnconfirmedInterviewKnowledgeAttempt(7, 'attempt-1');

    expect(apiPost).toHaveBeenCalledWith('/notes/7/knowledge-capture/preview', {
      attempt_key: 'attempt-1', mode: 'direct', selected_fragments: [...fragments],
    });
    expect(apiDelete).toHaveBeenCalledWith('/notes/7/knowledge-capture/attempts/attempt-1');
  });

  it('maps delete network uncertainty separately from provider failure', async () => {
    apiDelete.mockRejectedValue({ response: { status: 504, data: { error: 'secret' } }, message: 'Axios secret' });
    await expect(deleteUnconfirmedInterviewKnowledgeAttempt(7, 'attempt-1')).rejects.toMatchObject({
      code: 'capture_delete_unknown', resultUnknown: true, message: '操作结果未知，请重新打开复盘确认状态。',
    });
  });

  it('maps confirmed-attempt race to saved-and-viewable state', async () => {
    apiDelete.mockRejectedValue({ response: { status: 409, data: { error_code: 'capture_attempt_confirmed', error: 'secret' } } });
    await expect(deleteUnconfirmedInterviewKnowledgeAttempt(7, 'attempt-1')).rejects.toMatchObject({
      code: 'capture_attempt_confirmed', message: '该沉淀已保存，可在知识库查看。', resultUnknown: false,
    });
  });

  it('keeps the attempt after a provider 502 with a stable error code', async () => {
    apiPost.mockRejectedValue({
      response: { status: 502, data: { error_code: 'interview_knowledge_preview_provider_error' } },
    });
    await expect(createInterviewKnowledgePreview(7, 'attempt-1', 'ai', [])).rejects.toMatchObject({
      code: 'interview_knowledge_preview_provider_error',
      resultUnknown: true,
    });
  });
});
