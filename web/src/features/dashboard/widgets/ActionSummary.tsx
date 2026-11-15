import { t, useLocale } from '@/i18n';
import type { ActionItemSummary } from '@/lib/actionItems';
import styles from '../dashboard.module.css';

interface Props {
  summary: ActionItemSummary;
}

export default function ActionSummary({ summary }: Props) {
  useLocale();
  const cards = [
    { key: 'p0', label: t("P0 紧急"), value: summary.p0, tone: 'danger' },
    { key: 'interview', label: t("72h 面试"), value: summary.interviewSoon, tone: 'warning' },
    { key: 'stale', label: t("停滞跟进"), value: summary.stale, tone: 'primary' },
    { key: 'questions', label: t("待复习题"), value: summary.dueQuestions, tone: 'success' },
  ] as const;

  return (
    <div className={styles.actionSummary}>
      {cards.map((card) => (
        <div key={card.key} className={`${styles.actionSummaryCard} ${styles[card.tone]}`}>
          <div className={styles.actionSummaryLabel}>{card.label}</div>
          <div className={`${styles.actionSummaryValue} op-tnum`}>{card.value}</div>
        </div>
      ))}
    </div>
  );
}
