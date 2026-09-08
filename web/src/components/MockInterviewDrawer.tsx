import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Drawer, Empty, Input, List, Select, Space, Spin, Tag,
} from 'antd';
import {
  confirmMockInterviewReviewDraft,
  discardMockInterviewAttempt,
  finishMockInterview,
  generateMockInterviewQuestion,
  listMockInterviewHistory,
  startMockInterview,
  submitMockInterviewAnswer,
} from '@/services/mockInterviews';
import { listInterviewPreparationProposals } from '@/services/interviewPreparationProposals';
import type { InterviewPreparationProposal } from '@/types/interviewPreparationProposal';
import type {
  MockInterviewFeedbackBlock,
  MockInterviewHistoryItem,
  MockInterviewProposal,
} from '@/types/mockInterview';
import type {
  VoiceCoachingPendingReview,
  VoiceCoachingRecommendation,
} from '@/types/voiceCoaching';
import {
  getVoiceCoachingSnapshot,
  saveVoiceCoachingSnapshot,
} from '@/services/voiceCoaching';
import { ConfirmationPanel } from './ui/ConfirmationPanel';
import { SourceStateTag } from './ui/SourceStateTag';
import VoiceAnswerComposer, {
  type VoiceAnswerActivity,
  type VoiceAnswerBrowser,
} from '@/features/mockInterviewVoice/VoiceAnswerComposer';
import VoiceCoachingSnapshotSaveCard from './VoiceCoachingSnapshotSaveCard';
import workflowStyles from './ui/WorkflowSurface.module.css';

export interface MockInterviewDrawerDraft {
  resumeId?: number;
  jdText: string;
  jdVersionId?: number;
  attemptKey: string | null;
  questionKey: string | null;
  feedbackKey: string | null;
  turnKey: string | null;
  nextQuestionKey: string | null;
  confirmationKey: string | null;
  answerSubmitted: boolean;
  editedBlocks: Record<string, string>;
  preparationProposalId?: number;
  preparationItemIds: string[];
  attemptId: number | null;
  turnNo: number;
  question: string;
  answer: string;
  proposalId: number | null;
  proposal: MockInterviewProposal | null;
  selectedIds: string[];
  resultUnknown: boolean;
  retryAfterMs?: number;
  pendingOperation?: 'start' | 'answer' | 'question' | 'feedback' | 'confirm' | 'discard';
  error: string | null;
  voiceCoachingReview?: VoiceCoachingPendingReview | null;
  voicePracticeFocus?: VoiceCoachingRecommendation | null;
  hasSavedVoiceCoachingSnapshot?: boolean;
  hasSubmittedVoiceAnswer?: boolean;
}

interface ResumeOption { id: number; title?: string; name?: string }

interface Props {
  open: boolean;
  applicationId: number;
  eventId: number;
  resumes: ResumeOption[];
  draft: MockInterviewDrawerDraft;
  onDraftChange: (patch: Partial<MockInterviewDrawerDraft>) => void;
  onClose: () => void;
  onVoiceActivityChange?: (activity: VoiceAnswerActivity) => void;
  voiceBrowser?: VoiceAnswerBrowser;
}

function key(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `mock-interview-${Date.now()}`;
}

function blocks(proposal: MockInterviewProposal | null): MockInterviewFeedbackBlock[] {
  if (!proposal) return [];
  return [
    ...proposal.strengths,
    ...proposal.practice_points,
    ...proposal.follow_up_questions,
    ...proposal.next_practice_steps,
  ];
}

