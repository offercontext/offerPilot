import { useEffect, useState } from 'react';
import { Alert, Descriptions, Drawer, Spin } from 'antd';
import { t, useLocale } from '@/i18n';
import { getEvent } from '@/services/events';
import { listApplications } from '@/services/applications';
import type { Application } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import type { ReminderTarget } from '@/features/desktopReminders/types';
export default function DesktopReminderTarget() {
 useLocale();
 const [open,setOpen]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState(false);
 const [item,setItem]=useState<{application:Application;event:ScheduleEvent|null}>();
 useEffect(()=>{
  const bridge=window.offerpilotReminders;if(!bridge)return;
  let live=true,sequence=0;
  const consume=async()=>{
   let target:ReminderTarget|null;
   try{target=await bridge.consumeNavigation();}catch{return;}
   if(!live||!target)return;
   const request=++sequence;setOpen(true);setLoading(true);setError(false);setItem(undefined);
   try{
    if(!Number.isSafeInteger(target.applicationId)||target.applicationId<=0||(target.eventId!==null&&(!Number.isSafeInteger(target.eventId)||target.eventId<=0)))throw Error();
    const [applications,event]=await Promise.all([listApplications(),target.eventId===null?Promise.resolve(null):getEvent(target.eventId)]);
    const application=applications.find(a=>a.id===target.applicationId);
    if(!application||(event&&(event.id!==target.eventId||event.application_id!==application.id)))throw Error();
    if(live&&request===sequence)setItem({application,event});
   }catch{if(live&&request===sequence)setError(true);}finally{if(live&&request===sequence)setLoading(false);}
  };
  const off=bridge.onNavigation(()=>{void consume();});void consume();
  return()=>{live=false;sequence++;off();};
 },[]);
 // Keep the ordinary web shell free of a dormant desktop-only drawer.
 if(!open)return null;
 return <Drawer title={t('提醒对应事项')} open={open} onClose={()=>setOpen(false)} width={520}>
  {loading&&<Spin/>}
  {error&&<Alert type="warning" message={t('事项已变化、不可见或连接失败，请在提醒列表中核对。')}/>}
  {item&&<Descriptions column={1} items={[
   {key:'company',label:t('公司'),children:item.application.company_name},
   {key:'position',label:t('职位'),children:item.application.position_name},
   {key:'status',label:t('状态'),children:item.event?.status??item.application.status},
   ...(item.event?[{key:'time',label:t('时间'),children:item.event.scheduled_at},{key:'location',label:t('地点'),children:item.event.location},{key:'notes',label:t('备注'),children:item.event.notes}]:[]),
  ]}/>}
 </Drawer>;
}
