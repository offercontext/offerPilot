import { Button } from 'antd';
import type { PipelineInsight } from '@/lib/pipelineInsights';
import styles from '../dashboard.module.css';

interface Props {
  items: PipelineInsight[];
  onAction: (item: PipelineInsight) => void;
  onAddApplication: () => void;
  onOpenQuestions: () => void;
  onSeeAll: () => void;
}

const PRIORITY_LABEL: Record<PipelineInsight['priority'], string> = {
  p0: 'P0',
  p1: 'P1',
  p2: 'P2',
};

export default function ActionQueue({
  items,
  onAction,
  onAddApplication,
  onOpenQuestions,
  onSeeAll,
}: Props) {
  const topItems = items.slice(0, 5);

  if (topItems.length === 0) {
    return (
      <div className={styles.actionEmpty}>
        <div>
          <div className={styles.actionEmptyTitle}>今天没有紧急事项</div>
          <div className={styles.actionEmptyText}>当前节奏稳定，可以继续补充投递、刷题或整理复盘。</div>
        </div>
        <div className={styles.actionEmptyButtons}>
          <Button type="primary" onClick={onAddApplication}>添加投递</Button>
          <Button onClick={onOpenQuestions}>打开题库</Button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.actionQueue}>
      {topItems.map((item, index) => (
        <button
          key={item.id}
          type="button"
          className={`${styles.actionItem} ${styles[item.priority]}`}
          style={{ animationDelay: `${index * 45}ms` }}
          onClick={() => onAction(item)}
        >
          <span className={styles.actionPriority}>{PRIORITY_LABEL[item.priority]}</span>
          <span className={styles.actionBody}>
            <span className={styles.actionTitle}>{item.title}</span>
            <span className={styles.actionDetail}>{item.reason}</span>
          </span>
          <span className={styles.actionButtonText}>{item.primaryAction.label}</span>
        </button>
      ))}
      {items.length > topItems.length && (
        <Button className={styles.seeAllActions} onClick={onSeeAll}>
          查看全部 {items.length} 个行动
        </Button>
      )}
    </div>
  );
}
