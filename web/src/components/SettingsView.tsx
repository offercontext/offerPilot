import CompanionPersonaSettings from '@/features/companionPersona/CompanionPersonaSettings';
import CompanionLifeSettings from '@/features/companionLife/CompanionLifeSettings';
import { liveText } from '@/i18n/liveText';
import { localizedModal } from '@/i18n/modal';
import { t, useLocale, getLocale, setLocale } from '@/i18n';
import JobMailSettings from '@/features/jobMail/JobMailSettings';
import ContextPolicySettings from './ContextPolicySettings';
import ConfirmedMemorySettings from './ConfirmedMemorySettings';
import ProactiveSettings from './ProactiveSettings';
import DesktopUpdatesCard from './DesktopUpdatesCard';
import { ApiOutlined, CopyOutlined, DownloadOutlined, FileSearchOutlined, ReloadOutlined, RobotOutlined, SettingOutlined } from '@ant-design/icons';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Divider, Empty, Input, Pagination, Select, Skeleton, Space, Spin, Switch, Tag, Typography, message } from 'antd';
import { exportBackup, getLogs, getSettings, getSettingsBackup, type LogEntry, type LogsPage, type Settings } from '@/services/chat';
import { ONBOARDING_QUERY_KEY, setOnboardingForceOpen } from '@/services/onboarding';
import { buildDiagnosticsText } from '@/lib/diagnostics';
import type { PilotMascotAnimationLevel } from '@/features/pilotMascot/pilotMascotPreference';
import { lazy, Suspense, useEffect, useMemo, useState } from 'react';

const AISettingsDrawer = lazy(() => import('./AISettingsDrawer'));
const OfflineWhisperModelCard = lazy(() => import('@/features/mockInterviewVoice/OfflineWhisperModelCard'));

interface Props {
  aiSettingsRequest?: number;
  pilotMascotVisible: boolean;
  onPilotMascotVisibleChange: (visible: boolean) => void;
  pilotMascotZoom?: number;
  onPilotMascotZoomChange?: (zoom: number) => void;
  pilotMascotAnimationLevel?: PilotMascotAnimationLevel;
  onPilotMascotAnimationLevelChange?: (level: PilotMascotAnimationLevel) => void;
  onPilotMascotResetPosition?: () => void;
  systemReducedMotion?: boolean;
}

const LOG_PAGE_SIZE = 20;

