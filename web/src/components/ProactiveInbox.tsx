import { t, useLocale, getLocale } from '@/i18n';
import {
  BellOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Skeleton, Tag, Typography } from 'antd';
import { useId, useMemo, useRef, useState } from 'react';
import { listApplications } from '@/services/applications';
import { cancelProactiveJob, getProactiveSettings, listProactiveJobs } from '@/services/proactive';
import type { Application } from '@/types/application';
import {
  isApplicationProactiveJob,
  isProactiveJobActive,
  PROACTIVE_KIND_LABELS,
  PROACTIVE_STATE_LABELS,
  type ProactiveJob,
  type ProactiveJobState,
} from '@/types/proactive';
import styles from './ProactiveInbox.module.css';

const PROACTIVE_JOBS_QUERY_KEY = ['proactive-jobs'];
const APPLICATIONS_QUERY_KEY = ['applications'];

const STATE_COLORS: Record<ProactiveJobState, string> = {
  pending: 'processing',
  leased: 'processing',
  running: 'processing',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'default',
  result_unknown: 'warning',
};

/** The API returns UTC Unix seconds. Keep this conversion in one place. */
export function formatProactiveTimestamp(timestamp: number, timezone?: string): string {
  if (!Number.isFinite(timestamp)) return t("时间未知");
  try {
    return new Intl.DateTimeFormat(getLocale(), {
      dateStyle: 'medium',
      timeStyle: 'short',
      ...(timezone ? { timeZone: timezone } : {}),
    }).format(new Date(timestamp * 1000));
  } catch {
    // A stale client may know a timezone that this browser does not support.
    return new Intl.DateTimeFormat(getLocale(), {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(timestamp * 1000));
  }
}

function applicationLabel(application: Application | undefined, applicationId: number): string {
  return application ? `${application.company_name} · ${application.position_name}` : t("投递 #{value0}", { value0: applicationId });
}

function getErrorCopy(errorCode: string): string | null {
  if (!errorCode) return null;
  const copies: Record<string, string> = {
    user_cancelled: t("你已取消这项任务。"),
    source_changed: t("来源已变化，任务结果已清除。"),
    source_or_scope_changed: t("投递范围或来源已变化，任务已停止。"),
    scope_disabled: t("主动任务开关或范围已关闭。"),
    quiet_hours_started: t("安静时段开始，任务已停止。"),
    daily_reminder_budget: t("已达到普通提醒每日上限。"),
    attempt_limit: t("多次尝试未完成，任务已停止。"),
    runtime_result_requires_reconciliation: t("本地服务未能确认模型结果，请检查后再决定是否重试。"),
    generation_failed: t("本地服务生成失败，可以稍后重试。"),
    invalid_draft_output: t("生成结果不符合草稿格式，未展示内容。"),
  };
  return copies[errorCode] ?? t("任务状态：{value0}", { value0: errorCode });
}

function JobCard({
  job,
  application,
  timezone,
  cancelling,
  onCancel,
}: {
  job: ProactiveJob;
  application?: Application;
  timezone?: string;
  cancelling: boolean;
  onCancel: (job: ProactiveJob) => void;
}) {
  useLocale();
  const active = isProactiveJobActive(job.state);
  const hasResult = job.state === 'succeeded' && Boolean(job.result_text.trim());
  const errorCopy = getErrorCopy(job.error_code);

  return (
    <article className={styles.job} aria-label={`${PROACTIVE_KIND_LABELS[job.kind]}：${applicationLabel(application, job.application_id)}`}>
      <div className={styles.jobHeader}>
        <div className={styles.jobTitle}>
          <span className={styles.jobKind}>{PROACTIVE_KIND_LABELS[job.kind]}</span>
          <Tag color={STATE_COLORS[job.state]}>{PROACTIVE_STATE_LABELS[job.state]}</Tag>
        </div>
        <span className={styles.jobApplication} title={applicationLabel(application, job.application_id)}>
          {applicationLabel(application, job.application_id)}
        </span>
      </div>

      <div className={styles.jobMeta}>
        <span className={styles.jobMetaItem}>
          <ClockCircleOutlined aria-hidden="true" /> {t("计划处理时间：")}{formatProactiveTimestamp(job.due_at, timezone)}
        </span>
        <span className={styles.jobMetaItem}>{t("创建：")}{formatProactiveTimestamp(job.created_at, timezone)}</span>
      </div>

      {hasResult ? (
        <div className={styles.result}>
          <div className={styles.resultLabel}>
            {job.kind === 'interview_draft' ? <FileTextOutlined aria-hidden="true" /> : <BellOutlined aria-hidden="true" />}
            {job.kind === 'interview_draft' ? t("自动准备草稿 · 未发送") : t("主动提醒内容")}
          </div>
          {job.result_text}
          {job.kind === 'interview_draft' ? <div className={styles.resultNotice}>{t("仅供你查看，不会自动执行业务操作。")}</div> : null}
        </div>
      ) : null}

      {errorCopy ? <div className={styles.jobReason}>{errorCopy}</div> : !hasResult ? <div className={styles.jobReason}>{job.reason}</div> : null}

      {active ? (
        <div className={styles.jobFooter}>
          <span className={styles.jobReason}>{t("可以随时取消，取消后不会继续生成。")}</span>
          <Button danger ghost icon={<StopOutlined />} loading={cancelling} onClick={() => onCancel(job)}>
            {t("取消任务")}</Button>
        </div>
      ) : null}
    </article>
  );
}

export function isRepresentedReminder(job: ProactiveJob, candidateIds: readonly string[]): boolean {
  return isApplicationProactiveJob(job) && job.state === 'succeeded' && job.kind !== 'interview_draft'
    && Boolean(job.candidate_id && candidateIds.includes(job.candidate_id));
}

export function partitionProactiveJobs(allJobs: readonly ProactiveJob[], candidateIds: readonly string[]) {
  const applicationJobs = allJobs.filter(isApplicationProactiveJob);
  const isPublishedReminder = (job: ProactiveJob) => job.state === 'succeeded' && job.kind !== 'interview_draft';
  return {
    current: applicationJobs.filter((job) => !isPublishedReminder(job)),
    history: applicationJobs.filter((job) => isPublishedReminder(job) && !isRepresentedReminder(job, candidateIds)),
  };
}

export default function ProactiveInbox({ representedCandidateIds = [] }: { representedCandidateIds?: readonly string[] }) {
  useLocale();
  const queryClient = useQueryClient();
  const titleId = useId();
  const jobsQuery = useQuery({
    queryKey: PROACTIVE_JOBS_QUERY_KEY,
    queryFn: listProactiveJobs,
    refetchInterval: 15000,
  });
  const applicationsQuery = useQuery({
    queryKey: APPLICATIONS_QUERY_KEY,
    queryFn: () => listApplications(),
  });
  const settingsQuery = useQuery({
    queryKey: ['proactive-settings'],
    queryFn: getProactiveSettings,
  });
  const [cancellingId, setCancellingId] = useState<string>();
  const cancellationPending = useRef(false);
  const [error, setError] = useState('');

  const applicationsById = useMemo(
    () => new Map((applicationsQuery.data ?? []).map((application) => [application.id, application])),
    [applicationsQuery.data],
  );

  async function cancel(job: ProactiveJob) {
    if (cancellationPending.current) return;
    cancellationPending.current = true;
    setCancellingId(job.id);
    setError('');
    try {
      await cancelProactiveJob(job.id);
      await queryClient.invalidateQueries({ queryKey: PROACTIVE_JOBS_QUERY_KEY });
    } catch {
      setError("任务取消失败，请确认本地服务仍在运行后重试。");
    } finally {
      cancellationPending.current = false;
      setCancellingId(undefined);
    }
  }

  if (jobsQuery.isPending) {
    return (
      <section className={styles.panel} aria-labelledby={titleId}>
        <Skeleton active paragraph={{ rows: 7 }} />
      </section>
    );
  }

  if (jobsQuery.isError) {
    return (
      <section className={styles.panel} aria-labelledby={titleId}>
        <Alert
          type="error"
          showIcon
          message={t("主动任务读取失败")}
          description={t("请确认本地服务已运行，然后重试。")}
          action={<Button onClick={() => void jobsQuery.refetch()}>{t("重试")}</Button>}
        />
      </section>
    );
  }

  const allJobs = (jobsQuery.data ?? []).filter(isApplicationProactiveJob);
  const { current: jobs, history } = partitionProactiveJobs(allJobs, representedCandidateIds);

  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.header}>
        <div className={styles.heading}>
          <span className={styles.headingIcon} aria-hidden="true"><BellOutlined /></span>
          <div>
            <Typography.Title id={titleId} level={4} className={styles.headingTitle}>
              {t("主动任务")}</Typography.Title>
            <p className={styles.headingCopy}>{t("查看本地服务生成的提醒和准备结果，任务状态每 15 秒更新。")}</p>
          </div>
        </div>
        <Button
          className={styles.refreshButton}
          icon={<ReloadOutlined />}
          loading={jobsQuery.isFetching}
          onClick={() => void jobsQuery.refetch()}
          aria-label={t("刷新主动任务")}
        >
          {t("刷新")}</Button>
      </div>

      {error ? <Alert className={styles.error} type="error" showIcon message={t(error)} /> : null}

      {jobs.length === 0 ? (
        <div className={styles.empty}>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={allJobs.length > 0
            ? t("相关提醒已合并到待办列表") : t("还没有主动任务")} />
          {allJobs.length === 0 && <Typography.Paragraph type="secondary" style={{ marginBottom: 0, textAlign: 'center' }}>
            {t("在设置中开启主动任务并选择投递后，提醒和准备草稿会出现在这里。")}</Typography.Paragraph>}
        </div>
      ) : (
        <div className={styles.list} aria-label={t("主动任务列表")}>
          {jobs.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              application={applicationsById.get(job.application_id)}
              timezone={settingsQuery.data?.settings.timezone}
              cancelling={cancellingId === job.id}
              onCancel={(selected) => void cancel(selected)}
            />
          ))}
        </div>
      )}
      {history.length > 0 && <details>
        <summary>{t('已发布提醒历史（{count}）', { count: history.length })}</summary>
        <p>{t('以下是过去发布的内容，不代表当前仍需处理；当前提醒以待办列表为准。')}</p>
        <div className={styles.list} aria-label={t('历史提醒列表')}>
          {history.map((job) => <JobCard key={job.id} job={job}
            application={applicationsById.get(job.application_id)} timezone={settingsQuery.data?.settings.timezone}
            cancelling={false} onCancel={() => undefined} />)}
        </div>
      </details>}
    </section>
  );
}
