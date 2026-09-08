import type { Kpis } from '@/lib/insights';
import styles from '../dashboard.module.css';

export default function KpiCards({ kpis }: { kpis: Kpis }) {
  const cards = [
    { label: '总投递', value: kpis.total, hint: kpis.weeklyDelta > 0 ? `↑ 本周 +${kpis.weeklyDelta}` : '本周无新增', up: kpis.weeklyDelta > 0 },
    { label: '面试中', value: kpis.interviewing, hint: '进行中', up: false },
    { label: 'Offer', value: kpis.offers, hint: kpis.offers > 0 ? '已到手' : '继续冲', up: kpis.offers > 0 },
    { label: '响应率', value: `${Math.round(kpis.responseRate * 100)}%`, hint: '收到回音占比', up: false },
  ];
  return (
    <div className={styles.kpiRow}>
      {cards.map((c) => (
        <div key={c.label} className={styles.card}>
          <div className={styles.kpiLabel}>{c.label}</div>
          <div className={`${styles.kpiValue} op-tnum`}>{c.value}</div>
          <div className={`${styles.kpiHint} ${c.up ? styles.up : styles.muted}`}>{c.hint}</div>
        </div>
      ))}
    </div>
  );
}