export default function SettingsView({
  aiSettingsRequest = 0,
  pilotMascotVisible,
  onPilotMascotVisibleChange,
  pilotMascotZoom = 1,
  onPilotMascotZoomChange = () => undefined,
  pilotMascotAnimationLevel = 'full',
  onPilotMascotAnimationLevelChange = () => undefined,
  onPilotMascotResetPosition = () => undefined,
  systemReducedMotion = false,
}: Props) {
  const locale = useLocale();
  const queryClient = useQueryClient();
  const [localeSaving, setLocaleSaving] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [voiceOpened, setVoiceOpened] = useState(false);
  const [logLevel, setLogLevel] = useState('');
  const [logPage, setLogPage] = useState(1);
  const [lastLogsPage, setLastLogsPage] = useState<LogsPage>();
  const [aiSettingsOpen, setAISettingsOpen] = useState(false);
  useEffect(() => { if (aiSettingsRequest > 0) setAISettingsOpen(true); }, [aiSettingsRequest]);
  const logOffset = (logPage - 1) * LOG_PAGE_SIZE;
  const settingsQuery = useQuery({
    queryKey: ['settings-summary'],
    queryFn: getSettings,
  });
  const logsQuery = useQuery({
    queryKey: ['runtime-logs', LOG_PAGE_SIZE, logOffset, logLevel],
    queryFn: () => getLogs(LOG_PAGE_SIZE, logOffset, logLevel),
    placeholderData: keepPreviousData,
    enabled: diagnosticsOpen && !aiSettingsOpen,
    refetchInterval: diagnosticsOpen && !aiSettingsOpen && logPage === 1 ? 15000 : false,
  });
  const reopenOnboardingMutation = useMutation({
    mutationFn: () => setOnboardingForceOpen(true),
    onSuccess: (status) => {
      queryClient.setQueryData(ONBOARDING_QUERY_KEY, status);
      message.success(liveText(() => t("已重新打开新手引导")));
    },
    onError: () => message.error(liveText(() => t("新手引导打开失败"))),
  });
  const backupMutation = useMutation({
    mutationFn: getSettingsBackup,
    onSuccess: (backup) => {
      const blob = new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `offerpilot-settings-backup-v${backup.version}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      message.success(liveText(() => t("设置备份已导出")));
    },
    onError: () => {
      message.error(liveText(() => t("设置备份导出失败")));
    },
  });
  useEffect(() => {
    if (logsQuery.data) setLastLogsPage(logsQuery.data);
  }, [logsQuery.data]);
  const settings = settingsQuery.data;
  const logsPage = logsQuery.data ?? lastLogsPage;
  const logs = logsPage?.entries ?? [];
  const diagnosticsText = useMemo(
    () => (settings ? buildDiagnosticsText(settings, logs) : ''),
    [settings, logs, getLocale()],
  );

  async function copyDiagnostics() {
    if (!diagnosticsText) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(diagnosticsText);
      message.success(liveText(() => t("诊断信息已复制")));
    } catch {
      localizedModal('info', () => ({
        title: t("复制诊断信息失败，请手动复制"),
        content: <Input.TextArea value={diagnosticsText} readOnly autoSize={{ minRows: 8, maxRows: 18 }} />,
        width: 720,
      }));
    }
  }

  function refreshLogs() {
    setLogPage(1);
    void queryClient.invalidateQueries({
      queryKey: ['runtime-logs', LOG_PAGE_SIZE, 0, logLevel],
      exact: true,
    });
  }

  return (
    <>
    {aiSettingsOpen && <Suspense fallback={<Skeleton active paragraph={{ rows: 8 }} />}>
      <AISettingsDrawer open onClose={() => setAISettingsOpen(false)} />
    </Suspense>}
    <section
      aria-hidden={aiSettingsOpen || undefined}
      style={{
        maxWidth: 1040,
        display: aiSettingsOpen ? 'none' : 'grid',
        gap: 20,
      }}
    >
      <div>
        <Typography.Title level={2} style={{ margin: 0, color: 'var(--op-ink)' }}>
          {t("设置")}</Typography.Title>
      </div>

      <nav aria-label={t('设置分组')}><Space wrap>
        <Button type="link" onClick={() => document.getElementById('settings-general')?.scrollIntoView({ block: 'start' })}>{t('通用与外观')}</Button>
        <Button type="link" onClick={() => document.getElementById('settings-ai-privacy')?.scrollIntoView({ block: 'start' })}>{t('AI 与隐私')}</Button>
        <Button type="link" onClick={() => document.getElementById('settings-connections')?.scrollIntoView({ block: 'start' })}>{t('连接与自动任务')}</Button>
        <Button type="link" onClick={() => document.getElementById('data-backup-settings-title')?.scrollIntoView({ block: 'start' })}>{t('数据与备份')}</Button>
        <Button type="link" onClick={() => document.getElementById('settings-diagnostics')?.scrollIntoView({ block: 'start' })}>{t('高级与诊断')}</Button>
      </Space></nav>
      <Typography.Title id="settings-general" level={3}>{t('通用与外观')}</Typography.Title>
      <section style={panelStyle} aria-labelledby="language-settings-title">
        <Typography.Title id="language-settings-title" level={4} style={panelTitleStyle}>{t('界面语言')}</Typography.Title>
        <Select
          aria-label={t('界面语言')}
          value={locale}
          disabled={localeSaving}
          onChange={async (next) => {
            setLocaleSaving(true);
            try { await setLocale(next); }
            catch { message.error(liveText(() => t('无法保存语言设置，请重试。'))); }
            finally { setLocaleSaving(false); }
          }}
          options={[{ value: 'zh-CN', label: '简体中文' }, { value: 'en-US', label: 'English' }]}
          style={{ width: 220, maxWidth: '100%' }}
        />
        <Typography.Text type="secondary">{t('立即切换并保存在此设备；不会翻译你输入的内容或已有 AI 回复。')}</Typography.Text>
      </section>

      <section style={panelStyle} aria-labelledby="pilot-mascot-settings-title">
        <Space align="start" size={12} style={{ justifyContent: 'space-between' }}>
          <Space align="start" size={12}>
            <span style={panelIconStyle}>
              <RobotOutlined />
            </span>
            <div>
              <Typography.Title id="pilot-mascot-settings-title" level={4} style={panelTitleStyle}>
                {t("Haru 与外观")}</Typography.Title>
              <Typography.Text style={{ color: 'var(--op-muted)' }}>
                {t("调整 Haru 在桌面工作区的显示、大小、位置和动态效果。")}</Typography.Text>
            </div>
          </Space>
          <Switch
            aria-label={t("显示 Haru")}
            checked={pilotMascotVisible}
            onChange={onPilotMascotVisibleChange}
          />
        </Space>
        <Divider style={{ margin: 0 }} />
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
            gap: 12,
          }}
        >
          <label style={preferenceFieldStyle}>
            <span style={preferenceLabelStyle}>{t("角色大小")}</span>
            <Select
              aria-label={t("Haru 角色大小")}
              value={pilotMascotZoom}
              onChange={onPilotMascotZoomChange}
              options={[0.8, 0.9, 1, 1.1, 1.2, 1.3].map((value) => ({
                value,
                label: `${Math.round(value * 100)}%`,
              }))}
              style={{ width: '100%' }}
            />
          </label>
          <label style={preferenceFieldStyle}>
            <span style={preferenceLabelStyle}>{t("动画级别")}</span>
            <Select
              aria-label={t("Haru 动画级别")}
              value={pilotMascotAnimationLevel}
              onChange={(value) => onPilotMascotAnimationLevelChange(value)}
              options={[
                { value: 'full', label: t("完整") },
                { value: 'minimal', label: t("简洁") },
                { value: 'off', label: t("关闭") },
              ]}
              style={{ width: '100%' }}
            />
          </label>
        </div>
        <div>
          <Button
            icon={<ReloadOutlined />}
            aria-label={t("重置 Haru 位置")}
            onClick={onPilotMascotResetPosition}
          >
            {t("重置位置")}</Button>
        </div>
        <Typography.Text role="status" style={{ color: 'var(--op-muted)', fontSize: 12 }}>
          {systemReducedMotion
            ? t("系统已开启减少动态效果，Haru 将以静态方式显示。")
            : t("Haru 会遵循系统的减少动态效果设置。")}
        </Typography.Text>
        <Typography.Text style={{ color: 'var(--op-muted)', fontSize: 12 }}>
          {t("Haru character © Live2D Inc.，依据官方样例数据条款使用。")}{' '}
          <a href="https://www.live2d.com/en/learn/sample/" target="_blank" rel="noreferrer">
            {t("查看 Live2D 样例与许可说明")}</a>
        </Typography.Text>
      </section>

      <DesktopUpdatesCard />
      <Typography.Title id="settings-ai-privacy" level={3}>{t("AI 与隐私")}</Typography.Title>
      <section style={panelStyle}>
        <Space align="start" size={12}>
          <span style={panelIconStyle}>
            <ApiOutlined />
          </span>
          <div>
            <Typography.Title level={4} style={panelTitleStyle}>
              {t("AI 与模型")}</Typography.Title>
            <Typography.Text style={{ color: 'var(--op-muted)' }}>
              {t("管理模型供应商、模型、密钥与写入确认策略。")}</Typography.Text>
          </div>
        </Space>
        <Divider style={{ margin: 0 }} />
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
            gap: 12,
          }}
        >
          <RuntimeField label={t("运行模式")} value={formatRuntimeMode(settings?.runtime_mode)} />
          <RuntimeField label={t("后端版本")} value={settings?.version ?? '-'} />
          <RuntimeField label={t("密钥状态")} value={settings?.has_api_key ? t("已配置") : t("未配置")} />

        </div>
        <details style={detailsStyle}>
          <summary style={detailsSummaryStyle}>{t("高级运行信息")}</summary>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginTop: 12 }}>
            <RuntimeField label={t("多供应商")} value={t("{value0} 个", { value0: settings?.providers.length ?? 0 })} />
            <RuntimeField label="Fallback" value={fallbackLabel(settings)} />
            <RuntimeField label={t("日志级别")} value={settings?.log_level ?? '-'} />
            <RuntimeField label={t("访问控制")} value={settings?.auth_enabled ? t("已开启") : t("未开启")} />
          </div>
        </details>
        <div>
          <Space wrap>
            <Button type="primary" icon={<SettingOutlined />} onClick={() => setAISettingsOpen(true)}>
              {t("配置 AI")}</Button>
          </Space>
        </div>
      </section>

      <ContextPolicySettings />
      <ConfirmedMemorySettings />
      <CompanionPersonaSettings />
      <CompanionLifeSettings />

      <Typography.Title id="settings-connections" level={3}>{t("连接与自动任务")}</Typography.Title>
      <Typography.Paragraph type="secondary">{t("这里只管理连接、权限和自动任务范围；提醒、邮件待审与任务结果统一在今日待办处理。")}</Typography.Paragraph>
      <JobMailSettings />
      <ProactiveSettings />

      <section style={panelStyle} aria-labelledby="data-backup-settings-title">
        <Space align="start" size={12}>
          <span style={panelIconStyle}><DownloadOutlined /></span>
          <div>
            <Typography.Title id="data-backup-settings-title" level={4} style={panelTitleStyle}>{t("数据与备份")}</Typography.Title>
            <Typography.Text style={{ color: 'var(--op-muted)' }}>{t("导出本机设置或完整工作区数据。")}</Typography.Text>
          </div>
        </Space>
        <Divider style={{ margin: 0 }} />
        <RuntimeField label={t("数据目录")} value={settings?.data_dir ?? '-'} />
        <div>
          <Space wrap>
            <Button
              icon={<DownloadOutlined />}
              loading={backupMutation.isPending}
              onClick={() => backupMutation.mutate()}
            >
              {t("导出备份")}</Button>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => void exportBackup('/backups/export').catch(() => message.error(liveText(() => t("完整数据导出失败"))))}
            >
              {t("导出完整数据")}</Button>
            <Button
              loading={reopenOnboardingMutation.isPending}
              onClick={() => reopenOnboardingMutation.mutate()}
            >
              {t("重新打开新手引导")}</Button>
          </Space>
        </div>
      </section>

      <section style={panelStyle} aria-labelledby="voice-settings-title">
        <Typography.Title id="voice-settings-title" level={4} style={panelTitleStyle}>{t("语音")}</Typography.Title>
        <details style={detailsStyle} onToggle={(event) => { if (event.currentTarget.open) setVoiceOpened(true); }}>
          <summary style={detailsSummaryStyle}>{t('管理离线语音模型')}</summary>
          {voiceOpened && <Suspense fallback={<Skeleton active paragraph={{ rows: 2 }} />}><OfflineWhisperModelCard /></Suspense>}
        </details>
      </section>

      <section style={panelStyle}>
        <Space align="start" size={12}>
          <span style={panelIconStyle}>
            <FileSearchOutlined />
          </span>
          <div style={{ flex: 1 }}>
            <Typography.Title id="settings-diagnostics" level={4} style={panelTitleStyle}>
              {t("高级与诊断")}</Typography.Title>
            <Typography.Text style={{ color: 'var(--op-muted)' }}>
              {t("最近的本地运行日志。")}</Typography.Text>
          </div>
        </Space>
        <details style={detailsStyle} open={diagnosticsOpen} onToggle={(event) => setDiagnosticsOpen(event.currentTarget.open)}>
          <summary style={detailsSummaryStyle}>{t("查看运行日志与诊断")}</summary>
          <div style={{ display: 'grid', gap: 16, marginTop: 16 }}>
            <Space wrap>
              <Select
                aria-label={t("日志筛选")}
                value={logLevel}
                onChange={(value) => {
                  setLogLevel(value);
                  setLogPage(1);
                }}
                options={[
                  { value: '', label: t("全部日志") },
                  { value: 'DEBUG', label: 'DEBUG' },
                  { value: 'INFO', label: 'INFO' },
                  { value: 'WARNING', label: 'WARNING' },
                  { value: 'ERROR', label: 'ERROR' },
                ]}
                style={{ width: 130 }}
              />
              <Button icon={<CopyOutlined />} onClick={copyDiagnostics} disabled={!settings}>{t("复制诊断信息")}</Button>
              <Button icon={<ReloadOutlined />} onClick={refreshLogs} loading={logsQuery.isFetching} aria-label={t("刷新日志")} />
            </Space>
            <Divider style={{ margin: 0 }} />
            {!logsPage ? (
          logsQuery.isError ? (
            <Alert
              type="error"
              showIcon
              message={t("日志加载失败")}
              action={
                <Button aria-label={t("重试日志加载")} onClick={() => void logsQuery.refetch()}>
                  {t("重试日志加载")}</Button>
              }
            />
          ) : (
            <Skeleton active paragraph={{ rows: 3 }} />
          )
            ) : (
          <>
            {logsQuery.isError ? (
              <Alert
                type="warning"
                showIcon
                message={t("日志刷新失败，正在显示上一页结果")}
                action={
                  <Button aria-label={t("重试日志加载")} onClick={() => void logsQuery.refetch()}>
                    {t("重试日志加载")}</Button>
                }
              />
            ) : null}
            {logsQuery.isFetching ? <Spin size="small" aria-label={t("正在加载日志页")} /> : null}
            <div
              aria-label={t("运行日志列表")}
              role="region"
              style={{ height: 360, overflowY: 'auto', overscrollBehavior: 'contain' }}
            >
              <LogList entries={logsPage.entries} />
            </div>
            <Pagination
              current={logPage}
              pageSize={LOG_PAGE_SIZE}
              total={logsPage.total}
              showSizeChanger={false}
              onChange={(page) => setLogPage(page)}
            />
          </>
            )}
          </div>
        </details>
      </section>
    </section>
    </>
  );
}

const panelStyle = {
  display: 'grid',
  gap: 16,
  padding: 20,
  background: 'var(--op-surface)',
  border: '1px solid var(--op-border)',
  borderRadius: 8,
  boxShadow: 'var(--op-shadow-sm)',
} as const;

const panelIconStyle = {
  color: 'var(--op-primary)',
  fontSize: 20,
  lineHeight: 1,
} as const;

const panelTitleStyle = {
  margin: 0,
  color: 'var(--op-ink)',
  textWrap: 'balance',
} as const;

const detailsStyle = {
  border: '1px solid var(--op-border)',
  borderRadius: 6,
  padding: '10px 12px',
} as const;

const detailsSummaryStyle = {
  color: 'var(--op-ink)',
  cursor: 'pointer',
  fontWeight: 600,
} as const;

const preferenceFieldStyle = {
  display: 'grid',
  gap: 8,
} as const;

const preferenceLabelStyle = {
  color: 'var(--op-ink)',
  fontSize: 13,
  fontWeight: 600,
} as const;

function RuntimeField({ label, value }: { label: string; value: string }) {
  useLocale();
  return (
    <div
      style={{
        minHeight: 64,
        padding: 12,
        borderRadius: 6,
        background: 'rgba(148, 163, 184, 0.08)',
      }}
    >
      <Typography.Text style={{ display: 'block', color: 'var(--op-muted)', fontSize: 12 }}>
        {label}
      </Typography.Text>
      <Typography.Text strong style={{ color: 'var(--op-ink)', fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </Typography.Text>
    </div>
  );
}

function LogList({ entries }: { entries: LogEntry[] }) {
  useLocale();
  if (!entries.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("暂无日志")} />;
  }
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {entries.map((entry, index) => (
        <div
          key={`${entry.level}-${index}-${entry.message}`}
          style={{
            display: 'grid',
            gridTemplateColumns: '88px minmax(0, 1fr)',
            gap: 10,
            alignItems: 'start',
            padding: '10px 12px',
            borderRadius: 6,
            background: 'rgba(15, 23, 42, 0.03)',
          }}
        >
          <Tag color={levelColor(entry.level)} style={{ margin: 0, textAlign: 'center' }}>
            {entry.level || 'INFO'}
          </Tag>
          <Typography.Text style={{ color: 'var(--op-ink)', overflowWrap: 'anywhere' }}>
            {entry.message || '-'}
          </Typography.Text>
        </div>
      ))}
    </div>
  );
}

function levelColor(level: string) {
  switch (level.toUpperCase()) {
    case 'DEBUG':
      return 'default';
    case 'WARNING':
      return 'gold';
    case 'ERROR':
      return 'red';
    default:
      return 'blue';
  }
}

function formatRuntimeMode(value: string | undefined) {
  if (value === 'local') return t("本地模式");
  if (value === 'server') return t("服务器模式");
  return '-';
}

function fallbackLabel(settings?: Settings) {
  if (!settings?.fallback_provider_ids.length) return t("未启用");
  return settings.fallback_provider_ids
    .map((providerId) => settings.providers.find((item) => item.id === providerId)?.label || providerId)
    .join(' → ');
}
