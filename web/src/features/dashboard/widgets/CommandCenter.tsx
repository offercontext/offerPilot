import { Button } from 'antd';
import { formatPipelineHealthLabel, type PipelineHealth, type PipelineInsight } from '@/lib/pipelineInsights';
import ActionQueue from './ActionQueue';
import WeeklyRhythm from './WeeklyRhythm';
import styles from '../dashboard.module.css';

interface Props {
  items: PipelineInsight[];
  health: PipelineHealth;
  onAction: (item: PipelineInsight) => void;
  onAddApplication: () => void;
  onOpenQuestions: () => void;
  onSeeAll: () => void;
}

export default function CommandCenter({
  items,
  health,
  onAction,
  onAddApplication,
  onOpenQuestions,
  onSeeAll,
}: Props) {
  return (
    <section className={styles.commandCenter} aria-labelledby="command-center-title">
      <div className={styles.commandMain}>
        <div className={styles.commandHeader}>
          <div>
            <div className={styles.commandEyebrow}>流程智能提醒</div>
            <h2 id="command-center-title" className={styles.commandTitle}>
              今天有 {items.length} 个推荐行动
            </h2>
            <p className={styles.commandSubtitle}>
              按截止时间、流程风险和准备收益排序，每条提醒都会说明为什么现在值得处理。
            </p>
          </div>
          <Button onClick={onSeeAll}>查看全部提醒</Button>
        </div>
        <div className={styles.pipelineHealthStrip}>
          <div>
            <div className={styles.healthLabel}>健康分</div>
            <div className={`${styles.healthText} op-tnum`}>{health.score}</div>
          </div>
          <div>
            <div className={styles.healthLabel}>状态</div>
            <div className={styles.healthText}>{formatPipelineHealthLabel(health.label)}</div>
          </div>
          <div>
            <div className={styles.healthLabel}>瓶颈</div>
            <div className={styles.healthText}>{health.bottleneck}</div>
          </div>
        </div>
        <ActionQueue
          items={items}
          onAction={onAction}
          onAddApplication={onAddApplication}
          onOpenQuestions={onOpenQuestions}
          onSeeAll={onSeeAll}
        />
      </div>
      <aside className={styles.commandAside}>
        <WeeklyRhythm health={health} />
        <div className={styles.quickCard}>
          <div className={styles.cardTitle}>快速入口</div>
          <div className={styles.quickGrid}>
            <Button onClick={onAddApplication}>添加投递</Button>
            <Button onClick={onOpenQuestions}>打开题库</Button>
          </div>
        </div>
      </aside>
    </section>
  );
}
