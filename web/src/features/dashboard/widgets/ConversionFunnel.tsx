import type { FunnelStage } from '@/lib/insights';
import styles from '../dashboard.module.css';

const COLORS = ['#6366f1', '#7c6ff2', '#a855f7', '#059669'];

export default function ConversionFunnel({ stages }: { stages: FunnelStage[] }) {
  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>转化漏斗</div>
      {stages.map((s, i) => (
        <div key={s.key} className={styles.funnelRow}>
          <span className={styles.funnelLabel}>{s.label}</span>
          <div className={styles.funnelBarWrap}>
            <div
              className={styles.funnelBar}
              style={{ width: `${Math.max(s.ratio * 100, 3)}%`, background: COLORS[i] }}
            />
          </div>
          <span className={`${styles.funnelCount} op-tnum`} style={{ color: COLORS[i] }}>
            {s.count}
          </span>
        </div>
      ))}
    </div>
  );
}
