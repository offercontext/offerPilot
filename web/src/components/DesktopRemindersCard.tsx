import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Select, Space, Switch, Typography } from 'antd';
import { t, useLocale } from '@/i18n';
import type { ReminderPreferences, ReminderScope, ReminderState } from '@/features/desktopReminders/types';
const scopes: [ReminderScope,string][] = [['deadline','截止日期'],['interview_preparation','面试准备'],['review','面试复盘'],['stale_application','长期未跟进的投递']];
export default function DesktopRemindersCard() {
 useLocale();
 const bridge=window.offerpilotReminders;
 const [state,setState]=useState<ReminderState>();
 const [busy,setBusy]=useState(false),[error,setError]=useState(false),[testResult,setTestResult]=useState('');
 useEffect(()=>{
  if(!bridge)return;
  let active=true;
  const off=bridge.onState(value=>{if(active)setState(value);});
  const offTest=bridge.onTestResult(value=>{if(active)setTestResult(value);});
  void bridge.getState().then(value=>{if(active)setState(value);}).catch(()=>{if(active)setError(true);});
  return()=>{active=false;off();offTest();};
 },[bridge]);
 if(!bridge)return null;
 const save=async(patch:Partial<ReminderPreferences>)=>{
  if(!state)return;setBusy(true);setError(false);
  try{setState(await bridge.configure({...state.preferences,...patch}));}catch{setError(true);}finally{setBusy(false);}
 };
 const hours=Array.from({length:24},(_,value)=>({value,label:`${String(value).padStart(2,'0')}:00`}));
 return <section aria-label={t('桌面本地提醒')}>
  <Typography.Title level={4}>{t('桌面本地提醒')}</Typography.Title>
  <Typography.Paragraph>{t('默认关闭。选择范围后主动开启；仅提醒此后到期的事项。不会向招聘方发送消息，也不会更改事项。')}</Typography.Paragraph>
  <Typography.Paragraph type="secondary">{t('通知只显示通用提示，不显示公司或事项内容。应用退出后不保证提醒；休眠或断线恢复仅补发最近 15 分钟内仍有效的提醒，每批最多 3 条、每小时最多 10 条。其他活动和 Offer 截止日期仍在网页提醒中查看。')}</Typography.Paragraph>
  <Typography.Paragraph type="secondary">{t('系统勿扰、权限或通知中心设置可能隐藏通知；提交成功不代表你已看到。请在系统设置中允许 OfferPilot 通知。')}</Typography.Paragraph>
  {(error||state?.error)&&<Alert type="warning" message={t('提醒暂不可用。请检查系统通知设置或重新打开应用；若系统时间曾回拨，请先关闭再重新开启提醒。存储错误时不会发送。')} />}
  {state&&!state.supported&&<Alert type="warning" message={t('此系统不支持桌面通知。网页提醒仍可使用。')} />}
  {state&&<Space direction="vertical">
   <Checkbox.Group disabled={busy} value={state.preferences.scopes} options={scopes.map(([value,label])=>({value,label:t(label)}))} onChange={value=>void save({scopes:value as ReminderScope[],...(value.length===0?{enabled:false}:{})})}/>
   <label><Switch checked={state.preferences.enabled} disabled={busy||!state.supported||!state.preferences.scopes.length} onChange={enabled=>void save({enabled})}/> {t('开启桌面本地提醒')}</label>
   <label><Switch checked={state.preferences.paused} disabled={busy} onChange={paused=>void save({paused})}/> {t('暂停提醒')}</label>
   <label><Switch checked={state.preferences.quietEnabled} disabled={busy} onChange={quietEnabled=>void save({quietEnabled})}/> {t('勿扰时间（此设备本地时间）')}</label>
   <Space><Select aria-label={t('勿扰开始')} value={state.preferences.quietStart} options={hours} disabled={busy} onChange={quietStart=>void save({quietStart})}/><span>—</span><Select aria-label={t('勿扰结束')} value={state.preferences.quietEnd} options={hours} disabled={busy} onChange={quietEnd=>void save({quietEnd})}/></Space>
   <Typography.Text type="secondary">{t('开始与结束相同表示全天勿扰。暂停或勿扰期间不弹窗。')}</Typography.Text>
   <Button disabled={busy||state.status!=='ready'} onClick={async()=>{setBusy(true);try{setTestResult(await bridge.test());}catch{setTestResult('failed');}finally{setBusy(false);}}}>{t('发送测试通知（每分钟一次）')}</Button>
   {testResult&&<Typography.Text role="status">{testResult==='failed'?t('测试通知失败，请检查系统通知设置。'):t('已请求系统显示测试通知，是否可见请自行核对。')}</Typography.Text>}
  </Space>}
 </section>;
}