function safeError(error: unknown): string {
  const errorCode = typeof error === 'object' && error !== null
    ? (error as { response?: { data?: { error_code?: string } } }).response?.data?.error_code
    : undefined;
  if (errorCode === 'mock_interview_unverifiable') return 'AI 输出未通过验证，请重新开始本次模拟面试。';
  const status = typeof error === 'object' && error !== null
    ? (error as { response?: { status?: number } }).response?.status
    : undefined;
  const code = typeof error === 'object' && error !== null
    ? (error as { response?: { data?: { error_code?: string } } }).response?.data?.error_code
    : undefined;
  if (status === 409) return '本次操作与已有结果冲突，请检查后重新开始。';
  if (code === 'mock_interview_unverifiable') return 'AI 输出未通过验证，请使用原尝试重试。';
  if (code === 'mock_interview_provider_error' || status === 502) return 'AI 服务暂不可用，结果待确认，请使用原尝试重试。';
  if (!status) return '操作结果待确认，请使用原尝试重试。';
  return status === 404
    ? '面试事件或投递已不可见，当前流程已关闭。'
    : status === 422
      ? '输入无法用于本次模拟面试，请检查简历和 JD。'
      : '操作结果待确认，请使用原尝试重试。';
}

function isUnknownResult(error: unknown): boolean {
  const response = (error as { response?: { status?: number; data?: { error_code?: string } } })?.response;
  const status = response?.status;
  const code = response?.data?.error_code;
  if (code === 'mock_interview_unverifiable') return false;
  return !status || status >= 500 || code === 'mock_interview_provider_error';
}

function persistedReviewMatches(
  snapshot: Awaited<ReturnType<typeof getVoiceCoachingSnapshot>>,
  review: VoiceCoachingPendingReview,
): boolean {
  const persistedFillers = snapshot.filler_occurrences.map((item) => ({
    text: item.text,
    count: item.count,
    transcriptOffsets: item.transcript_offsets,
  }));
  return snapshot.total_duration_ms === review.summary.totalDurationMs
    && snapshot.voiced_duration_ms === review.summary.voicedDurationMs
    && snapshot.pause_count === review.summary.pauseCount
    && snapshot.longest_pause_ms === review.summary.longestPauseMs
    && snapshot.speech_rate_cpm === (review.summary.speechRateCpm ?? null)
    && JSON.stringify(persistedFillers) === JSON.stringify(review.summary.fillerOccurrences)
    && snapshot.reflection_text === review.reflectionText
    && snapshot.focus_kind === review.focusKind
    && snapshot.origin_snapshot_id === review.originSnapshotId;
}

