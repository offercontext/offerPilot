import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Pagination } from 'antd';
import { JOB_MAIL_SUGGESTIONS_KEY, listJobMailSuggestions } from '@/services/jobMail';
import type { ViewMode } from '@/layout/navigation';
import { mailActionLabel, mailStatusLabel } from './jobMailModel';
import JobMailImport from './JobMailImport';
import JobMailReview from './JobMailReview';
import styles from './jobMail.module.css';

interface Props { onNavigate: (view: ViewMode) => void; onOpenDetailById: (id: number) => void; }
export default function JobMailInbox({ onNavigate, onOpenDetailById }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [history, setHistory] = useState(false);
  const [page, setPage] = useState(1);
  const status = history ? 'processed' : 'unprocessed';
  const query = useQuery({ queryKey: [...JOB_MAIL_SUGGESTIONS_KEY, status, page], queryFn: () => listJobMailSuggestions({ status, limit: 20, offset: (page - 1) * 20 }), retry: false, refetchInterval: 30_000 });
  const shown = query.data?.items ?? [];
  return <section className={styles.panel} aria-labelledby="job-mail-inbox-title">
    <div className={styles.heading}>
      <div><h2 className={styles.title} id="job-mail-inbox-title">邮件待确认{query.data ? `（${query.data.pending_count}）` : ''}</h2><p className={styles.muted}>邮件建议尚未改变正式安排。打开查看不会视为确认。</p></div>
      <div className={styles.actions}><Button onClick={() => setImportOpen(true)}>粘贴邮件</Button><Button onClick={() => onNavigate('settings')}>管理求职邮箱</Button></div>
    </div>
    <div className={styles.actions}><Button type={history ? 'default' : 'primary'} onClick={() => { setHistory(false); setPage(1); }}>待核对</Button><Button type={history ? 'primary' : 'default'} onClick={() => { setHistory(true); setPage(1); }}>处理历史</Button><Button loading={query.isFetching} onClick={() => void query.refetch()}>刷新</Button></div>
    {query.isError ? <Alert type="error" showIcon message="邮件建议读取失败" description="这不代表没有新邮件或待确认安排。原有流程提醒仍可使用。" /> : query.isPending ? <p className={styles.muted}>正在读取邮件建议…</p> : shown.length === 0 ? <div className={styles.empty}>{history ? '暂无已处理的邮件建议。' : '暂无已生成的待核对建议。只有成功检查过的范围才被覆盖；可以在设置中立即同步，或粘贴单封邮件。'}</div> : <div className={styles.list}>
      {shown.map((item) => <button key={item.id} className={styles.item} type="button" onClick={() => setSelectedId(item.id)}>
        <div className={styles.heading}><span className={styles.subject}>{item.evidence?.subject || '无主题邮件'}</span><span className={styles.status}>{mailStatusLabel(item.status)}</span></div>
        <span className={styles.metadata}><span>{mailActionLabel(item.action)}</span><span>{item.application_candidates.length === 1 ? `${item.application_candidates[0].company_name} · ${item.application_candidates[0].position_name}（候选）` : item.application_candidates.length > 1 ? `${item.application_candidates.length} 个候选，需明确选择` : '待关联投递'}</span></span>
        <span className={styles.muted}>{item.reason}</span>
        <span className={styles.metadata}><span>接收：{item.evidence?.received_at ?? '原文不可用'}</span><span>时间：{item.proposed_fields.scheduled_at || '需核对原文／待补充'}</span><span>{item.proposed_fields.duration_minutes ? `时长：${item.proposed_fields.duration_minutes} 分钟` : '时长未确定'}</span></span>
      </button>)}
    </div>}
    {!!query.data?.total && query.data.total > 20 && <Pagination current={page} pageSize={20} total={query.data.total} showSizeChanger={false} onChange={setPage} />}
    {selectedId !== null && <JobMailReview suggestionId={selectedId} onClose={() => setSelectedId(null)} onOpenRecord={onOpenDetailById} onNavigate={onNavigate} />}
    {importOpen && <JobMailImport onClose={() => setImportOpen(false)} />}
  </section>;
}
