import { useQuery } from '@tanstack/react-query';
import { Alert, Button } from 'antd';
import { t, useLocale } from '@/i18n';
import { listProactiveJobs } from '@/services/proactive';
import { isApplicationProactiveJob, isProactiveJobActive, type ProactiveJob } from '@/types/proactive';
import JobMailPendingSummary from '@/features/jobMail/JobMailPendingSummary';
import styles from './dashboard.module.css';

export function summarizeTodayJobs(jobs: readonly ProactiveJob[]) {
  const applicationJobs = jobs.filter(isApplicationProactiveJob);
  return {
    active: applicationJobs.filter((job) => isProactiveJobActive(job.state)).length,
    attention: applicationJobs.filter((job) => job.state === 'failed' || job.state === 'result_unknown').length,
    drafts: applicationJobs.filter((job) => job.kind === 'interview_draft' && job.state === 'succeeded' && job.result_text.trim()).length,
  };
}

/** A destination summary, not another place to approve or rerun tasks. */
export default function TodayInboxSummary({ onOpen }: { onOpen: () => void }) {
  useLocale();
  const query = useQuery({ queryKey: ['proactive-jobs'], queryFn: listProactiveJobs, retry: false, refetchInterval: 15000 });
  const summary = summarizeTodayJobs(query.isError ? [] : query.data ?? []);
  return <section className={styles.card} aria-label={t('今日待办')}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
      <div><h2>{t('今日待办')}</h2><p>{t('提醒、邮件待审和主动任务结果，在同一个地方处理。')}</p></div>
      <Button onClick={onOpen}>{t('打开今日待办')}</Button>
    </div>
    {query.isError ? <Alert type="warning" showIcon message={t('主动任务状态暂时无法读取，请到今日待办重试。')} />
      : query.isPending ? <p role="status">{t('正在读取任务状态')}</p>
        : <p>{t('进行中 {active} · 需要检查 {attention} · 准备草稿 {drafts}', summary)}</p>}
    <JobMailPendingSummary onOpen={onOpen} />
  </section>;
}