export default function MockInterviewDrawer({
  open, applicationId, eventId, resumes, draft, onDraftChange, onClose,
  onVoiceActivityChange, voiceBrowser,
}: Props) {
  const [history, setHistory] = useState<MockInterviewHistoryItem[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [working, setWorking] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [preparations, setPreparations] = useState<InterviewPreparationProposal[]>([]);
  const [voiceDraftDirty, setVoiceDraftDirty] = useState(false);
  const [closeConfirming, setCloseConfirming] = useState(false);
  const [answerSubmitRevision, setAnswerSubmitRevision] = useState(0);
  const voiceCleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoadingHistory(true);
    listMockInterviewHistory(applicationId, eventId)
      .then(setHistory)
      .catch(() => setHistory([]))
      .finally(() => setLoadingHistory(false));
  }, [applicationId, eventId, open]);

  useEffect(() => {
    if (!open) return;
    listInterviewPreparationProposals(applicationId)
      .then((items) => setPreparations(items.filter((item) => item.event_id === eventId && item.source_status !== 'source_changed')))
      .catch(() => setPreparations([]));
  }, [applicationId, eventId, open]);

  const selectedBlocks = useMemo(
    () => blocks(draft.proposal)
      .filter((item) => draft.selectedIds.includes(item.id))
      .map((item) => ({ ...item, text: draft.editedBlocks[item.id] ?? item.text })),
    [draft.proposal, draft.selectedIds, draft.editedBlocks],
  );
  const pending = draft.resultUnknown;
  const voiceReview = draft.voiceCoachingReview?.turnNo === draft.turnNo
    ? draft.voiceCoachingReview
    : null;
  const voiceReviewNeedsDecision = Boolean(
    draft.answerSubmitted
    && voiceReview
    && voiceReview.saveState !== 'saved',
  );

  function resetDraft(error?: unknown): void {
    onDraftChange({
      attemptId: null,
      attemptKey: null,
      questionKey: null,
      feedbackKey: null,
      turnKey: null,
      nextQuestionKey: null,
      confirmationKey: null,
      answerSubmitted: false,
      question: '',
      answer: '',
      proposalId: null,
      proposal: null,
      selectedIds: [],
      preparationItemIds: [],
      editedBlocks: {},
      resultUnknown: false,
      pendingOperation: undefined,
      voiceCoachingReview: null,
      voicePracticeFocus: null,
      hasSavedVoiceCoachingSnapshot: false,
      hasSubmittedVoiceAnswer: false,
      error: error === undefined ? null : safeError(error),
    });
  }

  async function clearDefiniteAttempt(sourceError?: unknown, attemptKeyOverride?: string): Promise<boolean> {
    const response = (sourceError as { response?: { status?: number; data?: { attempt_id?: unknown } } })?.response;
    const responseAttemptId = response?.data?.attempt_id;
    const currentAttemptKey = attemptKeyOverride ?? draft.attemptKey;
    const attemptId = draft.attemptId ?? (typeof responseAttemptId === 'number' ? responseAttemptId : null);
    if (attemptId && (draft.hasSavedVoiceCoachingSnapshot || draft.hasSubmittedVoiceAnswer)) {
      resetDraft(sourceError);
      return true;
    }
    if (!attemptId) {
      if (response?.status === 422) {
        resetDraft(sourceError);
        return true;
      }
      if (currentAttemptKey) {
        onDraftChange({
          attemptId: null,
          attemptKey: currentAttemptKey,
          resultUnknown: true,
          pendingOperation: 'discard',
          error: safeError({}),
        });
        return false;
      }
      resetDraft(sourceError);
      return true;
    }
    onDraftChange({
      attemptId,
      attemptKey: currentAttemptKey ?? null,
      resultUnknown: true,
      pendingOperation: 'discard',
      error: safeError({}),
    });
    try {
      await discardMockInterviewAttempt({ applicationId, eventId, attemptId });
      resetDraft(sourceError);
      return true;
    } catch (error) {
      const discardResponse = (error as {
        response?: { status?: number; data?: { error_code?: string } };
      })?.response;
      if (
        discardResponse?.status === 404
        || (
          discardResponse?.status === 409
          && discardResponse.data?.error_code === 'mock_interview_attempt_confirmed'
        )
      ) {
        resetDraft(sourceError);
        return true;
      }
      onDraftChange({
        attemptId,
        attemptKey: currentAttemptKey ?? null,
        resultUnknown: true,
        pendingOperation: 'discard',
        error: safeError({}),
      });
      return false;
    }
  }

  const start = async () => {
    if (!draft.resumeId || !draft.jdVersionId) return;
    const attemptKey = draft.attemptKey ?? key();
    const questionKey = draft.questionKey ?? key();
    onDraftChange({ attemptKey, questionKey, error: null });
    setWorking(true);
    try {
      const result = await startMockInterview({
        applicationId, eventId, resumeId: draft.resumeId, jdVersionId: draft.jdVersionId,
        attemptKey,
        questionKey,
        preparationProposalId: draft.preparationProposalId,
        preparationItemIds: draft.preparationItemIds,
      });
      if (!('turn' in result)) {
        onDraftChange({ attemptId: result.attempt_id, pendingOperation: 'start', resultUnknown: true, retryAfterMs: result.retry_after_ms, error: safeError({ response: { status: 202 } }) });
        return;
      }
      onDraftChange({
        attemptId: result.attempt_id,
        turnNo: result.turn.turn_no,
        question: result.turn.question,
        answer: result.turn.answer,
        resultUnknown: false,
        pendingOperation: undefined,
        error: null,
      });
    } catch (error) {
      if (isUnknownResult(error)) {
        const response = (error as { response?: { data?: { attempt_id?: unknown } } })?.response;
        const responseAttemptId = response?.data?.attempt_id;
        onDraftChange({
          attemptId: typeof responseAttemptId === 'number' ? responseAttemptId : draft.attemptId,
          attemptKey,
          pendingOperation: 'start',
          resultUnknown: true,
          error: safeError(error),
        });
      } else { await clearDefiniteAttempt(error, attemptKey); }
    } finally { setWorking(false); }
  };

  const answer = async () => {
    if (!draft.attemptId || !draft.answer.trim()) return;
    const turnKey = draft.turnKey ?? key();
    onDraftChange({ turnKey, error: null });
    setWorking(true);
    try {
      await submitMockInterviewAnswer({
        applicationId, eventId, attemptId: draft.attemptId, turnNo: draft.turnNo,
        answerText: draft.answer, turnKey,
      });
      setAnswerSubmitRevision((revision) => revision + 1);
      onDraftChange({
        error: null,
        resultUnknown: false,
        answerSubmitted: true,
        hasSubmittedVoiceAnswer: draft.hasSubmittedVoiceAnswer || Boolean(voiceReview),
      });
    } catch (error) {
      if (isUnknownResult(error)) onDraftChange({ pendingOperation: 'answer', resultUnknown: true, error: safeError(error) });
      else { await clearDefiniteAttempt(error, draft.attemptKey ?? undefined); }
    } finally { setWorking(false); }
  };

  const saveVoiceReview = async () => {
    if (!draft.attemptId || !voiceReview) return;
    const idempotencyKey = voiceReview.idempotencyKey ?? key();
    const frozenReview = { ...voiceReview, idempotencyKey, saveState: 'saving' as const };
    onDraftChange({ voiceCoachingReview: frozenReview, error: null });
    try {
      const snapshot = await saveVoiceCoachingSnapshot({
        applicationId,
        eventId,
        attemptId: draft.attemptId,
        turnNo: draft.turnNo,
        payload: {
          idempotency_key: idempotencyKey,
          total_duration_ms: voiceReview.summary.totalDurationMs,
          voiced_duration_ms: voiceReview.summary.voicedDurationMs,
          pause_count: voiceReview.summary.pauseCount,
          longest_pause_ms: voiceReview.summary.longestPauseMs,
          speech_rate_cpm: voiceReview.summary.speechRateCpm ?? null,
          filler_occurrences: voiceReview.summary.fillerOccurrences.map((item) => ({
            text: item.text,
            count: item.count,
            transcript_offsets: item.transcriptOffsets,
          })),
          reflection_text: voiceReview.reflectionText,
          focus_kind: voiceReview.focusKind,
          origin_snapshot_id: voiceReview.originSnapshotId,
        },
      });
      onDraftChange({
        voiceCoachingReview: {
          ...frozenReview,
          saveState: 'saved',
          snapshotId: snapshot.id,
        },
        hasSavedVoiceCoachingSnapshot: true,
      });
    } catch (error) {
      const response = (error as { response?: { status?: number; data?: { error_code?: string } } })?.response;
      const status = response?.status;
      const errorCode = response?.data?.error_code;
      if (status === 409) {
        if (errorCode === 'voice_coaching_snapshot_exists') {
          try {
            const snapshot = await getVoiceCoachingSnapshot({ applicationId, eventId, attemptId: draft.attemptId, turnNo: draft.turnNo });
            if (persistedReviewMatches(snapshot, frozenReview)) {
              onDraftChange({
                voiceCoachingReview: { ...frozenReview, saveState: 'saved', snapshotId: snapshot.id },
                hasSavedVoiceCoachingSnapshot: true,
              });
              return;
            }
          } catch {
            // A deterministic conflict remains deterministic even when the read also fails.
          }
        }
        onDraftChange({
          voiceCoachingReview: { ...frozenReview, saveState: 'conflict' },
          error: '这道回答已经保存了另一份表达复盘，当前草稿不会覆盖历史。',
        });
        return;
      }
      if (!status || status >= 500) {
        try {
          const snapshot = await getVoiceCoachingSnapshot({
            applicationId,
            eventId,
            attemptId: draft.attemptId,
            turnNo: draft.turnNo,
          });
          if (persistedReviewMatches(snapshot, frozenReview)) {
            onDraftChange({
              voiceCoachingReview: { ...frozenReview, saveState: 'saved', snapshotId: snapshot.id },
              hasSavedVoiceCoachingSnapshot: true,
            });
            return;
          }
        } catch {
          // The write outcome remains unknown and must keep the original key/input frozen.
        }
        onDraftChange({
          voiceCoachingReview: { ...frozenReview, saveState: 'unknown' },
          error: '表达复盘保存结果待确认，请使用原保存请求重试。',
        });
        return;
      }
      onDraftChange({
        voiceCoachingReview: {
          ...voiceReview,
          idempotencyKey: null,
          saveState: 'idle',
        },
        error: status === 422
          ? '本次表达复盘无法保存，请检查反思文字后重试。'
          : '本次回答已不可用于保存表达复盘。',
      });
    }
  };

  const nextQuestion = async () => {
    if (!draft.attemptId || !draft.answerSubmitted || voiceReviewNeedsDecision) return;
    const nextQuestionKey = draft.nextQuestionKey ?? key();
    onDraftChange({ nextQuestionKey, error: null });
    setWorking(true);
    try {
      const result = await generateMockInterviewQuestion({
        applicationId, eventId, attemptId: draft.attemptId,
        turnNo: draft.turnNo + 1, questionKey: nextQuestionKey,
      });
      if (!('turn' in result)) {
        onDraftChange({ pendingOperation: 'question', resultUnknown: true, retryAfterMs: result.retry_after_ms, error: '结果待确认，请使用原尝试重试。' });
        return;
      }
      onDraftChange({
        turnNo: result.turn.turn_no,
        question: result.turn.question,
        answer: result.turn.answer,
        answerSubmitted: false,
        nextQuestionKey: null,
        turnKey: null,
        resultUnknown: false,
        pendingOperation: undefined,
        voiceCoachingReview: null,
        voicePracticeFocus: null,
        error: null,
      });
    } catch (error) {
      if (isUnknownResult(error)) onDraftChange({ pendingOperation: 'question', resultUnknown: true, error: safeError(error) });
      else { await clearDefiniteAttempt(error, draft.attemptKey ?? undefined); }
    } finally { setWorking(false); }
  };

  const finish = async () => {
    if (!draft.attemptId || voiceReviewNeedsDecision) return;
    const feedbackKey = draft.feedbackKey ?? key();
    onDraftChange({ feedbackKey, error: null });
    setWorking(true);
    try {
      const result = await finishMockInterview({ applicationId, eventId, attemptId: draft.attemptId, feedbackKey });
      if (!('proposal' in result)) {
        onDraftChange({ pendingOperation: 'feedback', resultUnknown: true, retryAfterMs: result.retry_after_ms, error: '结果待确认，请使用原尝试重试。' });
        return;
      }
      onDraftChange({ proposalId: result.proposal_id, proposal: result.proposal, selectedIds: [], resultUnknown: false, pendingOperation: undefined, error: null });
    } catch (error) {
      if (isUnknownResult(error)) onDraftChange({ pendingOperation: 'feedback', resultUnknown: true, error: safeError(error) });
      else { await clearDefiniteAttempt(error, draft.attemptKey ?? undefined); }
    } finally { setWorking(false); }
  };

  const confirmDraft = async () => {
    if (!draft.attemptId || !draft.proposalId || selectedBlocks.length === 0) return;
    const confirmationKey = draft.confirmationKey ?? key();
    onDraftChange({ confirmationKey, error: null });
    setWorking(true);
    try {
      await confirmMockInterviewReviewDraft({
        applicationId, eventId, attemptId: draft.attemptId, proposalId: draft.proposalId,
        confirmationKey, selectedBlocks,
      });
      setConfirming(false);
      onDraftChange({ error: null, resultUnknown: false, pendingOperation: undefined });
    } catch (error) {
      if (isUnknownResult(error)) {
        onDraftChange({ pendingOperation: 'confirm', resultUnknown: true, error: safeError(error) });
      } else {
        onDraftChange({ error: safeError(error) });
      }
    } finally { setWorking(false); }
  };

  const retryPendingOperation = () => {
    switch (draft.pendingOperation) {
      case 'start': return start();
      case 'answer': return answer();
      case 'question': return nextQuestion();
      case 'feedback': return finish();
      case 'confirm': return confirmDraft();
      case 'discard': return clearDefiniteAttempt();
      default: return undefined;
    }
  };

  const requestClose = () => {
    if (voiceDraftDirty || voiceReviewNeedsDecision) {
      setCloseConfirming(true);
      return;
    }
    voiceCleanupRef.current?.();
    onClose();
  };

  return (
    <Drawer open={open} width={620} title="模拟面试" onClose={requestClose}>
      <div data-testid="mock-interview-surface" className={workflowStyles.surface}>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <span className={workflowStyles.mutedText}>仅用于练习表达。AI 不提供录用判断、通过率或岗位匹配分。</span>
        {closeConfirming ? (
          <Alert
            type="warning"
            showIcon
            message="当前录音、转写文字或待确认的表达复盘尚未处理，关闭后会丢失。"
            action={(
              <Space>
                <Button size="small" onClick={() => setCloseConfirming(false)}>继续编辑</Button>
                <Button size="small" danger onClick={() => { voiceCleanupRef.current?.(); onClose(); }}>放弃并关闭</Button>
              </Space>
            )}
          />
        ) : null}
        {draft.resumeId && draft.jdVersionId ? (
          <SourceStateTag state="current" detail="当前面试事件与本次输入" />
        ) : null}
        {draft.error ? <Alert type="warning" showIcon message={draft.error} /> : null}
        {draft.voicePracticeFocus ? (
          <Alert
            type="info"
            showIcon
            message={`本次刻意练习：${draft.voicePracticeFocus.title}`}
            description={`来自已确认表达记录 #${draft.voicePracticeFocus.source_snapshot_id}：${draft.voicePracticeFocus.question_text}`}
          />
        ) : null}
        {pending ? (
          <Alert
            type="info"
            message="结果待确认，请使用原尝试重试；输入已冻结。"
            action={(
              <Button
                size="small"
                onClick={() => void retryPendingOperation()}
                disabled={working}
              >
                使用原尝试重试
              </Button>
            )}
          />
        ) : null}
        {!draft.attemptId && !draft.proposal && !draft.resultUnknown ? (
          <section className={workflowStyles.section}>
            <Select
              aria-label="选择简历"
              placeholder="请选择本次使用的简历"
              style={{ width: '100%' }}
              value={draft.resumeId}
              onChange={(value) => onDraftChange({ resumeId: value })}
              options={resumes.map((resume) => ({ value: resume.id, label: resume.name ?? resume.title ?? `简历 ${resume.id}` }))}
            />
            <Input.TextArea
              aria-label="岗位 JD"
              placeholder="当前投递尚未确认岗位资料"
              value={draft.jdText}
              readOnly
              aria-readonly="true"
              autoSize={{ minRows: 5, maxRows: 12 }}
            />
            <span className={workflowStyles.mutedText}>
              {draft.jdVersionId ? '使用投递当前已确认的岗位资料；如需修改，请先返回 JD 版本入口。' : '请先在投递详情确认岗位资料版本。'}
            </span>
            {preparations.length > 0 ? (
              <Select
                allowClear
                aria-label="选择面试准备建议"
                placeholder="可选：选择已确认的面试准备建议"
                value={draft.preparationProposalId}
                onChange={(value) => onDraftChange({ preparationProposalId: value, preparationItemIds: [] })}
                dropdownRender={(menu) => (
                  <>
                    {menu}
                    <Select
                      mode="multiple"
                      aria-label="preparation items"
                      value={draft.preparationItemIds}
                      onChange={(value) => onDraftChange({ preparationItemIds: value as string[] })}
                      options={preparations
                        .find((item) => item.id === draft.preparationProposalId)
                        ? Object.values(preparations.find((item) => item.id === draft.preparationProposalId)!.proposal)
                          .flat()
                          .map((item) => ({ value: item.id, label: item.text }))
                        : []}
                    />
                  </>
                )}
                options={preparations.map((item) => ({ value: item.id, label: `准备建议 · ${new Date(item.created_at).toLocaleString()}` }))}
              />
            ) : null}
            <span className={workflowStyles.mutedText}>本次输入将发送给当前配置的 AI 服务。请勿粘贴无关敏感信息。</span>
            <div data-testid="mock-interview-action-group" className={workflowStyles.actionGroup}>
              <Button type="primary" onClick={() => void start()} disabled={!draft.resumeId || !draft.jdVersionId || working}>
                开始模拟面试
              </Button>
            </div>
          </section>
        ) : null}
        {working && !draft.proposal ? <Spin tip="正在处理，请稍候" /> : null}
        {draft.attemptId && !draft.proposal ? (
          <section className={workflowStyles.section}>
            <div className={workflowStyles.sectionHeader}><h3>第 {draft.turnNo} 题</h3></div>
            <p className="op-long-text">{draft.question || '请介绍一次与本次岗位相关的经历。'}</p>
            <VoiceAnswerComposer
              key={`${applicationId}:${eventId}:${draft.attemptId}:${draft.turnNo}`}
              question={draft.question || '请介绍一次与本次岗位相关的经历。'}
              disabled={pending || working}
              textValue={draft.answer}
              onTextChange={(answer) => onDraftChange({ answer })}
              submitRevision={answerSubmitRevision}
              onConfirmTranscript={(answer) => onDraftChange({ answer })}
              onVoiceReviewConfirmed={(answer, summary) => onDraftChange({
                answer,
                voiceCoachingReview: {
                  turnNo: draft.turnNo,
                  summary: {
                    totalDurationMs: summary.totalDurationMs,
                    voicedDurationMs: summary.voicedDurationMs,
                    pauseCount: summary.pauseCount,
                    longestPauseMs: summary.longestPauseMs,
                    speechRateCpm: summary.speechRateCpm,
                    fillerOccurrences: summary.fillerOccurrences,
                  },
                  reflectionText: '',
                  focusKind: draft.voicePracticeFocus?.focus_kind ?? null,
                  originSnapshotId: draft.voicePracticeFocus?.source_snapshot_id ?? null,
                  idempotencyKey: null,
                  saveState: 'idle',
                  snapshotId: null,
                },
              })}
              onDirtyChange={(dirty) => {
                setVoiceDraftDirty(dirty);
                if (!dirty) setCloseConfirming(false);
              }}
              onActivityChange={onVoiceActivityChange}
              browser={voiceBrowser}
              cleanupRef={voiceCleanupRef}
            />
            <div className={workflowStyles.actionGroup}>
              <Button onClick={() => void answer()} disabled={!draft.answer.trim() || pending || working}>提交回答</Button>
              <Button type="primary" onClick={() => void finish()} disabled={!draft.answer.trim() || !draft.answerSubmitted || voiceReviewNeedsDecision || pending || working}>结束并生成复盘建议</Button>
            </div>
          </section>
        ) : null}
        {draft.attemptId && draft.answerSubmitted && voiceReview ? (
          <VoiceCoachingSnapshotSaveCard
            review={voiceReview}
            onChange={(patch) => onDraftChange({ voiceCoachingReview: { ...voiceReview, ...patch } })}
            onSave={() => void saveVoiceReview()}
            onSkip={() => onDraftChange({ voiceCoachingReview: null })}
          />
        ) : null}
        {draft.attemptId && !draft.proposal && draft.answerSubmitted ? (
          <Button onClick={() => void nextQuestion()} disabled={voiceReviewNeedsDecision || pending || working}>生成下一题</Button>
        ) : null}
        {draft.proposal ? (
          <section className={workflowStyles.section}>
            <Tag color={draft.proposal.proposal_status === 'safe_empty' ? 'default' : 'blue'}>
              {draft.proposal.proposal_status === 'safe_empty' ? '暂无可验证建议' : 'AI 建议（待人工确认）'}
            </Tag>
            {draft.proposal.proposal_status === 'safe_empty' ? (
              <Empty description="目前没有可验证、可给出的复盘建议" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : blocks(draft.proposal).map((item) => (
              <label key={item.id} className={workflowStyles.evidenceBlock} style={{ display: 'block', marginBottom: 12 }}>
                <input
                  type="checkbox"
                  checked={draft.selectedIds.includes(item.id)}
                  disabled={pending || working}
                  onChange={(event) => onDraftChange({
                    selectedIds: event.target.checked
                      ? [...draft.selectedIds, item.id]
                      : draft.selectedIds.filter((id) => id !== item.id),
                  })}
                />{' '}
                <Input.TextArea
                  value={draft.editedBlocks[item.id] ?? item.text}
                  disabled={pending || working}
                  onChange={(event) => onDraftChange({ editedBlocks: { ...draft.editedBlocks, [item.id]: event.target.value } })}
                  autoSize={{ minRows: 2, maxRows: 5 }}
                />
                {item.evidence_refs.map((ref) => <span key={`${item.id}-${ref.path}`} className={`${workflowStyles.mutedText} op-long-text`}>（{ref.source}：{ref.excerpt}）</span>)}
              </label>
            ))}
            {draft.proposal.proposal_status === 'normal' && selectedBlocks.length > 0 && !confirming ? (
              <Button onClick={() => setConfirming(true)} disabled={pending || working}>准备保存复盘草稿</Button>
            ) : null}
            {confirming ? (
              <ConfirmationPanel
                title="确认保存模拟练习复盘草稿"
                description="确认后仅保存独立草稿，不会覆盖正式复盘或写入知识库。"
                sources={[{ state: 'frozen', detail: '本次模拟面试回答' }]}
              >
                <Button type="primary" onClick={() => void confirmDraft()} disabled={pending || working}>确认保存</Button>
              </ConfirmationPanel>
            ) : null}
          </section>
        ) : null}
        <section aria-label="历史模拟面试" className={workflowStyles.section}>
          <div className={workflowStyles.sectionHeader}><h3>历史记录（只读）</h3></div>
          {loadingHistory ? <Spin size="small" /> : null}
          {!loadingHistory && history.length === 0 ? <span style={{ color: 'var(--op-muted)' }}>暂无历史记录</span> : null}
          <List
            size="small"
            dataSource={history}
            renderItem={(item) => (
              <List.Item className={workflowStyles.listRow}>
                <Space direction="vertical" style={{ width: '100%' }} className="op-long-text">
                  <Space>
                    <span>{new Date(item.created_at).toLocaleString()}</span>
                    <Tag>{item.proposal_status === 'safe_empty' ? '暂无可验证建议' : '复盘建议（只读）'}</Tag>
                    {item.source_status === 'source_changed' ? <Tag color="warning">来源已变化</Tag> : null}
                  </Space>
                  {(item.turns ?? []).map((turn) => (
                    <div key={turn.turn_no}>
                      <div>第 {turn.turn_no} 题：{turn.question}</div>
                      <div className={workflowStyles.mutedText}>回答：{turn.answer || '（未提交）'}</div>
                    </div>
                  ))}
                  {blocks(item.proposal).map((block) => (
                    <div key={block.id}>
                      <div>{block.text}</div>
                      <div className={workflowStyles.mutedText}>
                        {block.evidence_refs.map((ref) => `${ref.source} ${ref.path}: ${ref.excerpt}`).join('；')}
                      </div>
                    </div>
                  ))}
                  {item.review_draft ? (
                    <div>
                      <Tag color="green">已确认复盘草稿</Tag>
                      {Array.isArray(item.review_draft.selected_blocks)
                        ? item.review_draft.selected_blocks.map((selected, index) => (
                          <div key={index}>{typeof selected === 'object' && selected !== null && 'text' in selected ? String(selected.text) : ''}</div>
                        ))
                        : null}
                    </div>
                  ) : null}
                </Space>
              </List.Item>
            )}
          />
        </section>
      </Space>
      </div>
    </Drawer>
  );
}
