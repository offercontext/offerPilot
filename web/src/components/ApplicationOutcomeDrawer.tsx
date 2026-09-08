import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { App, Alert, Button, DatePicker, Drawer, Empty, Form, Input, Select, Spin, Tag } from 'antd';
import { AuditOutlined, RobotOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { isAxiosError } from 'axios';
import dayjs from 'dayjs';
import type { Application } from '@/types/application';
import type { Resume } from '@/types/resume';
import type { ScheduleEvent } from '@/types/event';
import { EVENT_TYPE_LABELS } from '@/types/event';
import type { ApplicationJdVersion } from '@/types/applicationJdVersion';
import type { PilotActionRequest } from '@/types/chat';
import type {
  ApplicationFeedbackTag,
  ApplicationOutcomeResult,
  ApplicationOutcomeStage,
  CreateApplicationOutcomeInput,
  CreateApplicationSubmissionSnapshotInput,
} from '@/types/applicationOutcome';
import {
  createApplicationOutcome,
  createSubmissionSnapshot,
  getApplicationOutcomeSummary,
  listApplicationOutcomes,
  listSubmissionSnapshots,
} from '@/services/applicationOutcomes';
import { getApplicationMaterialKit } from '@/services/materialKits';
import ApplicationCommunicationDraftPanel from './ApplicationCommunicationDraftPanel';
import styles from './ApplicationOutcomeDrawer.module.css';

interface Props {
  application: Application | null;
  open: boolean;
  onClose: () => void;
  resumes: Resume[];
  currentJd: ApplicationJdVersion | null;
  events: ScheduleEvent[];
  onAskPilot?: (application: Application, action: PilotActionRequest) => void;
}

interface SnapshotFormValues {
  resumeId: number;
  submittedAt: dayjs.Dayjs;
  note: string;
  includeMaterial: boolean;
}

interface OutcomeFormValues {
  snapshotId: number;
  eventId?: number;
  stage: ApplicationOutcomeStage;
  result: ApplicationOutcomeResult;
  feedbackText: string;
  reflectionText: string;
  nextActionText: string;
  feedbackTags: ApplicationFeedbackTag[];
  occurredAt: dayjs.Dayjs;
}

type PendingWrite<T> = { input: T; resultUnknown: boolean };

const STAGE_OPTIONS = [
  ['applied', '已投递'], ['screening', '筛选沟通'], ['written_test', '笔试'],
  ['interview', '面试'], ['offer', 'Offer'], ['closed', '流程结束'],
].map(([value, label]) => ({ value, label }));
const RESULT_OPTIONS = [
  ['advanced', '进入下一阶段'], ['rejected', '未通过'], ['withdrawn', '主动退出'],
  ['no_response', '暂无回复'], ['offer_received', '收到 Offer'], ['other', '其他'],
].map(([value, label]) => ({ value, label }));
const TAG_OPTIONS = [
  ['technical_depth', '技术深度'], ['communication', '沟通表达'], ['system_design', '系统设计'],
  ['domain_experience', '领域经验'], ['leadership', '领导力'], ['collaboration', '协作'], ['other', '其他'],
].map(([value, label]) => ({ value, label }));

const labels = Object.fromEntries([...STAGE_OPTIONS, ...RESULT_OPTIONS, ...TAG_OPTIONS].map(({ value, label }) => [value, label]));

function key(): string {
  return crypto.randomUUID().replace(/-/g, '');
}

function pendingStorageKey(applicationId: number, kind: 'snapshot' | 'outcome'): string {
  return `offerpilot:application-outcome:${applicationId}:${kind}`;
}

function readPending<T>(applicationId: number, kind: 'snapshot' | 'outcome'): PendingWrite<T> | null {
  if (applicationId <= 0) return null;
  try {
    const raw = sessionStorage.getItem(pendingStorageKey(applicationId, kind));
    if (!raw) return null;
    const value = JSON.parse(raw) as PendingWrite<T>;
    return value && typeof value === 'object' && value.resultUnknown === true ? value : null;
  } catch {
    return null;
  }
}

function writePending<T>(applicationId: number, kind: 'snapshot' | 'outcome', value: PendingWrite<T> | null) {
  try {
    const storageKey = pendingStorageKey(applicationId, kind);
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
  } catch {
    // Session persistence is a recovery aid; a blocked storage backend must not break the form.
  }
}

function isUnknownWriteResult(error: unknown): boolean {
  return isAxiosError(error) && (!error.response || error.response.status >= 500);
}

function sourceLabel(source: 'resume' | 'jd' | 'material', state: 'current' | 'changed' | 'missing') {
  const sourceName = { resume: '简历', jd: 'JD', material: '材料' }[source];
  const stateName = { current: '当前', changed: '已变化', missing: '已缺失' }[state];
  return `${sourceName} · ${stateName}`;
}

function preview(value: unknown, limit = 220): string {
  let text = '';
  if (typeof value === 'string') text = value;
  else {
    try { text = JSON.stringify(value, null, 2); } catch { text = '内容暂不可预览'; }
  }
  const normalized = text.trim();
  return Array.from(normalized).length > limit
    ? `${Array.from(normalized).slice(0, limit).join('')}…`
    : normalized;
}

function resumeSnapshotPreview(snapshot: Record<string, unknown>): string {
  return typeof snapshot.raw_text === 'string' && snapshot.raw_text.trim()
    ? preview(snapshot.raw_text)
    : preview(snapshot);
}

export default function ApplicationOutcomeDrawer({ application, open, onClose, resumes, currentJd, events, onAskPilot }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [snapshotForm] = Form.useForm<SnapshotFormValues>();
  const [outcomeForm] = Form.useForm<OutcomeFormValues>();
  const applicationId = application?.id ?? 0;
  const [snapshotPending, setSnapshotPending] = useState<PendingWrite<CreateApplicationSubmissionSnapshotInput> | null>(
    () => readPending(applicationId, 'snapshot'),
  );
  const [outcomePending, setOutcomePending] = useState<PendingWrite<CreateApplicationOutcomeInput> | null>(
    () => readPending(applicationId, 'outcome'),
  );
  const enabled = open && applicationId > 0;
  const snapshots = useQuery({ queryKey: ['application-submission-snapshots', applicationId], queryFn: () => listSubmissionSnapshots(applicationId), enabled });
  const outcomes = useQuery({ queryKey: ['application-outcomes', applicationId], queryFn: () => listApplicationOutcomes(applicationId), enabled });
  const summary = useQuery({ queryKey: ['application-outcome-summary', applicationId], queryFn: () => getApplicationOutcomeSummary(applicationId), enabled });
  const kit = useQuery({ queryKey: ['material-kit', applicationId], queryFn: () => getApplicationMaterialKit(applicationId), enabled, retry: false });

  useEffect(() => {
    if (!open) return;
    const restored = readPending<CreateApplicationSubmissionSnapshotInput>(applicationId, 'snapshot');
    setSnapshotPending(restored);
    if (restored) {
      snapshotForm.setFieldsValue({
        resumeId: restored.input.resume_id,
        submittedAt: dayjs(restored.input.submitted_at),
        note: restored.input.note,
        includeMaterial: restored.input.material_kit_id !== null,
      });
      return;
    }
    const preferredResume = resumes.find((resume) => !resume.deleted_at)?.id;
    if (!snapshotForm.getFieldValue('resumeId')) {
      snapshotForm.setFieldsValue({ resumeId: preferredResume, submittedAt: dayjs(), note: '', includeMaterial: Boolean(kit.data) });
    }
  }, [applicationId, kit.data, open, resumes, snapshotForm]);
  useEffect(() => {
    const restored = readPending<CreateApplicationOutcomeInput>(applicationId, 'outcome');
    setOutcomePending(restored);
    if (open && restored) {
      outcomeForm.setFieldsValue({
        snapshotId: restored.input.submission_snapshot_id,
        eventId: restored.input.application_event_id ?? undefined,
        stage: restored.input.stage,
        result: restored.input.result,
        feedbackText: restored.input.feedback_text,
        reflectionText: restored.input.reflection_text,
        nextActionText: restored.input.next_action_text,
        feedbackTags: restored.input.feedback_tags,
        occurredAt: dayjs(restored.input.occurred_at),
      });
      return;
    }
    const firstSnapshot = snapshots.data?.[0]?.id;
    if (!open || !firstSnapshot) return;
    if (!outcomeForm.getFieldValue('snapshotId')) {
      outcomeForm.setFieldsValue({ snapshotId: firstSnapshot, stage: 'interview', result: 'advanced', feedbackTags: [], occurredAt: dayjs() });
    }
  }, [applicationId, open, outcomeForm, snapshots.data]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['application-submission-snapshots', applicationId] }),
      queryClient.invalidateQueries({ queryKey: ['application-outcomes', applicationId] }),
      queryClient.invalidateQueries({ queryKey: ['application-outcome-summary', applicationId] }),
    ]);
  };
  const saveSnapshot = useMutation({
    mutationFn: (input: CreateApplicationSubmissionSnapshotInput) => createSubmissionSnapshot(applicationId, input),
    onSuccess: async () => {
      setSnapshotPending(null);
      writePending(applicationId, 'snapshot', null);
      await refresh();
      message.success('已冻结本次投递事实');
    },
    onError: (error, input) => {
      if (isUnknownWriteResult(error)) {
        const pending = { input, resultUnknown: true };
        setSnapshotPending(pending);
        writePending(applicationId, 'snapshot', pending);
        message.error('保存结果待确认，请使用原尝试重试');
        return;
      }
      setSnapshotPending(null);
      writePending(applicationId, 'snapshot', null);
      message.error('保存失败，请检查输入后重试');
    },
  });
  const saveOutcome = useMutation({
    mutationFn: (input: CreateApplicationOutcomeInput) => createApplicationOutcome(applicationId, input),
    onSuccess: async () => {
      setOutcomePending(null);
      writePending(applicationId, 'outcome', null);
      await refresh();
      message.success('已记录外部结果');
    },
    onError: (error, input) => {
      if (isUnknownWriteResult(error)) {
        const pending = { input, resultUnknown: true };
        setOutcomePending(pending);
        writePending(applicationId, 'outcome', pending);
        message.error('记录结果待确认，请使用原尝试重试');
        return;
      }
      setOutcomePending(null);
      writePending(applicationId, 'outcome', null);
      message.error('记录失败，请检查输入后重试');
    },
  });

  if (!application) return null;
  const snapshotInput = (values: SnapshotFormValues): CreateApplicationSubmissionSnapshotInput => ({
    resume_id: values.resumeId,
    jd_version_id: currentJd!.id,
    material_kit_id: values.includeMaterial ? (kit.data?.id ?? null) : null,
    submitted_at: values.submittedAt.toISOString(), note: values.note ?? '', idempotency_key: key(),
  });
  const outcomeInput = (values: OutcomeFormValues): CreateApplicationOutcomeInput => ({
    submission_snapshot_id: values.snapshotId, application_event_id: values.eventId ?? null,
    stage: values.stage, result: values.result, feedback_text: values.feedbackText ?? '',
    reflection_text: values.reflectionText ?? '', next_action_text: values.nextActionText ?? '',
    feedback_tags: values.feedbackTags ?? [], occurred_at: values.occurredAt.toISOString(), idempotency_key: key(),
  });

  const askPilotSnapshot = async () => {
    const values = await snapshotForm.validateFields();
    const input = snapshotInput(values);
    onAskPilot?.(application, { type: 'application_submission_snapshot', resumeId: input.resume_id, jdVersionId: input.jd_version_id, materialKitId: input.material_kit_id, submittedAt: input.submitted_at, note: input.note });
  };
  const askPilotOutcome = async () => {
    const values = await outcomeForm.validateFields();
    const input = outcomeInput(values);
    onAskPilot?.(application, { type: 'application_outcome_record', snapshotId: input.submission_snapshot_id, eventId: input.application_event_id, stage: input.stage, result: input.result, feedbackText: input.feedback_text, reflectionText: input.reflection_text, nextActionText: input.next_action_text, feedbackTags: input.feedback_tags, occurredAt: input.occurred_at });
  };

  const submitSnapshot = (values: SnapshotFormValues) => {
    const input = snapshotInput(values);
    setSnapshotPending({ input, resultUnknown: false });
    saveSnapshot.mutate(input);
  };
  const submitOutcome = (values: OutcomeFormValues) => {
    const input = outcomeInput(values);
    setOutcomePending({ input, resultUnknown: false });
    saveOutcome.mutate(input);
  };

  const loading = snapshots.isLoading || outcomes.isLoading || summary.isLoading;
  const writeInFlight = saveSnapshot.isPending || saveOutcome.isPending;
  return (
    <Drawer
      className={styles.drawer}
      open={open}
      onClose={() => { if (!writeInFlight) onClose(); }}
      width={980}
      title={null}
      destroyOnClose
      closable={!writeInFlight}
      maskClosable={!writeInFlight}
      keyboard={!writeInFlight}
    >
      <header className={styles.hero}>
        <p className={styles.eyebrow}>APPLICATION EVIDENCE ARCHIVE</p>
        <h2>{application.company_name} · 投递事实与结果</h2>
        <p>冻结面试官实际看到的材料，原样记录外部反馈，让每次准备都有可追溯的事实依据。</p>
      </header>
      <main className={styles.body}>
        <div className={styles.summaryGrid} aria-label="结果反馈摘要">
          <div className={styles.metric}><span>事实档案</span><strong>{snapshots.data?.length ?? 0}</strong></div>
          <div className={styles.metric}><span>结果记录</span><strong>{summary.data?.total ?? 0}</strong></div>
          <div className={styles.metric}><span>进入下一阶段</span><strong>{summary.data?.result_counts.advanced ?? 0}</strong></div>
          <div className={styles.metric}><span>含下一步行动</span><strong>{summary.data?.next_actions_pending ?? 0}</strong></div>
        </div>
        {Object.keys(summary.data?.feedback_tag_counts ?? {}).length > 0 ? (
          <div className={styles.patternBar} aria-label="已记录反馈模式">
            <span>已记录的反馈模式</span>
            {Object.entries(summary.data?.feedback_tag_counts ?? {})
              .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
              .map(([tag, count]) => <Tag key={tag}>{labels[tag] ?? tag} · {count}</Tag>)}
          </div>
        ) : null}
        {loading ? <Spin /> : null}

        <section className={styles.panel}>
          <div className={styles.panelHeader}>
            <div><h3><SafetyCertificateOutlined /> 冻结本次实际投递</h3><p>保存当时使用的版本；后续修改不会覆盖这份档案。</p></div>
            <Tag color="cyan">只读快照</Tag>
          </div>
          <Form form={snapshotForm} layout="vertical" disabled={snapshotPending?.resultUnknown} onFinish={submitSnapshot}>
            <div className={styles.formGrid}>
              <Form.Item name="resumeId" label="实际提交的简历" rules={[{ required: true, message: '请选择简历' }]}>
                <Select options={resumes.filter((item) => !item.deleted_at).map((item) => ({ value: item.id, label: item.title || item.name || `简历 #${item.id}` }))} />
              </Form.Item>
              <Form.Item label="岗位资料版本"><Input value={currentJd ? `v${currentJd.version_number} · ${currentJd.jd_text.slice(0, 42)}` : '尚未保存 JD'} disabled /></Form.Item>
              <Form.Item name="submittedAt" label="实际投递时间" rules={[{ required: true }]}><DatePicker showTime style={{ width: '100%' }} /></Form.Item>
              <Form.Item name="includeMaterial" label="确认材料"><Select options={[{ value: false, label: '仅冻结简历与 JD' }, { value: true, label: kit.data ? `同时冻结材料包 #${kit.data.id}` : '暂无可冻结材料包', disabled: !kit.data }]} /></Form.Item>
              <Form.Item className={styles.span2} name="note" label="投递备注"><Input.TextArea rows={2} placeholder="例如：官网投递，已补充作品集链接" /></Form.Item>
            </div>
            {!snapshotPending?.resultUnknown ? <div className={styles.actionBar}>
              {onAskPilot ? <Button className={styles.pilotButton} icon={<RobotOutlined />} onClick={() => void askPilotSnapshot()} disabled={!currentJd}>交给 Pilot 确认</Button> : null}
              <Button type="primary" htmlType="submit" loading={saveSnapshot.isPending} disabled={!currentJd}>直接保存档案</Button>
            </div> : null}
            {snapshotPending?.resultUnknown ? <Alert className={styles.unknownAlert} type="warning" showIcon message="保存结果待确认" description="输入已冻结；仅使用原幂等键重试，不会创建新的尝试。" /> : null}
          </Form>
          {snapshotPending?.resultUnknown ? <div className={styles.actionBar}>
            <Button type="primary" loading={saveSnapshot.isPending} onClick={() => saveSnapshot.mutate(snapshotPending.input)}>使用原尝试重试</Button>
          </div> : null}
        </section>

        {(snapshots.data?.length ?? 0) > 0 ? (
          <ApplicationCommunicationDraftPanel
            application={application}
            snapshots={snapshots.data ?? []}
            outcomes={outcomes.data ?? []}
            events={events}
          />
        ) : null}

        <section className={styles.panel}>
          <div className={styles.panelHeader}>
            <div><h3><AuditOutlined /> 记录外部结果</h3><p>原始反馈与个人复盘分开保存，不让系统替你解释结果。</p></div>
            <Tag color="gold">追加式历史</Tag>
          </div>
          {(snapshots.data?.length ?? 0) === 0 ? <Empty description="先冻结一份实际投递档案，再记录结果" /> : (
            <>
              <Form form={outcomeForm} layout="vertical" disabled={outcomePending?.resultUnknown} onFinish={submitOutcome}>
              <div className={styles.formGrid}>
                <Form.Item name="snapshotId" label="关联投递档案" rules={[{ required: true }]}><Select options={(snapshots.data ?? []).map((item) => ({ value: item.id, label: `${dayjs(item.submitted_at).format('YYYY-MM-DD')} · ${item.resume_title} · JD v${item.jd_version_number}` }))} /></Form.Item>
                <Form.Item name="eventId" label="关联日程（可选）"><Select allowClear options={events.map((item) => ({ value: item.id, label: `${dayjs(item.scheduled_at).format('MM-DD')} · ${EVENT_TYPE_LABELS[item.event_type]}` }))} /></Form.Item>
                <Form.Item name="stage" label="阶段" rules={[{ required: true }]}><Select options={STAGE_OPTIONS} /></Form.Item>
                <Form.Item name="result" label="结果" rules={[{ required: true }]}><Select options={RESULT_OPTIONS} /></Form.Item>
                <Form.Item name="occurredAt" label="发生时间" rules={[{ required: true }]}><DatePicker showTime style={{ width: '100%' }} /></Form.Item>
                <Form.Item name="feedbackTags" label="反馈标签"><Select mode="multiple" maxCount={8} options={TAG_OPTIONS} /></Form.Item>
                <Form.Item className={styles.span2} name="feedbackText" label="原始反馈"><Input.TextArea rows={3} placeholder="尽量原样记录 HR 或面试官的反馈" /></Form.Item>
                <Form.Item name="reflectionText" label="我的复盘"><Input.TextArea rows={3} placeholder="这是你的观点，不会被标成外部事实" /></Form.Item>
                <Form.Item name="nextActionText" label="下次怎么做"><Input.TextArea rows={3} placeholder="写一个可以执行的下一步" /></Form.Item>
              </div>
              {!outcomePending?.resultUnknown ? <div className={styles.actionBar}>
                {onAskPilot ? <Button className={styles.pilotButton} icon={<RobotOutlined />} onClick={() => void askPilotOutcome()}>交给 Pilot 确认</Button> : null}
                <Button type="primary" htmlType="submit" loading={saveOutcome.isPending}>直接记录结果</Button>
              </div> : null}
              {outcomePending?.resultUnknown ? <Alert className={styles.unknownAlert} type="warning" showIcon message="记录结果待确认" description="输入已冻结；仅使用原幂等键重试，不会创建新的尝试。" /> : null}
              </Form>
              {outcomePending?.resultUnknown ? <div className={styles.actionBar}>
                <Button type="primary" loading={saveOutcome.isPending} onClick={() => saveOutcome.mutate(outcomePending.input)}>使用原尝试重试</Button>
              </div> : null}
            </>
          )}
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}><div><h3>历史与来源风险</h3><p>冻结内容永不覆盖；来源变化只做提示。</p></div></div>
          <div className={styles.historyList}>
            {(snapshots.data ?? []).map((snapshot) => (
              <article className={styles.historyCard} key={`snapshot-${snapshot.id}`}>
                <div className={styles.historyTop}><span className={styles.historyTitle}>{dayjs(snapshot.submitted_at).format('YYYY-MM-DD HH:mm')} · {snapshot.resume_title}</span><Tag>{snapshot.source_kind === 'pilot' ? 'Pilot 确认' : 'UI 保存'}</Tag></div>
                <div className={styles.sourceRow}>{(['resume', 'jd', 'material'] as const).map((source) => <span key={source} className={`${styles.sourcePill} ${styles[snapshot.source_states[source]]}`}>{source === 'material' && snapshot.material_kit_id === null ? '材料 · 未包含' : sourceLabel(source, snapshot.source_states[source])}</span>)}</div>
                {snapshot.note ? <div className={styles.feedbackBlock}>{snapshot.note}</div> : null}
                <details className={styles.snapshotDetails}>
                  <summary>查看冻结内容</summary>
                  <div><b>简历快照</b><pre>{resumeSnapshotPreview(snapshot.resume_snapshot)}</pre></div>
                  <div><b>JD v{snapshot.jd_version_number}</b><pre>{preview(snapshot.jd_snapshot)}</pre></div>
                  {snapshot.material_snapshot ? <div><b>材料快照</b><pre>{preview(snapshot.material_snapshot)}</pre></div> : null}
                </details>
              </article>
            ))}
            {(outcomes.data ?? []).map((outcome) => (
              <article className={styles.historyCard} key={`outcome-${outcome.id}`}>
                <div className={styles.historyTop}><span className={styles.historyTitle}>{labels[outcome.stage]} · {labels[outcome.result]}</span><Tag color={outcome.source_kind === 'pilot' ? 'cyan' : 'blue'}>{outcome.source_kind === 'pilot' ? 'Pilot 确认' : 'UI 记录'}</Tag></div>
                <div className={styles.tagRow}>{outcome.feedback_tags.map((tag) => <Tag key={tag}>{labels[tag]}</Tag>)}</div>
                <div className={styles.feedbackBlock}>{outcome.feedback_text ? <div><b>原始反馈：</b>{outcome.feedback_text}</div> : null}{outcome.reflection_text ? <div><b>我的复盘：</b>{outcome.reflection_text}</div> : null}{outcome.next_action_text ? <div><b>下次行动：</b>{outcome.next_action_text}</div> : null}</div>
              </article>
            ))}
            {!loading && !(snapshots.data?.length || outcomes.data?.length) ? <Empty description="还没有投递事实档案" /> : null}
          </div>
        </section>
      </main>
    </Drawer>
  );
}
