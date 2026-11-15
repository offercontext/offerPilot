import { t, useLocale } from '@/i18n';
import { CheckCircleOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { Button } from 'antd';
import type { OnboardingStatus } from '@/services/onboarding';
import type { OnboardingAction } from './actionRouting';
import styles from './OnboardingChecklist.module.css';

interface Props {
  status: OnboardingStatus;
  onCollapse: () => void;
  collapsing?: boolean;
  onAction: (action: OnboardingAction) => void;
}

const STEPS: Array<{ key: OnboardingAction; label: string }> = [
  { key: 'create_first_application', get label() { return t("添加第一条投递"); } },
  { key: 'create_primary_resume', get label() { return t("创建主简历"); } },
  { key: 'configure_ai', get label() { return t("配置 AI"); } },
  { key: 'send_first_pilot_message', get label() { return t("向 Pilot 发出第一条消息"); } },
];

export default function OnboardingChecklist({ status, onCollapse, onAction, collapsing }: Props) {
  useLocale();
  return (
    <section className={styles.card} aria-label={t("新手引导")}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>{t("从第一条投递开始")}</div>
          <div className={styles.subtitle}>{t("无需配置 AI 即可记录投递。简历和 AI 能力可按需添加。")}</div>
        </div>
        <div>
          <Button type="link" size="small" loading={collapsing} onClick={onCollapse}>
            {t("稍后再说")}</Button>
        </div>
      </div>
      <div className={styles.steps}>
        {STEPS.map((step) => {
          const completed = status.steps[step.key];
          return (
            <button
              key={step.key}
              type="button"
              data-onboarding-action={step.key}
              className={`${styles.step} ${completed ? styles.completed : ''}`}
              onClick={() => onAction(step.key)}
            >
              <span className={styles.stepIcon} aria-hidden="true">
                {completed ? <CheckCircleOutlined /> : <ClockCircleOutlined />}
              </span>
              <span className={styles.stepLabel}>{step.label}</span>
              {step.key !== 'create_first_application' && <span className={styles.subtitle}>{t("可选增强")}</span>}
            </button>
          );
        })}
      </div>
    </section>
  );
}
