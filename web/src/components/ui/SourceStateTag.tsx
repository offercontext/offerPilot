import { t, useLocale } from '@/i18n';
import styles from './SourceStateTag.module.css';

export type SourceState = 'current' | 'frozen' | 'changed' | 'unknown' | 'pending';

export interface SourceStateTagProps {
  state: SourceState;
  detail?: string;
}

const labels: Record<SourceState, string> = {
  get current() { return t("当前使用来源"); },
  get frozen() { return t("已保留当时版本"); },
  get changed() { return t("来源已更新，已有结果仍保留"); },
  get unknown() { return t("来源暂不可确认"); },
  get pending() { return t("待确认的证据预览"); },
};

export function SourceStateTag({ state, detail }: SourceStateTagProps) {
  useLocale();
  return (
    <span className={`${styles.tag} ${styles[state]}`}>
      <span className={styles.dot} aria-hidden="true" />
      <span>{labels[state]}</span>
      {detail ? <span className={styles.detail}>{detail}</span> : null}
    </span>
  );
}
