import { Alert, Button, Checkbox, Select, Space, Tag, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import {
  clearConversationReadiness,
  confirmConversationReadiness,
  getConversationReadinessContext,
  getConversationReadinessOptions,
  type ConversationReadinessContext,
} from '@/services/conversationReadiness';
import { isSafeReadinessFeedbackItem, type ReadinessFeedbackItem } from '@/features/reviewReadiness/contracts';
import type { ScheduleEvent } from '@/types/event';
import type { Resume } from '@/types/resume';

export interface ConversationReadinessControlProps {
  conversationId?: number;
  applicationId?: number;
  /** An explicit task target may be supplied; the user can still change it. */
  targetEventId?: number;
  resumeId?: number;
  /** Candidate version IDs are displayed in this order; none is selected automatically. */
  availableVersionIds?: readonly number[];
  /** A caller can provide a selection already made by the user. */
  selectedVersionIds?: readonly number[];
  busy?: boolean;
  onChanged?: (value: ConversationReadinessContext) => void;
}

const TARGET_STATUSES = new Set(['todo', 'pending', 'scheduled', 'in_progress']);

function newMutationId(): string {
  return crypto.randomUUID();
}

function eventLabel(event: ScheduleEvent): string {
  const kind = event.subtype?.trim() || `第 ${event.round || 1} 轮`;
  const when = event.scheduled_at ? ` · ${new Date(event.scheduled_at).toLocaleString()}` : '';
  return `${kind}${when}`;
}

function resumeLabel(resume: Resume): string {
  return resume.title?.trim() || resume.name?.trim() || `简历 #${resume.id}`;
}

function safeTargetEvents(events: ScheduleEvent[]): ScheduleEvent[] {
  return events.filter((event) => event.event_type === 'interview' && TARGET_STATUSES.has(event.status));
}

function uniquePositive(values: readonly number[]): number[] {
  return [...new Set(values)].filter((value) => Number.isSafeInteger(value) && value > 0).slice(0, 8);
}

export default function ConversationReadinessControl({
  conversationId,
  applicationId,
  targetEventId,
  resumeId,
  availableVersionIds = [],
  selectedVersionIds,
  busy = false,
  onChanged,
}: ConversationReadinessControlProps) {
  const [context, setContext] = useState<ConversationReadinessContext>();
  const [events, setEvents] = useState<ScheduleEvent[]>([]);
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [readiness, setReadiness] = useState<ReadinessFeedbackItem[]>([]);
  const [chosenTarget, setChosenTarget] = useState<number | undefined>(targetEventId);
  const [chosenResume, setChosenResume] = useState<number | undefined>(resumeId);
  const [selection, setSelection] = useState<number[]>(() => [...(selectedVersionIds ?? [])]);
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [loadingContext, setLoadingContext] = useState(false);
  const [contextReady, setContextReady] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);
  const generation = useRef(0);
  const currentConversation = useRef(conversationId);
  currentConversation.current = conversationId;
  const selectedVersionKey = selectedVersionIds?.join(',') ?? '';

  useEffect(() => {
    const generationId = ++generation.current;
    setContext(undefined);
    setEvents([]);
    setResumes([]);
    setReadiness([]);
    setChosenTarget(targetEventId);
    setChosenResume(resumeId);
    setSelection(uniquePositive(selectedVersionIds ?? []));
    setError('');
    setExpanded(false);
    setLoadingContext(Boolean(conversationId));
    setContextReady(false);
    if (!conversationId) return;
    const id = conversationId;
    void getConversationReadinessContext(id)
      .then((value) => {
        if (generation.current !== generationId || currentConversation.current !== id) return;
        setContext(value);
        // Re-display an existing explicit binding when the caller did not pass
        // a task target.  A withdrawn row never supplies a target implicitly.
        if (targetEventId === undefined && value.state === 'confirmed' && value.target_event_id) {
          setChosenTarget(value.target_event_id);
        }
        if (resumeId === undefined && value.state === 'confirmed' && value.resume_id) {
          setChosenResume(value.resume_id);
        }
        if (selectedVersionIds === undefined && value.state === 'confirmed') {
          setSelection(uniquePositive(value.ordered_version_ids));
        }
        setContextReady(true);
      })
      .catch(() => {
        if (generation.current === generationId && currentConversation.current === id) {
          setError('本次面试的准备重点读取失败。');
          setContextReady(false);
        }
      })
      .finally(() => {
        if (generation.current === generationId && currentConversation.current === id) {
          setLoadingContext(false);
        }
      });
  }, [conversationId, selectedVersionKey, targetEventId, resumeId]);

  useEffect(() => {
    const generationId = generation.current;
    if (!applicationId) {
      setEvents([]);
      setResumes([]);
      return;
    }
    const id = applicationId;
    const conversation = conversationId;
    setLoadingOptions(true);
    void getConversationReadinessOptions(id, chosenTarget)
      .then((value) => {
        if (generation.current !== generationId || currentConversation.current !== conversation) return;
        setEvents(value.events);
        setResumes(value.resumes.filter((item) => item.deleted_at === null));
        setReadiness(value.readiness);
      })
      .catch(() => {
        if (generation.current === generationId && currentConversation.current === conversation) {
          setError('面试、简历或准备重点读取失败。');
        }
      })
      .finally(() => {
        if (generation.current === generationId && currentConversation.current === conversation) {
          setLoadingOptions(false);
        }
      });
  }, [applicationId, chosenTarget, conversationId]);

  if (!conversationId || !applicationId) return null;

  const targetChoices = safeTargetEvents(events);
  const resumeChoices = resumes;
  const fallbackVersionIds = uniquePositive(availableVersionIds);
  const feedbackChoices = readiness.filter((item) => isSafeReadinessFeedbackItem(item));
  const visibleVersionIds = feedbackChoices.length > 0
    ? feedbackChoices.map((item) => item.versionId)
    : fallbackVersionIds;
  const active = context?.state === 'confirmed';
  const canConfirm = Boolean(chosenTarget && chosenResume)
    && contextReady
    && !loadingContext
    && !busy
    && !working
    && !loadingOptions;

  const statusSummary = context?.state === 'confirmed'
    ? `面试 ${context.target_event_id} · 简历 ${context.resume_id} · ${context.ordered_version_ids.length ? `已选 ${context.ordered_version_ids.length} 个重点` : '明确空选择'}`
    : context?.state === 'withdrawn'
      ? '本次回复不会使用准备重点。'
      : '尚未绑定准备重点。';

  function chooseTarget(value: number) {
    setChosenTarget(value);
    // A selection from a different target must be made explicitly again.
    setSelection([]);
    setReadiness([]);
  }

  function toggleVersion(versionId: number, checked: boolean) {
    setSelection((current) => {
      if (checked) return current.includes(versionId) ? current : [...current, versionId].slice(0, 8);
      return current.filter((value) => value !== versionId);
    });
  }

  async function confirm() {
    if (!canConfirm || !chosenTarget || !chosenResume || conversationId === undefined) return;
    const id = conversationId;
    const chosen = uniquePositive(selection);
    setWorking(true);
    setError('');
    try {
      const value = await confirmConversationReadiness(id, {
        mutation_id: newMutationId(),
        expected_revision: context?.revision ?? 0,
        confirmed: true,
        target_event_id: chosenTarget,
        resume_id: chosenResume,
        ordered_version_ids: chosen,
      });
      if (currentConversation.current === id) {
        setContext(value);
        onChanged?.(value);
      }
    } catch {
      if (currentConversation.current === id) setError('准备重点未确认，可能已被其他页面修改，请刷新后重试。');
    } finally {
      if (currentConversation.current === id) setWorking(false);
    }
  }

  async function clear() {
    if (!active || busy || working || conversationId === undefined) return;
    const id = conversationId;
    setWorking(true);
    setError('');
    try {
      const value = await clearConversationReadiness(id, {
        mutation_id: newMutationId(),
        expected_revision: context?.revision ?? 0,
        confirmed: true,
      });
      if (currentConversation.current === id) {
        setContext(value);
        onChanged?.(value);
      }
    } catch {
      if (currentConversation.current === id) setError('准备重点未撤回，请刷新后重试。');
    } finally {
      if (currentConversation.current === id) setWorking(false);
    }
  }

  return (
    <section aria-labelledby="conversation-readiness-title" style={{ display: 'grid', gap: 12 }}>
      <div>
        <Button
          id="conversation-readiness-title"
          type="text"
          aria-expanded={expanded}
          aria-controls="conversation-readiness-panel"
          onClick={() => setExpanded((value) => !value)}
          style={{ paddingInline: 0, fontWeight: 600 }}
        >
          本次面试的准备重点
        </Button>
        <div role="status" style={{ marginTop: 4 }}>
          <Space wrap>
            {context?.state === 'confirmed' && <Tag color="green">已确认</Tag>}
            {context?.state === 'withdrawn' && <Tag>已撤回</Tag>}
            <Typography.Text type="secondary">{statusSummary}</Typography.Text>
          </Space>
        </div>
      </div>
      {error && <Alert type="error" showIcon message={error} />}
      {expanded && (
        <div id="conversation-readiness-panel" style={{ display: 'grid', gap: 12 }}>
          <Typography.Text type="secondary">
            先选择目标面试和简历，再从当前有效的复盘重点中明确勾选；不会自动猜测目标。
          </Typography.Text>
          <Space direction="vertical" style={{ width: '100%' }}>
            <label htmlFor="readiness-target-event">目标面试</label>
            <Select
              id="readiness-target-event"
              aria-label="目标面试"
              placeholder="选择一次即将进行的面试"
              value={chosenTarget}
              options={targetChoices.map((event) => ({ label: eventLabel(event), value: event.id }))}
              loading={loadingOptions && events.length === 0}
              disabled={busy || working || loadingContext}
              onChange={chooseTarget}
              style={{ width: '100%' }}
            />
            <label htmlFor="readiness-resume">本次使用的简历</label>
            <Select
              id="readiness-resume"
              aria-label="本次使用的简历"
              placeholder="选择一份简历"
              value={chosenResume}
              options={resumeChoices.map((resume) => ({ label: resumeLabel(resume), value: resume.id }))}
              loading={loadingOptions && resumes.length === 0}
              disabled={busy || working || loadingContext}
              onChange={setChosenResume}
              style={{ width: '100%' }}
            />
          </Space>
          {chosenTarget && feedbackChoices.length > 0 && (
            <div aria-label="可选的准备重点版本" style={{ display: 'grid', gap: 8 }}>
              {feedbackChoices.map((item) => (
                <Checkbox
                  key={item.versionId}
                  checked={selection.includes(item.versionId)}
                  disabled={busy || working || loadingContext}
                  onChange={(event) => toggleVersion(item.versionId, event.target.checked)}
                >
                  <span>{item.title}</span>
                  <Typography.Text type="secondary"> · {item.sourceLabel}</Typography.Text>
                </Checkbox>
              ))}
            </div>
          )}
          {chosenTarget && feedbackChoices.length === 0 && fallbackVersionIds.length === 0 && !loadingOptions && (
            <Typography.Text type="secondary">当前面试没有可用的已确认准备重点。仍需点击确认，明确选择空的准备重点。</Typography.Text>
          )}
          <Space wrap>
            <Button type="primary" loading={working} disabled={!canConfirm} onClick={() => void confirm()}>
              确认绑定准备重点
            </Button>
            {active && (
              <Button loading={working} disabled={busy || working} onClick={() => void clear()}>
                撤回绑定
              </Button>
            )}
          </Space>
          {visibleVersionIds.length > 8 && <Typography.Text type="secondary">最多选择 8 个准备重点。</Typography.Text>}
        </div>
      )}
    </section>
  );
}

export { ConversationReadinessControl };
