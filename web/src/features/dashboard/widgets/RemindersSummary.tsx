import type { Reminder } from '@/lib/insights';
import styles from '../dashboard.module.css';

const DOT: Record<string, string> = {
  red: 'var(--op-error)',
  amber: 'var(--op-warning)',
  green: 'var(--op-success)',
};

interface Props {
  reminders: Reminder[];
  onSeeAll: () => void;
}

export default function RemindersSummary({ reminders, onSeeAll }: Props) {
  const top = reminders.slice(0, 4);
  return (
    <div className={styles.card}>
      <div
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}
      >
        <span className={styles.cardTitle} style={{ margin: 0 }}>待跟进</span>
        <a onClick={onSeeAll} style={{ fontSize: 14, cursor: 'pointer', color: 'var(--op-primary)' }}>
          全部 →
        </a>
      </div>
      {top.length === 0 ? (
        <div className={styles.empty}>暂无待办 ✦</div>
      ) : (
        top.map((r) => (
          <div key={r.id} style={{ display: 'flex', gap: 9, alignItems: 'center', marginBottom: 9 }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: DOT[r.severity], flexShrink: 0 }} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 14, color: 'var(--op-ink)' }}>{r.title}</div>
              <div style={{ fontSize: 12, color: 'var(--op-muted)' }}>{r.detail}</div>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
