import { t, useLocale } from '@/i18n';
import { Button, Empty, Space, Tag } from 'antd';
import {
  CalendarOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  ReadOutlined,
} from '@ant-design/icons';
import { STATUS_LABELS } from '@/types/application';
import type { Application } from '@/types/application';
import {
  READINESS_MATERIAL_STATUS_LABELS,
  READINESS_STATE_LABELS,
  type ApplicationReadiness,
} from '@/lib/missionControl';
import type { ViewMode } from '@/layout/navigation';
import styles from '../dashboard.module.css';

interface Props {
  application?: Application;
  readiness?: ApplicationReadiness;
  onOpenDetail: (applicationId: number) => void;
  onNavigate: (view: ViewMode) => void;
}

export default function FocusWorkspace({ application, readiness, onOpenDetail, onNavigate }: Props) {
  useLocale();
  if (!application || !readiness) {
    return (
      <aside className={styles.focusWorkspace} aria-label={t("当前焦点")}>
        <Empty description={t("选择一个投递，查看关联材料、日程和准备入口。")} />
      </aside>
    );
  }

  return (
    <aside className={styles.focusWorkspace} aria-labelledby="focus-workspace-title">
      <div className={styles.commandEyebrow}>{t("当前焦点")}</div>
      <h2 id="focus-workspace-title" className={styles.sectionHeading}>
        {application.company_name}
      </h2>
      <p className={styles.focusPosition}>{application.position_name}</p>

      <div className={styles.focusTags}>
        <Tag>{STATUS_LABELS[application.status]}</Tag>
        <Tag>{READINESS_STATE_LABELS[readiness.readiness]}</Tag>
        <Tag>{t("材料：")}{READINESS_MATERIAL_STATUS_LABELS[readiness.materialStatus]}</Tag>
      </div>

      <div className={styles.focusEvidence}>
        {readiness.evidence.map((item) => (
          <div key={item} className={styles.focusEvidenceRow}>
            {item}
          </div>
        ))}
      </div>

      <Space direction="vertical" className={styles.focusActions}>
        <Button icon={<FolderOpenOutlined />} onClick={() => onOpenDetail(application.id)} block>
          {t("打开投递详情")}</Button>
        <Button icon={<FileTextOutlined />} onClick={() => onOpenDetail(application.id)} block>
          {t("查看材料包")}</Button>
        <Button icon={<CalendarOutlined />} onClick={() => onNavigate('calendar')} block>
          {t("查看日程")}</Button>
        <Button icon={<ReadOutlined />} onClick={() => onNavigate('questions')} block>
          {t("练习题目")}</Button>
      </Space>
    </aside>
  );
}
