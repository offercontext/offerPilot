import { BellOutlined, CloudServerOutlined, SaveOutlined, StopOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Divider, InputNumber, Select, Skeleton, Space, Switch, Tag, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { listApplications } from '@/services/applications';
import {
  getProactiveSettings,
  updateProactiveSettings,
} from '@/services/proactive';
import type { Application } from '@/types/application';
import {
  DEFAULT_PROACTIVE_SETTINGS,
  type ProactiveSettings as ProactiveSettingsValue,
} from '@/types/proactive';
import styles from './ProactiveSettings.module.css';

const PROACTIVE_SETTINGS_QUERY_KEY = ['proactive-settings'];
const APPLICATIONS_QUERY_KEY = ['applications'];

const TIMEZONE_OPTIONS = [
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Singapore',
  'America/Los_Angeles',
  'America/New_York',
  'Europe/London',
  'Europe/Berlin',
  'UTC',
];

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, hour) => ({
  value: hour,
  label: hourLabel(hour),
}));

function normalizeSettings(value: Partial<ProactiveSettingsValue> | undefined): ProactiveSettingsValue {
  return {
    ...DEFAULT_PROACTIVE_SETTINGS,
    ...value,
    application_ids: [...new Set(value?.application_ids ?? DEFAULT_PROACTIVE_SETTINGS.application_ids)],
  };
}

function applicationLabel(application: Application): string {
  return `${application.company_name} · ${application.position_name}`;
}

