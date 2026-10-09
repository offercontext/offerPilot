import { useQuery } from '@tanstack/react-query';
import { Alert, Button } from 'antd';
import { getJobMailPendingCount, JOB_MAIL_COUNT_KEY } from '@/services/jobMail';
import styles from './jobMail.module.css';

export default function JobMailPendingSummary({ onOpen }: { onOpen: () => void }) {
  const query = useQuery({ queryKey: JOB_MAIL_COUNT_KEY, queryFn: getJobMailPendingCount, retry: false, refetchInterval: 30_000 });
  if (query.isError) return <Alert type="warning" message="邮件待确认数量暂时无法读取" action={<Button onClick={onOpen}>查看邮件提醒</Button>} />;
  if (!query.data) return null;
  return <section className={styles.panel} aria-label="邮件待确认摘要"><div className={styles.heading}><div><h2 className={styles.title}>有 {query.data} 条邮件建议待核对</h2><p className={styles.muted}>需要确认或补充信息，尚未写入正式日程。</p></div><Button onClick={onOpen}>核对邮件建议</Button></div></section>;
}
