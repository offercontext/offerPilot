// @vitest-environment jsdom
import { act, type ReactNode, useRef, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInterviewDrawerDraft } from './MockInterviewDrawer';
import type { VoiceAnswerBrowser } from '@/features/mockInterviewVoice/VoiceAnswerComposer';

const services = vi.hoisted(() => ({
  startMockInterview: vi.fn(),
  generateMockInterviewQuestion: vi.fn(),
  submitMockInterviewAnswer: vi.fn(),
  finishMockInterview: vi.fn(),
  discardMockInterviewAttempt: vi.fn(),
  listMockInterviewHistory: vi.fn(),
  confirmMockInterviewReviewDraft: vi.fn(),
  listInterviewPreparationProposals: vi.fn(),
  saveVoiceCoachingSnapshot: vi.fn(),
  getVoiceCoachingSnapshot: vi.fn(),
}));

vi.mock('@/services/mockInterviews', () => services);
vi.mock('@/services/interviewPreparationProposals', () => ({
  listInterviewPreparationProposals: services.listInterviewPreparationProposals,
}));
vi.mock('@/services/voiceCoaching', () => ({
  saveVoiceCoachingSnapshot: services.saveVoiceCoachingSnapshot,
  getVoiceCoachingSnapshot: services.getVoiceCoachingSnapshot,
}));
vi.mock('antd', () => ({
  Alert: (props: { children?: ReactNode; action?: ReactNode; message?: ReactNode; description?: ReactNode }) => (
    <div>{props.message}{props.description}{props.children}{props.action}</div>
  ),
  Button: (props: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props} />,
  Drawer: (props: { open?: boolean; children?: ReactNode; onClose?: () => void }) => (
    props.open ? <div role="dialog">{props.children}<button type="button" data-testid="close-drawer" onClick={props.onClose}>close</button></div> : null
  ),
  Empty: (props: { description?: ReactNode }) => <div>{props.description}</div>,
  Input: Object.assign(
    (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
    { TextArea: (props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...props} /> },
  ),
  List: (props: { dataSource?: unknown[]; renderItem: (item: never) => ReactNode }) => (
    <div>{(props.dataSource ?? []).map((item, index) => <div key={index}>{props.renderItem(item as never)}</div>)}</div>
  ),
  Select: (props: React.SelectHTMLAttributes<HTMLSelectElement>) => <select {...props} />,
  Space: (props: { children?: ReactNode }) => <div>{props.children}</div>,
  Spin: () => <span>loading</span>,
  Tag: (props: { children?: ReactNode }) => <span>{props.children}</span>,
}));

const { default: MockInterviewDrawer } = await import('./MockInterviewDrawer');

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseDraft: MockInterviewDrawerDraft = {
  jdText: 'JD',
  jdVersionId: 1,
  attemptKey: null,
  questionKey: null,
  feedbackKey: null,
  turnKey: null,
  nextQuestionKey: null,
  confirmationKey: null,
  answerSubmitted: false,
  editedBlocks: {},
  preparationItemIds: [],
  attemptId: null,
  turnNo: 1,
  question: '',
  answer: '',
  proposalId: null,
  proposal: null,
  selectedIds: [],
  resultUnknown: false,
  error: null,
};

let container: HTMLDivElement | undefined;
let root: Root | undefined;

function drawerButtons(): HTMLButtonElement[] {
  return [...(container?.querySelectorAll('button') ?? [])]
    .filter((button) => !button.dataset.testid && !button.closest('[data-testid="voice-answer-composer"]')) as HTMLButtonElement[];
}

function buttonByText(label: string): HTMLButtonElement {
  const button = [...(container?.querySelectorAll('button') ?? [])]
    .find((item) => item.textContent?.includes(label)) as HTMLButtonElement | undefined;
  if (!button) throw new Error(`missing button: ${label}`);
  return button;
}

function changeTextarea(textarea: HTMLTextAreaElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing textarea setter');
  act(() => {
    setter.call(textarea, value);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function flush() {
  return act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function voiceBrowserFixture() {
  const track = { stop: vi.fn() };
  const recorder = {
    state: 'inactive',
    ondataavailable: null as ((event: { data: Blob }) => void) | null,
    onstop: null as (() => void) | null,
    start: vi.fn(function start(this: { state: string }) { this.state = 'recording'; }),
    pause: vi.fn(),
    resume: vi.fn(),
    stop: vi.fn(function stop(this: typeof recorder) {
      this.state = 'inactive';
      this.ondataavailable?.({ data: new Blob(['voice'], { type: 'audio/webm' }) });
      this.onstop?.();
    }),
  };
  const voiceBrowser: VoiceAnswerBrowser = {
    getUserMedia: vi.fn(async () => ({ getTracks: () => [track] }) as unknown as MediaStream),
    createMediaRecorder: vi.fn(() => recorder),
    createObjectURL: vi.fn(() => 'blob:voice'),
    revokeObjectURL: vi.fn(),
    speechSynthesis: { speak: vi.fn(), cancel: vi.fn(), pause: vi.fn(), resume: vi.fn(), paused: false },
    speechSynthesisSupported: true,
    createUtterance: (text) => ({ text }),
    now: () => 0,
  };
  return { voiceBrowser, recorder, track };
}

function Harness({ initial = baseDraft, voiceBrowser, keepOpenOnClose = false }: {
  initial?: MockInterviewDrawerDraft;
  voiceBrowser?: VoiceAnswerBrowser;
  keepOpenOnClose?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const [draft, setDraft] = useState(initial);
  const draftRef = useRef(initial);
  const patches = useRef<Partial<MockInterviewDrawerDraft>[]>([]);
  return (
    <>
      <button type="button" data-testid="toggle-drawer" onClick={() => setOpen((value) => !value)}>toggle</button>
      {open ? (
        <MockInterviewDrawer
          open
          applicationId={7}
          eventId={11}
          resumes={[{ id: 3, title: 'Resume' }]}
          draft={draft}
          onDraftChange={(patch) => {
            patches.current.push(patch);
            setDraft((current) => {
              const next = { ...current, ...patch };
              draftRef.current = next;
              return next;
            });
          }}
          onClose={() => { if (!keepOpenOnClose) setOpen(false); }}
          voiceBrowser={voiceBrowser}
        />
      ) : null}
      <output data-testid="draft-state">{JSON.stringify(draftRef.current)}</output>
    </>
  );
}

function render(initial?: MockInterviewDrawerDraft, options: { voiceBrowser?: VoiceAnswerBrowser; keepOpenOnClose?: boolean } = {}) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root?.render(<Harness initial={initial} {...options} />));
}

async function retryDiscardAfterRemount() {
  act(() => (container?.querySelector('[data-testid="toggle-drawer"]') as HTMLButtonElement).click());
  act(() => (container?.querySelector('[data-testid="toggle-drawer"]') as HTMLButtonElement).click());
  await flush();
  act(() => drawerButtons()[0]?.click());
  await flush();
}

beforeEach(() => {
  services.startMockInterview.mockReset();
  services.generateMockInterviewQuestion.mockReset();
  services.submitMockInterviewAnswer.mockReset();
  services.finishMockInterview.mockReset();
  services.discardMockInterviewAttempt.mockReset();
  services.listMockInterviewHistory.mockResolvedValue([]);
  services.listInterviewPreparationProposals.mockResolvedValue([]);
  services.saveVoiceCoachingSnapshot.mockReset();
  services.getVoiceCoachingSnapshot.mockReset();
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
});

describe('MockInterviewDrawer failed-attempt cleanup', () => {
  it('shows the confirmed practice focus before a new Attempt starts', () => {
    render({
      ...baseDraft,
      voicePracticeFocus: {
        focus_kind: 'long_pause_control',
        title: '减少长停顿',
        reason: '连续长停顿仍较明显',
        source_snapshot_ids: [17],
        source_snapshot_id: 17,
        application_id: 7,
        event_id: 11,
        question_text: '请介绍一次故障处理经历。',
        source_available: true,
      },
    });

    expect(container?.textContent).toContain('本次刻意练习：减少长停顿');
    expect(container?.textContent).toContain('已确认表达记录 #17');
  });

  it('submits voice text only after the user explicitly confirms it', async () => {
    services.submitMockInterviewAnswer.mockResolvedValue(undefined);
    const { voiceBrowser } = voiceBrowserFixture();
    render({
      ...baseDraft,
      resumeId: 3,
      attemptId: 88,
      turnNo: 1,
      question: '请介绍一次故障处理经历。',
    }, { voiceBrowser });

    act(() => buttonByText('语音回答').click());
    act(() => buttonByText('开始录音').click());
    await flush();
    act(() => buttonByText('完成录音').click());
    await flush();
    const transcript = container?.querySelector('textarea[aria-label="确认后的回答文字"]') as HTMLTextAreaElement;
    changeTextarea(transcript, '我先确认影响范围，再完成回滚。');
    expect(services.submitMockInterviewAnswer).not.toHaveBeenCalled();

    act(() => buttonByText('确认使用这段文字').click());
    expect(services.submitMockInterviewAnswer).not.toHaveBeenCalled();
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}'))
      .not.toMatchObject({ hasSubmittedVoiceAnswer: true });
    act(() => buttonByText('提交回答').click());
    await flush();

    expect(services.submitMockInterviewAnswer).toHaveBeenCalledWith(expect.objectContaining({
      attemptId: 88,
      answerText: '我先确认影响范围，再完成回滚。',
    }));
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}'))
      .toMatchObject({ hasSubmittedVoiceAnswer: true });
  });

  it('requires a separate confirmation before saving local voice measurements', async () => {
    services.submitMockInterviewAnswer.mockResolvedValue(undefined);
    services.saveVoiceCoachingSnapshot.mockResolvedValue({ id: 91 });
    const { voiceBrowser } = voiceBrowserFixture();
    render({
      ...baseDraft,
      resumeId: 3,
      attemptId: 88,
      turnNo: 1,
      question: '请介绍一次故障处理经历。',
      voicePracticeFocus: {
        focus_kind: 'long_pause_control',
        title: '减少长停顿',
        reason: '连续长停顿仍较明显',
        source_snapshot_ids: [17, 16],
        source_snapshot_id: 17,
        application_id: 7,
        event_id: 11,
        question_text: '请介绍一次故障处理经历。',
        source_available: true,
      },
    }, { voiceBrowser });

    act(() => buttonByText('语音回答').click());
    act(() => buttonByText('开始录音').click());
    await flush();
    act(() => buttonByText('完成录音').click());
    await flush();
    const transcript = container?.querySelector('textarea[aria-label="确认后的回答文字"]') as HTMLTextAreaElement;
    changeTextarea(transcript, '我先确认影响范围，再完成回滚。');
    act(() => buttonByText('确认使用这段文字').click());
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}'))
      .toMatchObject({
        voiceCoachingReview: { originSnapshotId: 17, focusKind: 'long_pause_control' },
      });
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}'))
      .not.toMatchObject({ hasSubmittedVoiceAnswer: true });
    act(() => buttonByText('提交回答').click());
    await flush();

    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}'))
      .toMatchObject({ hasSubmittedVoiceAnswer: true });

    expect(container?.textContent).toContain('保存本次表达复盘');
    expect(services.saveVoiceCoachingSnapshot).not.toHaveBeenCalled();
    expect(buttonByText('生成下一题').disabled).toBe(true);
    act(() => buttonByText('确认保存').click());
    await flush();

    expect(services.saveVoiceCoachingSnapshot).toHaveBeenCalledWith(expect.objectContaining({
      applicationId: 7,
      eventId: 11,
      attemptId: 88,
      turnNo: 1,
      payload: expect.objectContaining({
        idempotency_key: expect.any(String),
        reflection_text: '',
      }),
    }));
    expect(container?.textContent).toContain('表达复盘已保存');
    expect(buttonByText('生成下一题').disabled).toBe(false);
  });

  it('freezes an unknown snapshot save and retries with the same key and payload', async () => {
    const review = {
      turnNo: 1,
      summary: {
        totalDurationMs: 42_000,
        voicedDurationMs: 30_000,
        pauseCount: 1,
        longestPauseMs: 2_800,
        speechRateCpm: 120,
        fillerOccurrences: [],
      },
      reflectionText: '先给结论',
      focusKind: 'long_pause_control' as const,
      originSnapshotId: null,
      idempotencyKey: null,
      saveState: 'idle' as const,
      snapshotId: null,
    };
    services.saveVoiceCoachingSnapshot
      .mockRejectedValueOnce(new Error('response lost'))
      .mockResolvedValueOnce({ id: 92 });
    services.getVoiceCoachingSnapshot.mockRejectedValue({ response: { status: 404 } });
    render({
      ...baseDraft,
      resumeId: 3,
      attemptId: 88,
      turnNo: 1,
      question: '请介绍一次故障处理经历。',
      answer: '回答',
      answerSubmitted: true,
      voiceCoachingReview: review,
    });

    act(() => buttonByText('确认保存').click());
    await flush();
    const firstRequest = services.saveVoiceCoachingSnapshot.mock.calls[0][0];
    expect(container?.textContent).toContain('使用原保存请求重试');
    act(() => buttonByText('使用原保存请求重试').click());
    await flush();
    const secondRequest = services.saveVoiceCoachingSnapshot.mock.calls[1][0];
    expect(secondRequest.payload).toEqual(firstRequest.payload);
    expect(container?.textContent).toContain('表达复盘已保存');
  });

  it('only reconciles a lost save response when the persisted snapshot matches the frozen request', async () => {
    const review = {
      turnNo: 1,
      summary: { totalDurationMs: 42_000, voicedDurationMs: 30_000, pauseCount: 1, longestPauseMs: 2_800, speechRateCpm: 120, fillerOccurrences: [] },
      reflectionText: '先给结论',
      focusKind: null,
      originSnapshotId: null,
      idempotencyKey: null,
      saveState: 'idle' as const,
      snapshotId: null,
    };
    services.saveVoiceCoachingSnapshot.mockRejectedValue(new Error('response lost'));
    services.getVoiceCoachingSnapshot.mockResolvedValue({
      id: 99,
      total_duration_ms: 42_000,
      voiced_duration_ms: 30_000,
      pause_count: 1,
      longest_pause_ms: 9_999,
      speech_rate_cpm: 120,
      filler_occurrences: [],
      reflection_text: '另一份内容',
      focus_kind: null,
      origin_snapshot_id: null,
    });
    render({ ...baseDraft, resumeId: 3, attemptId: 88, turnNo: 1, question: '问题', answer: '回答', answerSubmitted: true, voiceCoachingReview: review });

    act(() => buttonByText('确认保存').click());
    await flush();

    expect(container?.textContent).toContain('保存结果待确认');
    expect(container?.textContent).not.toContain('表达复盘已保存');
  });

  it('treats an idempotency conflict as deterministic and never exposes a retry', async () => {
    const review = {
      turnNo: 1,
      summary: { totalDurationMs: 42_000, voicedDurationMs: 30_000, pauseCount: 1, longestPauseMs: 2_800, fillerOccurrences: [] },
      reflectionText: '',
      focusKind: null,
      originSnapshotId: null,
      idempotencyKey: null,
      saveState: 'idle' as const,
      snapshotId: null,
    };
    services.saveVoiceCoachingSnapshot.mockRejectedValue({ response: { status: 409, data: { error_code: 'voice_coaching_idempotency_conflict' } } });
    render({ ...baseDraft, resumeId: 3, attemptId: 88, turnNo: 1, question: '问题', answer: '回答', answerSubmitted: true, voiceCoachingReview: review });

    act(() => buttonByText('确认保存').click());
    await flush();

    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      error: '这道回答已经保存了另一份表达复盘，当前草稿不会覆盖历史。',
      voiceCoachingReview: { saveState: 'conflict' },
    });
    expect(container?.textContent).not.toContain('使用原保存请求重试');
    expect(services.getVoiceCoachingSnapshot).not.toHaveBeenCalled();
  });

  it('asks before closing when a local voice draft has not been submitted', async () => {
    render({
      ...baseDraft,
      resumeId: 3,
      attemptId: 88,
      question: '请介绍一次故障处理经历。',
    });
    act(() => buttonByText('语音回答').click());
    const transcript = container?.querySelector('textarea[aria-label="确认后的回答文字"]') as HTMLTextAreaElement;
    changeTextarea(transcript, '未提交的回答');

    act(() => (container?.querySelector('[data-testid="close-drawer"]') as HTMLButtonElement).click());
    expect(container?.querySelector('[role="dialog"]')).not.toBeNull();
    act(() => buttonByText('放弃并关闭').click());
    expect(container?.querySelector('[role="dialog"]')).toBeNull();
  });

  it('stops local recording before an async parent close finishes', async () => {
    const { voiceBrowser, recorder, track } = voiceBrowserFixture();
    render({ ...baseDraft, resumeId: 3, attemptId: 88, question: '请介绍一次故障处理经历。' }, {
      voiceBrowser,
      keepOpenOnClose: true,
    });
    act(() => buttonByText('语音回答').click());
    act(() => buttonByText('开始录音').click());
    await flush();
    expect(recorder.start).toHaveBeenCalledOnce();

    act(() => (container?.querySelector('[data-testid="close-drawer"]') as HTMLButtonElement).click());
    act(() => buttonByText('放弃并关闭').click());

    expect(recorder.stop).toHaveBeenCalledOnce();
    expect(track.stop).toHaveBeenCalledOnce();
    expect(container?.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it('invalidates a pending microphone permission request before a normal async parent close', async () => {
    const { voiceBrowser, track } = voiceBrowserFixture();
    let resolveStream!: (stream: MediaStream) => void;
    voiceBrowser.getUserMedia = vi.fn(() => new Promise<MediaStream>((resolve) => { resolveStream = resolve; }));
    render({ ...baseDraft, resumeId: 3, attemptId: 88, question: '请介绍一次故障处理经历。' }, {
      voiceBrowser,
      keepOpenOnClose: true,
    });
    act(() => buttonByText('语音回答').click());
    act(() => buttonByText('开始录音').click());

    act(() => (container?.querySelector('[data-testid="close-drawer"]') as HTMLButtonElement).click());
    await act(async () => {
      resolveStream({ getTracks: () => [track] } as unknown as MediaStream);
      await Promise.resolve();
    });

    expect(track.stop).toHaveBeenCalledOnce();
    expect(voiceBrowser.createMediaRecorder).not.toHaveBeenCalled();
    expect(container?.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it('renders a stable workflow surface and action group', () => {
    render({ ...baseDraft, resumeId: 3 });

    expect(container?.querySelector('[data-testid="mock-interview-surface"]')).not.toBeNull();
    expect(container?.querySelector('[data-testid="mock-interview-action-group"]')).not.toBeNull();
  });

  it('preserves the initial Attempt ID/key across DELETE unknown, remount, and retry', async () => {
    const failure = { response: { status: 502, data: { error_code: 'mock_interview_unverifiable', attempt_id: 101 } } };
    services.startMockInterview.mockRejectedValue(failure);
    services.discardMockInterviewAttempt
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce(undefined);
    render({ ...baseDraft, resumeId: 3 });

    act(() => drawerButtons()[0]?.click());
    await flush();
    const originalKey = services.startMockInterview.mock.calls[0][0].attemptKey as string;
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: 101,
      attemptKey: originalKey,
      pendingOperation: 'discard',
    });

    await retryDiscardAfterRemount();
    expect(services.discardMockInterviewAttempt).toHaveBeenNthCalledWith(2, {
      applicationId: 7,
      eventId: 11,
      attemptId: 101,
    });
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: null,
      attemptKey: null,
      resultUnknown: false,
    });
  });

  it('retains the original key and server Attempt after an unknown Provider result', async () => {
    services.startMockInterview.mockRejectedValue({
      response: { status: 502, data: { error_code: 'mock_interview_provider_error', attempt_id: 202 } },
    });
    render({ ...baseDraft, resumeId: 3 });

    act(() => drawerButtons()[0]?.click());
    await flush();
    const originalKey = services.startMockInterview.mock.calls[0][0].attemptKey as string;
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: 202,
      attemptKey: originalKey,
      pendingOperation: 'start',
      resultUnknown: true,
    });
    expect(services.discardMockInterviewAttempt).not.toHaveBeenCalled();
  });

  it('does not claim current sources before a resume and JD are present', () => {
    render({ ...baseDraft, jdText: '', resumeId: undefined });

    expect(container?.textContent).not.toContain('当前使用来源');
  });

  it.each([
    ['answer', { attemptId: 301, attemptKey: 'answer-attempt', answer: 'answer' }, 0],
    ['next question', { attemptId: 302, attemptKey: 'question-attempt', answer: 'answer', answerSubmitted: true }, 2],
    ['feedback', { attemptId: 303, attemptKey: 'feedback-attempt', answer: 'answer', answerSubmitted: true }, 1],
  ])('cleans a deterministic %s failure only after DELETE is retried', async (_operation, patch, buttonIndex) => {
    const operation = _operation === 'answer'
      ? services.submitMockInterviewAnswer
      : _operation === 'next question'
        ? services.generateMockInterviewQuestion
        : services.finishMockInterview;
    operation.mockRejectedValue({ response: { status: 502, data: { error_code: 'mock_interview_unverifiable' } } });
    services.discardMockInterviewAttempt
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce(undefined);
    render({ ...baseDraft, ...patch });

    act(() => drawerButtons()[buttonIndex]?.click());
    await flush();
    expect(services.discardMockInterviewAttempt).toHaveBeenCalledWith({
      applicationId: 7,
      eventId: 11,
      attemptId: patch.attemptId,
    });
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: patch.attemptId,
      attemptKey: patch.attemptKey,
      pendingOperation: 'discard',
      resultUnknown: true,
    });

    await retryDiscardAfterRemount();
    expect(services.discardMockInterviewAttempt).toHaveBeenCalledTimes(2);
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: null,
      attemptKey: null,
      resultUnknown: false,
    });
  });

  it('clears a pre-attempt 422 without issuing DELETE', async () => {
    services.startMockInterview.mockRejectedValue({ response: { status: 422, data: { error_code: 'mock_interview_event_required' } } });
    render({ ...baseDraft, resumeId: 3 });

    act(() => drawerButtons()[0]?.click());
    await flush();
    expect(services.discardMockInterviewAttempt).not.toHaveBeenCalled();
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: null,
      attemptKey: null,
      resultUnknown: false,
    });
  });

  it('keeps a saved voice snapshot when a later question fails deterministically', async () => {
    services.generateMockInterviewQuestion.mockRejectedValue({
      response: { status: 422, data: { error_code: 'mock_interview_question_invalid' } },
    });
    render({
      ...baseDraft,
      attemptId: 408,
      attemptKey: 'attempt-408',
      answer: 'answer',
      answerSubmitted: true,
      hasSavedVoiceCoachingSnapshot: true,
      hasSubmittedVoiceAnswer: true,
      voiceCoachingReview: {
        turnNo: 1,
        summary: {
          totalDurationMs: 42_000,
          voicedDurationMs: 30_000,
          pauseCount: 1,
          longestPauseMs: 2_800,
          speechRateCpm: 120,
          fillerOccurrences: [],
        },
        reflectionText: '',
        focusKind: null,
        originSnapshotId: null,
        idempotencyKey: 'voice-save-key-408',
        saveState: 'saved',
        snapshotId: 408,
      },
    });

    act(() => buttonByText('生成下一题').click());
    await flush();

    expect(services.discardMockInterviewAttempt).not.toHaveBeenCalled();
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}'))
      .toMatchObject({ attemptId: null, hasSavedVoiceCoachingSnapshot: false });
  });

  it('keeps a submitted voice answer when the user skipped saving and a later question fails', async () => {
    services.generateMockInterviewQuestion.mockRejectedValue({
      response: { status: 422, data: { error_code: 'mock_interview_question_invalid' } },
    });
    render({
      ...baseDraft,
      attemptId: 409,
      attemptKey: 'attempt-409',
      answer: 'answer',
      answerSubmitted: true,
      hasSubmittedVoiceAnswer: true,
      hasSavedVoiceCoachingSnapshot: false,
      voiceCoachingReview: null,
    });

    act(() => drawerButtons()[2]?.click());
    await flush();

    expect(services.discardMockInterviewAttempt).not.toHaveBeenCalled();
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}'))
      .toMatchObject({ attemptId: null, hasSubmittedVoiceAnswer: false });
  });

  it('treats a protected Attempt DELETE conflict as a completed local cleanup', async () => {
    services.submitMockInterviewAnswer.mockRejectedValue({
      response: { status: 502, data: { error_code: 'mock_interview_unverifiable' } },
    });
    services.discardMockInterviewAttempt.mockRejectedValue({
      response: { status: 409, data: { error_code: 'mock_interview_attempt_confirmed' } },
    });
    render({ ...baseDraft, attemptId: 410, attemptKey: 'attempt-410', answer: 'answer' });

    act(() => drawerButtons()[0]?.click());
    await flush();

    expect(services.discardMockInterviewAttempt).toHaveBeenCalledTimes(1);
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: null,
      attemptKey: null,
      resultUnknown: false,
    });
  });

  it('treats DELETE 404 as an already-completed cleanup', async () => {
    services.submitMockInterviewAnswer.mockRejectedValue({
      response: { status: 502, data: { error_code: 'mock_interview_unverifiable' } },
    });
    services.discardMockInterviewAttempt.mockRejectedValue({ response: { status: 404 } });
    render({ ...baseDraft, attemptId: 404, attemptKey: 'attempt-404', answer: 'answer' });

    act(() => drawerButtons()[0]?.click());
    await flush();
    expect(services.discardMockInterviewAttempt).toHaveBeenCalledTimes(1);
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: null,
      attemptKey: null,
      resultUnknown: false,
    });
  });

  it('replays an unknown confirmation after remount with the same key and selected blocks', async () => {
    services.confirmMockInterviewReviewDraft
      .mockRejectedValueOnce({ response: { status: 502, data: { error_code: 'mock_interview_provider_error' } } })
      .mockResolvedValueOnce({ draft_id: 77, status: 'confirmed' });
    const proposal = {
      schema_version: '1',
      proposal_status: 'normal' as const,
      strengths: [{ id: 'strength-1', text: 'Clear answer', evidence_refs: [{ source: 'turn', path: '/turns/001/answer', excerpt: 'answer' }] }],
      practice_points: [],
      follow_up_questions: [],
      next_practice_steps: [],
    };
    render({
      ...baseDraft,
      attemptId: 700,
      attemptKey: 'attempt-700',
      proposalId: 701,
      proposal,
      selectedIds: ['strength-1'],
    });

    act(() => drawerButtons()[0]?.click());
    await flush();
    act(() => drawerButtons()[0]?.click());
    await flush();
    const firstRequest = services.confirmMockInterviewReviewDraft.mock.calls[0][0];
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: 700,
      confirmationKey: firstRequest.confirmationKey,
      pendingOperation: 'confirm',
      resultUnknown: true,
    });
    expect([...container?.querySelectorAll('input, textarea') ?? []].every((control) => (control as HTMLInputElement).disabled)).toBe(true);
    expect(drawerButtons().slice(1).every((button) => button.disabled)).toBe(true);
    drawerButtons().slice(1).forEach((button) => button.click());
    await flush();
    expect(services.confirmMockInterviewReviewDraft).toHaveBeenCalledTimes(1);

    await retryDiscardAfterRemount();
    const secondRequest = services.confirmMockInterviewReviewDraft.mock.calls[1][0];
    expect(secondRequest.confirmationKey).toBe(firstRequest.confirmationKey);
    expect(secondRequest.selectedBlocks).toEqual(firstRequest.selectedBlocks);
    const finalDraft = JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}');
    expect(finalDraft).toMatchObject({ resultUnknown: false });
    expect(finalDraft).not.toHaveProperty('pendingOperation');
  });

  it.each([
    ['answer', { attemptId: 501, attemptKey: 'answer-attempt', answer: 'answer' }, services.submitMockInterviewAnswer],
    ['question', { attemptId: 502, attemptKey: 'question-attempt', answer: 'answer', answerSubmitted: true }, services.generateMockInterviewQuestion],
    ['feedback', { attemptId: 503, attemptKey: 'feedback-attempt', answer: 'answer', answerSubmitted: true }, services.finishMockInterview],
  ])('retries an unknown %s result through the same endpoint and key after remount', async (_operation, patch, operation) => {
    operation
      .mockRejectedValueOnce({ response: { status: 502, data: { error_code: 'mock_interview_provider_error', attempt_id: patch.attemptId } } })
      .mockResolvedValueOnce(_operation === 'answer'
        ? { attempt_id: patch.attemptId, attempt_status: 'in_progress', transcript_fingerprint: 'fingerprint' }
        : _operation === 'question'
          ? { attempt_id: patch.attemptId, turn: { turn_no: 2, question: 'Next question', answer: '' } }
          : {
            attempt_id: patch.attemptId,
            proposal_id: 9,
            proposal: {
              proposal_status: 'safe_empty',
              strengths: [],
              practice_points: [],
              follow_up_questions: [],
              next_practice_steps: [],
            },
          });
    render({ ...baseDraft, ...patch });

    const initialButtonIndex = _operation === 'answer' ? 0 : _operation === 'question' ? 2 : 1;
    act(() => drawerButtons()[initialButtonIndex]?.click());
    await flush();
    const firstRequest = operation.mock.calls[0][0];
    const expectedKey = _operation === 'answer'
      ? firstRequest.turnKey
      : _operation === 'question'
        ? firstRequest.questionKey
        : firstRequest.feedbackKey;
    expect(JSON.parse(container?.querySelector('[data-testid="draft-state"]')?.textContent ?? '{}')).toMatchObject({
      attemptId: patch.attemptId,
      pendingOperation: _operation,
      resultUnknown: true,
    });
    expect([...container?.querySelectorAll('textarea') ?? []].every((textarea) => textarea.disabled)).toBe(true);
    expect(drawerButtons().slice(1).every((button) => button.disabled)).toBe(true);
    drawerButtons().slice(1).forEach((button) => button.click());
    await flush();
    expect(operation).toHaveBeenCalledTimes(1);

    await retryDiscardAfterRemount();
    const retryRequest = operation.mock.calls[1][0];
    expect(operation).toHaveBeenCalledTimes(2);
    expect(retryRequest).toMatchObject({
      attemptId: patch.attemptId,
      ...(_operation === 'answer' ? { turnKey: expectedKey } : {}),
      ...(_operation === 'question' ? { questionKey: expectedKey } : {}),
      ...(_operation === 'feedback' ? { feedbackKey: expectedKey } : {}),
    });
    expect(services.startMockInterview).not.toHaveBeenCalled();
  });
});