export default function ProactiveSettings() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({
    queryKey: PROACTIVE_SETTINGS_QUERY_KEY,
    queryFn: getProactiveSettings,
  });
  const applicationsQuery = useQuery({
    queryKey: APPLICATIONS_QUERY_KEY,
    queryFn: () => listApplications(),
  });
  const [draft, setDraft] = useState<ProactiveSettingsValue>(DEFAULT_PROACTIVE_SETTINGS);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (settingsQuery.data && !dirty) {
      setDraft(normalizeSettings(settingsQuery.data.settings));
    }
  }, [dirty, settingsQuery.data]);

  const applications = useMemo(
    () => (applicationsQuery.data ?? []).filter((application) => !application.deleted_at),
    [applicationsQuery.data],
  );
  const applicationOptions = useMemo(() => {
    const knownIds = new Set(applications.map((application) => application.id));
    const missingSelected = draft.application_ids
      .filter((id) => !knownIds.has(id))
      .map((id) => ({ value: id, label: `投递 #${id}（当前不可用）`, disabled: true }));
    return [
      ...applications.map((application) => ({
        value: application.id,
        label: applicationLabel(application),
      })),
      ...missingSelected,
    ];
  }, [applications, draft.application_ids]);

  function patchDraft(patch: Partial<ProactiveSettingsValue>) {
    setDraft((current) => ({ ...current, ...patch }));
    setDirty(true);
    setNotice('');
    setError('');
  }

  async function saveSettings(next: ProactiveSettingsValue = draft, successMessage = '主动任务设置已保存') {
    if (!settingsQuery.data || saving) return;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const result = await updateProactiveSettings(settingsQuery.data.revision, normalizeSettings(next));
      queryClient.setQueryData(PROACTIVE_SETTINGS_QUERY_KEY, result);
      setDraft(normalizeSettings(result.settings));
      setDirty(false);
      setNotice(successMessage);
    } catch (saveError) {
      setError(
        (saveError as { response?: { status?: number } }).response?.status === 409
          ? '设置版本已变化，请刷新后再保存。'
          : '主动任务设置保存失败，请检查本地服务后重试。',
      );
      await settingsQuery.refetch();
    } finally {
      setSaving(false);
    }
  }

  function disableAll() {
    void saveSettings({ ...draft, enabled: false }, '已关闭全部主动任务');
  }

  if (settingsQuery.isPending) {
    return <Skeleton active paragraph={{ rows: 10 }} />;
  }

  if (settingsQuery.isError || !settingsQuery.data) {
    return (
      <section className={styles.panel} aria-labelledby="proactive-settings-title">
        <Alert
          type="error"
          showIcon
          message="主动任务设置读取失败"
          description="请确认本地服务已运行，然后重试。"
          action={<Button onClick={() => void settingsQuery.refetch()}>重试</Button>}
        />
      </section>
    );
  }

  return (
    <section className={styles.panel} aria-labelledby="proactive-settings-title">
      <div className={styles.header}>
        <div className={styles.heading}>
          <span className={styles.headingIcon} aria-hidden="true"><BellOutlined /></span>
          <div>
            <Typography.Title id="proactive-settings-title" level={4} className={styles.headingTitle}>
              主动提醒与准备
            </Typography.Title>
            <p className={styles.headingCopy}>
              让本地服务在你选择的投递上准备提醒和面试草稿，所有任务都可以随时关闭。
            </p>
          </div>
        </div>
        <div className={styles.headerActions}>
          <Button
            danger
            icon={<StopOutlined />}
            disabled={!draft.enabled || saving}
            onClick={disableAll}
          >
            一键关闭全部
          </Button>
        </div>
      </div>

      <Alert
        className={styles.serviceNote}
        type="info"
        showIcon
        icon={<CloudServerOutlined />}
        message="需要本地服务保持运行"
        description="主动任务在本机后台检查。服务停止时不会继续排队或生成；重新启动后会从持久化队列继续检查。"
      />

      <div className={styles.masterRow}>
        <div className={styles.rowCopy}>
          <span className={styles.rowTitle}>启用主动任务</span>
          <span className={styles.rowHint}>
            {draft.enabled ? '当前允许本地服务按下面的范围和开关处理任务。' : '已关闭；下面的设置会保留，之后可以重新开启。'}
          </span>
        </div>
        <Switch
          aria-label="启用主动任务"
          checked={draft.enabled}
          disabled={saving}
          onChange={(enabled) => patchDraft({ enabled })}
        />
      </div>

      <div className={styles.section}>
        <div>
          <div className={styles.sectionTitle}>任务类型</div>
          <div className={styles.sectionHint}>普通提醒和可能消耗 AI 额度的准备草稿分开控制，默认都关闭。</div>
        </div>
        <div className={styles.optionList}>
          <div className={styles.optionRow}>
            <div className={styles.rowCopy}>
              <span className={styles.rowTitle}>普通提醒</span>
              <span className={styles.rowHint}>截止、面试临近、投递跟进和面试复盘提醒，不调用模型。</span>
            </div>
            <Switch
              aria-label="启用普通提醒"
              checked={draft.reminders_enabled}
              disabled={saving}
              onChange={(reminders_enabled) => patchDraft({ reminders_enabled })}
            />
          </div>
          <div className={styles.optionRow}>
            <div className={styles.rowCopy}>
              <span className={styles.rowTitle}>面试准备草稿 <Tag color="purple">可能消耗额度</Tag></span>
              <span className={styles.rowHint}>只生成供你查看的自动准备草稿，不代表你已发送，也不会自动执行业务操作。</span>
            </div>
            <Switch
              aria-label="启用面试准备草稿"
              checked={draft.drafts_enabled}
              disabled={saving}
              onChange={(drafts_enabled) => patchDraft({ drafts_enabled })}
            />
          </div>
        </div>
      </div>

      <Divider style={{ margin: 0 }} />

      <div className={styles.section}>
        <div>
          <div className={styles.sectionTitle}>主动任务范围</div>
          <div className={styles.sectionHint}>只为明确选择的投递创建任务；移除投递后，相关未完成任务会停止。</div>
        </div>
        <label className={styles.scopeField} htmlFor="proactive-application-scope">
          <span className={styles.fieldLabel}>选择投递</span>
          <Select<number[]>
            id="proactive-application-scope"
            className={styles.scopeSelect}
            mode="multiple"
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={applicationsQuery.isPending ? '正在读取投递…' : '选择一个或多个投递'}
            value={draft.application_ids}
            options={applicationOptions}
            loading={applicationsQuery.isPending}
            disabled={saving}
            onChange={(application_ids) => patchDraft({ application_ids })}
          />
        </label>
        {applicationsQuery.isError ? (
          <Alert type="warning" showIcon message="投递列表读取失败，暂时无法调整范围。" />
        ) : null}
      </div>

      <div className={styles.section}>
        <div>
          <div className={styles.sectionTitle}>时间与费用限制</div>
          <div className={styles.sectionHint}>时间按所选时区计算；安静时段内不会生成或发布主动任务。</div>
        </div>
        <div className={styles.fieldGrid}>
          <label className={styles.fieldCard} htmlFor="proactive-timezone">
            <span className={styles.fieldLabel}>时区</span>
            <Select
              id="proactive-timezone"
              value={draft.timezone}
              options={TIMEZONE_OPTIONS.includes(draft.timezone)
                ? TIMEZONE_OPTIONS.map((timezone) => ({ value: timezone, label: timezone }))
                : [{ value: draft.timezone, label: `${draft.timezone}（当前）` }, ...TIMEZONE_OPTIONS.map((timezone) => ({ value: timezone, label: timezone }))]}
              onChange={(timezone: string) => patchDraft({ timezone })}
              disabled={saving}
            />
          </label>
          <div className={styles.fieldCard}>
            <span className={styles.fieldLabel}>安静时段</span>
            <Space.Compact block>
              <Select aria-label="安静时段开始" value={draft.quiet_start_hour} options={HOUR_OPTIONS} onChange={(quiet_start_hour: number) => patchDraft({ quiet_start_hour })} disabled={saving} />
              <Select aria-label="安静时段结束" value={draft.quiet_end_hour} options={HOUR_OPTIONS} onChange={(quiet_end_hour: number) => patchDraft({ quiet_end_hour })} disabled={saving} />
            </Space.Compact>
          </div>
          <label className={styles.fieldCard} htmlFor="proactive-reminder-budget">
            <span className={styles.fieldLabel}>普通提醒每日上限</span>
            <InputNumber id="proactive-reminder-budget" min={1} max={20} precision={0} value={draft.max_reminders_per_day} onChange={(max_reminders_per_day) => { if (max_reminders_per_day !== null) patchDraft({ max_reminders_per_day }); }} disabled={saving} addonAfter="条" />
          </label>
          <label className={styles.fieldCard} htmlFor="proactive-draft-budget">
            <span className={styles.fieldLabel}>准备草稿每日上限</span>
            <InputNumber id="proactive-draft-budget" min={1} max={3} precision={0} value={draft.max_drafts_per_day} onChange={(max_drafts_per_day) => { if (max_drafts_per_day !== null) patchDraft({ max_drafts_per_day }); }} disabled={saving} addonAfter="次" />
          </label>
        </div>
      </div>

      {(error || notice) ? (
        <div role={error ? 'alert' : 'status'} className={error ? undefined : styles.status}>
          {error || notice}
        </div>
      ) : null}

      <div className={styles.saveBar}>
        <span className={styles.revision}>设置版本 {settingsQuery.data.revision}{dirty ? ' · 有未保存修改' : ''}</span>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          disabled={!dirty || saving}
          onClick={() => void saveSettings()}
        >
          保存主动任务设置
        </Button>
      </div>
    </section>
  );
}
