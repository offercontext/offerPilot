import { companionText, useCompanionName } from '@/features/companionPersona/useCompanionName';
import { useState } from 'react';
import { CompanionLifeDetails } from './CompanionLifeStatus';
import { Button, Select, Space, Switch, Typography } from 'antd';
import { t, useLocale } from '@/i18n';
import { localizedModal } from '@/i18n/modal';
import { useCompanionLife } from './useCompanionLife';
import styles from './companionLife.module.css';
export default function CompanionLifeSettings() {
  useLocale();
  const companionName = useCompanionName();
  const life = useCompanionLife();
  const [detailsOpen, setDetailsOpen] = useState(false);
  if (!life) return null;
  const settings = life.snapshot?.settings;
  const disabled = !settings || life.busy || life.uncertain;
  return <section className={styles.settings} aria-labelledby="companion-life-settings-title">
    <Typography.Title level={4} id="companion-life-settings-title">{companionText(t('Haru 的虚拟日常'), companionName)}</Typography.Title>
    <p>{companionText(t('Haru 可以有自己的小活动和愿望；这是虚拟角色设定，不是现实经历。不会改变求职任务或已确认记忆。'), companionName)}</p>
    <label className={styles.row}><span>{t('开启虚拟日常')}</span><Switch aria-label={t('开启虚拟日常')} checked={settings?.enabled ?? false} disabled={disabled} onChange={enabled => settings && void life.updateSettings({ ...settings, enabled })} /></label>
    <label className={styles.row}><span>{t('日常主动程度')}</span><Select aria-label={t('日常主动程度')} value={settings?.proactivity ?? 'off'} disabled={disabled || !settings?.enabled} onChange={proactivity => settings && void life.updateSettings({ ...settings, proactivity })} options={[{ value: 'off', label: t('只在我打开时') }, { value: 'less', label: t('少一点') }, { value: 'default', label: t('适量') }]} /></label>
    <p>{t('输入、工作、待确认和模拟面试期间不打扰。一次邀请未回应，本次使用不再主动邀请。')}</p>
    <label className={styles.row}><span>{t('隐藏日常状态')}</span><Switch aria-label={t('隐藏日常状态')} checked={settings?.hide_status ?? false} disabled={disabled} onChange={hide_status => settings && void life.updateSettings({ ...settings, hide_status })} /></label>
    <label className={styles.row}><span>{t('这次安静陪伴')}</span><Switch aria-label={t('这次安静陪伴')} checked={life.quiet} disabled={disabled} onChange={value => void life.setQuiet(value)} /></label>
    <Button disabled={!settings?.enabled || life.busy} aria-expanded={detailsOpen} onClick={() => { setDetailsOpen(value => !value); if (!detailsOpen) void life.event('user_interaction', true); }}>{t('查看日常活动')}</Button>
    {detailsOpen && settings?.enabled && <CompanionLifeDetails onClose={() => setDetailsOpen(false)} />}
    {life.error && <p role="alert">{t(life.error)} <Button size="small" disabled={life.busy} onClick={() => void life.retry()}>{t('重试')}</Button></p>}
    <Space wrap><Button danger disabled={disabled} onClick={() => localizedModal('confirm', () => ({ title: t('重置虚拟日常？'), content: companionText(t('这会清除 Haru 的虚拟活动、愿望和共同参与记录，无法撤销。不会删除对话、求职数据或已确认记忆。'), companionName), okText: t('重置虚拟日常'), cancelText: t('取消'), okButtonProps: { danger: true }, onOk: () => life.reset() }))}>{t('重置虚拟日常')}</Button></Space>
  </section>;
}
