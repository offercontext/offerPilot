import { Tag } from 'antd';
import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import {
  READINESS_MATERIAL_STATUS_LABELS,
  READINESS_STATE_LABELS,
  type ApplicationReadiness,
  type ApplicationReadinessState,
} from '@/lib/missionControl';
import styles from '../dashboard.module.css';

interface Props {
  items: ApplicationReadiness[];
  focusApplicationId?: number;
  onFocus: (applicationId: number) => void;
}

const STATE_ICONS: Record<ApplicationReadinessState, ReactNode> = {
  ready: <CheckCircleOutlined />,
  watch: <WarningOutlined />,
  blocked: <ExclamationCircleOutlined />,
};

export default function ApplicationReadinessStrip({ items, focusApplicationId, onFocus }: Props) {
  if (items.length === 0) {
    return (
      <section className={styles.readinessStrip} aria-label="投递准备度">
        <div className={styles.readinessEmpty}>暂无活跃投递。添加投递后，这里会显示材料、日程和准备状态。</div>
      </section>
    );
  }

  return (
    <section className={styles.readinessStrip} aria-label="投递准备度">
      <div className={styles.sectionHeaderLine}>
        <div>
          <div className={styles.commandEyebrow}>准备度</div>
          <h2 className={styles.sectionHeading}>重点投递准备度</h2>
        </div>
      </div>
      <div className={styles.readinessList}>
        {items.slice(0, 6).map((item) => (
          <button
            key={item.applicationId}
            type="button"
            className={`${styles.readinessCard} ${styles[`readiness-${item.readiness}`]} ${
              focusApplicationId === item.applicationId ? styles.readinessActive : ''
            }`}
            onClick={() => onFocus(item.applicationId)}
          >
            <span className={styles.readinessIcon} aria-hidden="true">
              {STATE_ICONS[item.readiness]}
            </span>
            <span className={styles.readinessBody}>
              <span className={styles.readinessTitle}>{item.companyName}</span>
              <span className={styles.readinessPosition}>{item.positionName}</span>
              <span className={styles.readinessEvidence}>{item.evidence[0] ?? '暂无准备风险'}</span>
            </span>
            <span className={styles.readinessTags}>
              <Tag>{READINESS_STATE_LABELS[item.readiness]}</Tag>
              <Tag>{READINESS_MATERIAL_STATUS_LABELS[item.materialStatus]}</Tag>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
