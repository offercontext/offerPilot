import {
  BellOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Skeleton, Tag, Typography } from 'antd';
import { useId, useMemo, useState } from 'react';
import { listApplications } from '@/services/applications';
import { cancelProactiveJob, getProactiveSettings, listProactiveJobs } from '@/services/proactive';
import type { Application } from '@/types/application';
import {
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
  if (!Number.isFinite(timestamp)) return '时间未知';
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      dateStyle: 'medium',
      timeStyle: 'short',
      ...(timezone ? { timeZone: timezone } : {}),
    }).format(new Date(timestamp * 1000));
  } catch {
    // A stale client may know a timezone that this browser does not support.
    return new Intl.DateTimeFormat('zh-CN', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(timestamp * 1000));
  }
}

function applicationLabel(application: Application | undefined, applicationId: number): string {
  return application ? `${application.company_name} · ${application.position_name}` : `投递 #${applicationId}`;
}

function getErrorCopy(errorCode: string): string | null {
  if (!errorCode) return null;
  const copies: Record<string, string> = {
    user_cancelled: '你已取消这项任务。',
    source_changed: '来源已变化，任务结果已清除。',
    source_or_scope_changed: '投递范围或来源已变化，任务已停止。',
    scope_disabled: '主动任务开关或范围已关闭。',
    quiet_hours_started: '安静时段开始，任务已停止。',
    daily_reminder_budget: '已达到普通提醒每日上限。',
    attempt_limit: '多次尝试未完成，任务已停止。',
    runtime_result_requires_reconciliation: '本地服务未能确认模型结果，请检查后再决定是否重试。',
    generation_failed: '本地服务生成失败，可以稍后重试。',
    invalid_draft_output: '生成结果不符合草稿格式，未展示内容。',
  };
  return copies[errorCode] ?? `任务状态：${errorCode}`;
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
          <ClockCircleOutlined aria-hidden="true" /> 计划处理时间：{formatProactiveTimestamp(job.due_at, timezone)}
        </span>
        <span className={styles.jobMetaItem}>创建：{formatProactiveTimestamp(job.created_at, timezone)}</span>
      </div>

      {hasResult ? (
        <div className={styles.result}>
          <div className={styles.resultLabel}>
            {job.kind === 'interview_draft' ? <FileTextOutlined aria-hidden="true" /> : <BellOutlined aria-hidden="true" />}
            {job.kind === 'interview_draft' ? '自动准备草稿 · 未发送' : '主动提醒内容'}
          </div>
          {job.result_text}
          {job.kind === 'interview_draft' ? <div className={styles.resultNotice}>仅供你查看，不会自动执行业务操作。</div> : null}
        </div>
      ) : null}

      {errorCopy ? <div className={styles.jobReason}>{errorCopy}</div> : !hasResult ? <div className={styles.jobReason}>{job.reason}</div> : null}

      {active ? (
        <div className={styles.jobFooter}>
          <span className={styles.jobReason}>可以随时取消，取消后不会继续生成。</span>
          <Button danger ghost icon={<StopOutlined />} loading={cancelling} onClick={() => onCancel(job)}>
            取消任务
          </Button>
        </div>
      ) : null}
    </article>
  );
}

export default function ProactiveInbox() {
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
  const [error, setError] = useState('');

  const applicationsById = useMemo(
    () => new Map((applicationsQuery.data ?? []).map((application) => [application.id, application])),
    [applicationsQuery.data],
  );

  async function cancel(job: ProactiveJob) {
    if (cancellingId) return;
    setCancellingId(job.id);
    setError('');
    try {
      await cancelProactiveJob(job.id);
      await queryClient.invalidateQueries({ queryKey: PROACTIVE_JOBS_QUERY_KEY });
    } catch {
      setError('任务取消失败，请确认本地服务仍在运行后重试。');
    } finally {
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
          message="主动任务读取失败"
          description="请确认本地服务已运行，然后重试。"
          action={<Button onClick={() => void jobsQuery.refetch()}>重试</Button>}
        />
      </section>
    );
  }

  const jobs = jobsQuery.data ?? [];

  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.header}>
        <div className={styles.heading}>
          <span className={styles.headingIcon} aria-hidden="true"><BellOutlined /></span>
          <div>
            <Typography.Title id={titleId} level={4} className={styles.headingTitle}>
              主动任务
            </Typography.Title>
            <p className={styles.headingCopy}>查看本地服务生成的提醒和准备结果，任务状态每 15 秒更新。</p>
          </div>
        </div>
        <Button
          className={styles.refreshButton}
          icon={<ReloadOutlined />}
          loading={jobsQuery.isFetching}
          onClick={() => void jobsQuery.refetch()}
          aria-label="刷新主动任务"
        >
          刷新
        </Button>
      </div>

      {error ? <Alert className={styles.error} type="error" showIcon message={error} /> : null}

      {jobs.length === 0 ? (
        <div className={styles.empty}>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有主动任务" />
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0, textAlign: 'center' }}>
            在设置中开启主动任务并选择投递后，提醒和准备草稿会出现在这里。
          </Typography.Paragraph>
        </div>
      ) : (
        <div className={styles.list} aria-label="主动任务列表">
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
    </section>
  );
}
