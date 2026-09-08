import type { MomentumBucket } from '@/lib/insights';
import styles from '../dashboard.module.css';

export default function MomentumChart({ buckets }: { buckets: MomentumBucket[] }) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>近 4 周动量</div>
      <div className={styles.bars}>
        {buckets.map((b) => (
          <div key={b.label} style={{ flex: 1 }}>
            <div className={styles.bar} style={{ height: `${(b.count / max) * 100}%` }} title={`${b.count} 个`} />
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        {buckets.map((b) => (
          <div key={b.label} className={styles.barLabel} style={{ flex: 1 }}>
            {b.label}
          </div>
        ))}
      </div>
    </div>
  );
}
