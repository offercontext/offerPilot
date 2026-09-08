import { formatPipelineHealthLabel, type PipelineHealth } from '@/lib/pipelineInsights';
import styles from '../dashboard.module.css';

interface Props {
  health: PipelineHealth;
}

function rhythmCopy(health: PipelineHealth): string {
  if (health.label === 'critical') return '优先处理高风险推荐，再安排投递和复盘。';
  if (health.label === 'watch') return '当前有需要关注的风险，建议把最靠前的推荐先推进。';
  return '当前节奏稳定，继续保持投递、复盘和练习的闭环。';
}

export default function WeeklyRhythm({ health }: Props) {
  return (
    <div className={styles.rhythmCard}>
      <div className={styles.cardTitle}>本周节奏</div>
      <div className={styles.rhythmTrack} aria-hidden="true">
        <div className={styles.rhythmBar} style={{ width: `${health.score}%` }} />
      </div>
      <div className={styles.rhythmMeta}>
        <span className="op-tnum">
          本周 {health.weeklyApplications}/{health.weeklyTarget} 个投递
        </span>
        <span>{formatPipelineHealthLabel(health.label)}</span>
      </div>
      <p className={styles.rhythmText}>{rhythmCopy(health)}</p>
    </div>
  );
}
