import { t, useLocale } from '@/i18n';
import { Card, Progress, Tag, Tooltip } from 'antd';
import { CopyOutlined, DeleteOutlined, EditOutlined, FileTextOutlined, StarOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import type { ReactNode } from 'react';
import type { Resume } from '@/types/resume';
import { formatResumeLineage, resumeDisplayTitle } from '@/features/materialSurfaces/materialLabels';
import { resolveResumeLineage } from '@/features/materialSurfaces/resumeLineage';
import { createPilotAttachmentDragBinding } from './PilotAttachmentHandle';

interface Props {
  resume: Resume;
  onEdit: (id: number) => void;
  onSetMaster: (id: number) => void;
  onCopy: (id: number) => void;
  onDelete: (id: number) => void;
  onCompare?: (id: number) => void;
  onAttachToPilot?: (attachment: import('@/types/chat').PilotContextAttachment) => void;
  resumes?: readonly Resume[];
}

const SOURCE_LABELS: Record<string, string> = {
  get manual() { return t("手动创建"); },
  get dialog() { return t("Pilot 对话"); },
  get upload() { return t("PDF 上传"); },
  get sample() { return t("样例开始"); },
  get sample_copy() { return t("样例副本"); },
};

const SECTION_LABELS: Record<string, string> = {
  get career_intent() { return t("求职意向"); },
  get contact() { return t("联系方式"); },
  get education() { return t("教育经历"); },
  get experience() { return t("工作经历"); },
  get projects() { return t("项目经历"); },
  get skills() { return t("技能清单"); },
};

export default function ResumeCard({ resume, onEdit, onSetMaster, onCopy, onDelete, onCompare, onAttachToPilot, resumes }: Props) {
  useLocale();
  const title = resumeDisplayTitle(resume);
  const sourceLabel = SOURCE_LABELS[resume.source] ?? t("来源待确认");
  const lineage = resolveResumeLineage(resumes ?? [resume], resume.id);
  const lineageLabel = formatResumeLineage(lineage);
  const isBase = lineage.kind === 'base';
  const canCreateLineage = lineage.kind !== 'relationship_unknown';
  const completion = Math.max(0, Math.min(100, resume.completion_percent ?? 0));
  const missing = (resume.missing_sections ?? []).map((item) => SECTION_LABELS[item] ?? item);
  const preview = sectionPreview(resume);
  const resumeDragBinding = onAttachToPilot
    ? createPilotAttachmentDragBinding({ kind: 'resume', id: String(resume.id), label: title })
    : undefined;

  return (
    <Card hoverable style={{ minWidth: 0 }} styles={{ body: { padding: 14 } }} {...resumeDragBinding}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', minWidth: 0, flex: '1 1 180px', gap: 6, flexWrap: 'wrap' }}>
          <Tag color={isBase ? 'blue' : lineage.kind === 'relationship_unknown' ? 'warning' : 'default'} style={{ borderRadius: 8, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' }} title={lineageLabel}>{lineageLabel}</Tag>
          <Tag color={sourceColor(resume.source)} style={{ borderRadius: 8 }}>{sourceLabel}</Tag>
        </div>
        <span style={{ fontSize: 11, color: 'var(--op-muted)', whiteSpace: 'nowrap' }}>
          {t("创建于 ")}{dayjs(resume.created_at).format('MM-DD HH:mm')}
        </span>
      </div>

      <div style={{ fontSize: 15, fontWeight: 600, margin: '10px 0 8px', color: 'var(--op-ink)', overflowWrap: 'anywhere', textWrap: 'pretty' as const }}>
        {title}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <Progress percent={completion} showInfo={false} size="small" strokeColor={completion >= 80 ? '#16a34a' : '#f59e0b'} style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: 'var(--op-text)', fontVariantNumeric: 'tabular-nums', minWidth: 34 }}>{completion}%</span>
      </div>

      <div style={{ fontSize: 12, color: 'var(--op-muted)', lineHeight: 1.5, minHeight: 36, overflow: 'hidden', overflowWrap: 'anywhere', textWrap: 'pretty' as const }}>
        {preview}
      </div>

      <div style={{ display: 'flex', gap: 6, minHeight: 24, marginTop: 10, flexWrap: 'wrap' }}>
        {resume.is_complete ? (
          <Tag color="success" style={{ borderRadius: 8 }}>{t("结构完整")}</Tag>
        ) : missing.length ? (
          missing.slice(0, 3).map((label) => <Tag key={label} style={{ borderRadius: 8 }}>{label}</Tag>)
        ) : (
          <Tag style={{ borderRadius: 8 }}>{t("待补全")}</Tag>
        )}
        {missing.length > 3 && <Tag style={{ borderRadius: 8 }}>+{missing.length - 3}</Tag>}
      </div>

      <div style={{ display: 'flex', marginTop: 12, borderTop: '1px solid var(--op-border)', paddingTop: 10 }}>
        <CardAction label={t("编辑")} icon={<EditOutlined />} onClick={() => onEdit(resume.id)} primary />
        {!isBase && (
          <>
            <VDivider />
            <CardAction
              label={t("设为基础简历")}
              icon={<StarOutlined />}
              onClick={() => onSetMaster(resume.id)}
              disabled={!canCreateLineage}
              disabledReason={!canCreateLineage ? t("关系待确认，暂不能设为基础简历") : undefined}
            />
          </>
        )}
        <VDivider />
        <CardAction
          label={t("复制")}
          icon={<CopyOutlined />}
          onClick={() => onCopy(resume.id)}
          disabled={!canCreateLineage}
          disabledReason={!canCreateLineage ? t("关系待确认，暂不能复制") : undefined}
        />
        {onCompare && (
          <>
            <VDivider />
            <CardAction label={t("对比版本")} icon={<FileTextOutlined />} onClick={() => onCompare(resume.id)} />
          </>
        )}
        <VDivider />
        <CardAction
          label={t("删除")}
          icon={<DeleteOutlined />}
          onClick={() => onDelete(resume.id)}
          disabled={isBase}
          disabledReason={isBase ? t("基础简历不可删除") : undefined}
        />
      </div>
    </Card>
  );
}

function VDivider() {
  useLocale();
  return <span style={{ width: 1, background: 'var(--op-border)' }} />;
}

function CardAction({
  label,
  icon,
  onClick,
  primary,
  disabled,
  disabledReason,
}: {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  primary?: boolean;
  disabled?: boolean;
  disabledReason?: string;
}) {
  useLocale();
  return (
    <Tooltip title={disabledReason ?? label}>
      <button
        onClick={() => {
          if (!disabled) onClick();
        }}
        aria-label={label}
        title={disabledReason}
        disabled={disabled}
        style={{
          flex: 1,
          background: 'transparent',
          border: 'none',
          cursor: disabled ? 'not-allowed' : 'pointer',
          padding: '4px 0',
          fontSize: 12,
          color: disabled ? '#cbd5e1' : primary ? 'var(--op-primary)' : 'var(--op-muted)',
          fontWeight: primary ? 600 : 400,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 4,
        }}
      >
        {icon}
        <span>{label}</span>
      </button>
    </Tooltip>
  );
}

function sourceColor(source: string) {
  if (source === 'dialog') return 'purple';
  if (source === 'upload') return 'gold';
  if (source === 'sample' || source === 'sample_copy') return 'green';
  return 'default';
}

function sectionPreview(resume: Resume) {
  const content = resume.content_json ?? {};
  const intent = content.career_intent;
  const roles = Array.isArray(intent?.target_roles) ? intent.target_roles.filter(Boolean).join(' / ') : '';
  if (roles) return t("目标：{roles}", { roles: roles });
  const raw = typeof content.raw_text === 'string' ? content.raw_text : resume.parsed_data;
  const summary = (raw || '').slice(0, 90).replace(/\s+/g, ' ').trim();
  return summary || t("等待补充结构化内容");
}
