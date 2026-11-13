'use strict';
const {isTrustedFrame}=require('./haru-protocol.cjs');
const {createReminderStore}=require('./reminder-store.cjs');
const {createReminderController}=require('./reminder-controller.cjs');
const {readReminderCandidates}=require('./reminder-transport.cjs');
const CHANNEL='offerpilot:reminders:';
function createReminderService({ipcMain,Notification,powerMonitor,window,origin,token,userData,isQuitting,locale}){
 let pending=null,disposed=false,lastTest=0,testNotice=null;
 const send=(name,value)=>{if(!disposed&&!isQuitting()&&!window.isDestroyed())window.webContents.send(CHANNEL+name,value);};
 const text=()=>({title:'OfferPilot',body:locale()==='en-US'?'You have a reminder. Open OfferPilot to view it.':'你有一条提醒。打开 OfferPilot 查看。'});
 const controller=createReminderController({store:createReminderStore(userData),readCandidates:()=>readReminderCandidates(origin,token),
  supported:()=>Notification.isSupported(),createNotification:options=>new Notification(options),text,
  changed:state=>send('state',state),navigate:target=>{if(isQuitting()||disposed)return;pending={...target,expires:Date.now()+30000};if(window.isMinimized())window.restore();window.show();window.focus();send('navigation',null);}});
 const assertOwner=event=>{if(disposed||isQuitting()||!isTrustedFrame(event,window.webContents,origin))throw new Error('unavailable');};
 const handlers={
  state:()=>controller.status(),
  configure:value=>{pending=null;testNotice?.close();const result=controller.configure(value);void controller.tick();return result;},
  consume:()=>{const value=pending;pending=null;return value&&Date.now()<value.expires?{applicationId:value.applicationId,eventId:value.eventId}:null;},
  test:()=>{
   const state=controller.status();
   if(!state.preferences.enabled||state.status!=='ready'||!state.supported||Date.now()-lastTest<60000)throw new Error('unavailable');
   lastTest=Date.now();testNotice?.close();testNotice=new Notification({title:'OfferPilot',body:locale()==='en-US'?'Test reminder. System settings may hide notifications.':'测试提醒。系统设置可能隐藏通知。'});
   testNotice.on('failed',()=>send('test-result','failed'));
   testNotice.on('show',()=>send('test-result','system-accepted'));
   try{testNotice.show();return 'submitted';}catch{return 'failed';}
  },
 };
 for(const [name,handler] of Object.entries(handlers))ipcMain.handle(CHANNEL+name,(event,value)=>{assertOwner(event);return handler(value);});
 const pauses=new Set();
 const suspend=(reason='update')=>{pauses.add(reason);pending=null;testNotice?.close();controller.suspend();};
 const resume=(reason='update')=>{pauses.delete(reason);if(!pauses.size&&!isQuitting())void controller.resume();};
 const powerEvents=[['suspend',()=>suspend('sleep')],['resume',()=>resume('sleep')],['lock-screen',()=>suspend('locked')],['unlock-screen',()=>resume('locked')]];
 for(const [event,handler] of powerEvents)powerMonitor.on(event,handler);
 const timer=setInterval(()=>{if(!isQuitting())void controller.tick();},60000);timer.unref?.();void controller.tick();
 return {suspend,resume,dispose(){disposed=true;pending=null;clearInterval(timer);testNotice?.close();controller.dispose();for(const name of Object.keys(handlers))ipcMain.removeHandler(CHANNEL+name);for(const [event,handler] of powerEvents)powerMonitor.removeListener(event,handler);}};
}
module.exports={createReminderService};
