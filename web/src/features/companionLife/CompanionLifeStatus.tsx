import { companionText, useCompanionName } from '@/features/companionPersona/useCompanionName';
import { companionLifeExpression } from './copy';
import { useEffect, useState } from 'react';
import { Button, Popover, Space } from 'antd';
import { t, useLocale } from '@/i18n';
import type { PilotMascotRect } from '@/features/pilotMascot/pilotMascotPreference';
import { useCompanionLife } from './useCompanionLife';
import { blocksCompanion, companionMirror } from './model';
import styles from './companionLife.module.css';
const STATUS = { active: '进行中', paused: '已暂停', waiting_shared: '等你有空一起', completed: '这件小事完成了', abandoned: '先放下了' };
export function CompanionLifeDetails({ onClose }: { onClose?: () => void }) {
  useLocale();
  const companionName = useCompanionName();
  const life = useCompanionLife();
  const snapshot = life?.snapshot;
  if (!life || !snapshot) return null;
  const activity = snapshot.state.activity;
  const wish = snapshot.state.wish;
  const canWithdraw = activity?.status === 'waiting_shared' || (activity?.status === 'paused' && activity.paused_from === 'waiting_shared');
  const disabled = life.busy || life.uncertain || !snapshot.settings.enabled;
  return <section className={styles.content} aria-label={companionText(t('Haru 的虚拟日常'), companionName)} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose?.(); } }}>
    <header><strong>{activity?.title ? t(activity.title) : companionText(t('Haru 的虚拟日常'), companionName)}</strong>{onClose && <Button size="small" type="text" aria-label={t('关闭日常详情')} onClick={onClose}>{t('关闭')}</Button>}</header>
    <p className={styles.explanation}>{companionText(t('这是 Haru 的虚拟日常，不代表现实经历。你可以随时离开，不必回应。'), companionName)}</p>
    {activity ? <><p>{companionLifeExpression(activity, t)}</p><small>{t(STATUS[activity.status])} · {t('第 {step} / {total} 步', { step: Math.min(activity.step_index + 1, activity.step_count), total: activity.step_count })}</small></> : <p>{companionLifeExpression(activity, t)}</p>}
    {activity?.promise && activity.status !== 'waiting_shared' && <p>{t(activity.promise)}</p>}
    {activity?.status === 'waiting_shared' && <p>{t('共同的小事会留在这里，等你在聊天里回应或确认这一步；不倒计时，也不会催促。')}</p>}
    {canWithdraw && <p>{companionText(t('不想参与这一步时，可以交给 Haru 自己做。不会清除之前的共同记录；已暂停的日常仍会暂停。'), companionName)}</p>}
    {wish && <p>{t(wish.text)}</p>}
    <Space wrap>
      {activity && ['active', 'waiting_shared'].includes(activity.status) && <Button size="small" disabled={disabled} onClick={() => void life.event('pause', true)}>{t('暂停日常')}</Button>}
      {activity?.status === 'paused' && <Button size="small" disabled={disabled} onClick={() => void life.event('resume', true)}>{t('继续日常')}</Button>}
      {activity?.status === 'active' && <Button size="small" disabled={disabled} onClick={() => void life.event('join', true)}>{t('一起做这件小事')}</Button>}
      {activity?.status === 'waiting_shared' && <Button size="small" disabled={disabled} onClick={() => void life.event('complete_shared', true)}>{t('约好的一步完成了')}</Button>}
      {canWithdraw && <Button size="small" disabled={disabled} onClick={() => void life.event('withdraw_shared', true)}>{companionText(t('这步请 Haru 自己做'), companionName)}</Button>}
      {activity && ['active', 'paused', 'waiting_shared'].includes(activity.status) && <Button size="small" disabled={disabled} onClick={() => void life.event('abandon', true)}>{t('先放下这件事')}</Button>}
      {wish && <>{activity?.status === 'active' && <Button size="small" disabled={disabled} onClick={() => void life.event('accept_wish', true)}>{t('试试这个愿望')}</Button>}<Button size="small" disabled={disabled} onClick={() => void life.event('decline_wish', true)}>{t('这次先不了')}</Button></>}
    </Space>
    {life.error && <p role="alert">{t(life.error)} <Button size="small" disabled={life.busy} onClick={() => void life.retry()}>{t('重试')}</Button></p>}
    <footer><Button type="text" size="small" disabled={disabled} onClick={() => { life.setQuiet(true); onClose?.(); }}>{t('这次安静陪伴')}</Button><Button type="text" size="small" disabled={disabled} onClick={() => void life.updateSettings({ ...snapshot.settings, hide_status: true })}>{t('隐藏日常状态')}</Button></footer>
  </section>;
}
export default function CompanionLifeStatus({ anchorRect, inline = false, suppressed = false }: { anchorRect?: PilotMascotRect; inline?: boolean; suppressed?: boolean }) {
  useLocale();
  const companionName = useCompanionName();
  const life = useCompanionLife();
  const [open, setOpen] = useState(false);
  const snapshot = life?.snapshot;
  const hardBlocked = !snapshot?.settings.enabled || snapshot.settings.hide_status || suppressed
    || Boolean(life && blocksCompanion({ ...life.signals, failure: false }));
  useEffect(() => { if (hardBlocked) setOpen(false); }, [hardBlocked]);
  if (!life || !snapshot?.settings.enabled || snapshot.settings.hide_status || suppressed
    || (blocksCompanion(life.signals) && !(open && life.uncertain && !hardBlocked)) || (snapshot.presence.blocked && !open)) return null;
  const activity = snapshot.state.activity;
  const invitation = life.bid ? companionMirror(snapshot, life.signals, life.bid) : undefined;
  const changeOpen = (value: boolean) => { setOpen(value); if (value) void life.event('user_interaction', true); };
  const content = <CompanionLifeDetails onClose={() => setOpen(false)} />;
  const style = !inline && anchorRect ? { left: Math.max(12, Math.min(anchorRect.left, window.innerWidth - 260)), bottom: Math.max(12, window.innerHeight - anchorRect.top + 8) } : undefined;
  return <div className={inline ? styles.inline : styles.anchor} style={style} data-companion-life="status">
    {life.bid && invitation && <div className={styles.bid} role="status"><span>{invitation.detail}</span><button type="button" aria-label={t('收起日常邀请')} onClick={life.dismissBid}>×</button></div>}
    <Popover content={content} trigger="click" open={open} onOpenChange={changeOpen} placement="top" destroyTooltipOnHide>
      <button type="button" className={styles.status} aria-expanded={open} aria-label={companionText(t('查看 Haru 的虚拟日常'), companionName)}>
        <span aria-hidden="true">·</span> {companionLifeExpression(activity, t)}{activity ? <small>{t('虚拟日常')} · {t(STATUS[activity.status])}</small> : null}
      </button>
    </Popover>
  </div>;
}
