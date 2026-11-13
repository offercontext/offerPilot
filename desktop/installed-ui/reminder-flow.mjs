import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {hash} from './integrity.mjs';
export const delay=ms=>new Promise(r=>setTimeout(r,ms));
export async function until(fn,timeout=30000){const end=Date.now()+timeout;do{if(await fn())return;await delay(200);}while(Date.now()<end);throw Error('bounded-condition-timeout');}
async function api(page,url,method='GET',body){return page.evaluate(async({url,method,body})=>{const r=await fetch(url,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok)throw Error(`synthetic API status ${r.status}`);return r.json();},{url,method,body});}
async function settings(page){await page.getByRole('navigation',{name:'主导航',exact:true}).getByRole('button',{name:'设置',exact:true}).click();await page.getByRole('button',{name:'连接与自动任务',exact:true}).click();await page.getByRole('heading',{name:'桌面本地提醒',exact:true}).waitFor();}
export async function reminderFlow({page,app,exe,userData,output,report,command,restart}){
 const assertStage=(name,value)=>{assert.ok(value,name);report.assertions.push(name);};
 const shot=async name=>{const file=`${name}.png`;await page.screenshot({path:path.join(output,file),fullPage:true});report.screenshots.push({file,sha256:await hash(path.join(output,file))});};
 report.native={visible:false,clicked:false,physicalSuspendResume:'not-run: hosted runner has no independently controllable wake path',showReceiptIsVisibility:false};
 const identity=await app.evaluate(({app,Notification,shell})=>({supported:Notification.isSupported(),shortcut:shell.readShortcutLink(`${app.getPath('desktop')}/OfferPilot Desktop.lnk`)}));
 report.native.identity={supported:identity.supported,configuredAppId:'org.offerpilot.desktop',appIdEvidence:'verified packaged main source and actual shortcut',shortcutAppId:identity.shortcut.appUserModelId??null,shortcutTargetMatches:identity.shortcut.target.toLowerCase()===exe.toLowerCase()};
 assertStage('native AppUserModelID and installed shortcut match',identity.shortcut.appUserModelId==='org.offerpilot.desktop'&&report.native.identity.shortcutTargetMatches);
 const nativeScript=path.join(path.dirname(fileURLToPath(import.meta.url)),'reminder-native.ps1');
 const readyFile=path.join(output,'native-observer-ready');
 const nativeAbort=new AbortController();
 const native=async mode=>JSON.parse(await command('pwsh.exe',['-NoProfile','-NonInteractive','-File',nativeScript,'-Mode',mode],{...process.env,REMINDER_NATIVE_SCREENSHOT:path.join(output,'native-toast.png'),REMINDER_NATIVE_READY:readyFile},100000,nativeAbort.signal));
 report.native.conditions=await native('conditions');
 await settings(page);const section=page.getByRole('region',{name:'桌面本地提醒',exact:true});
 const state=()=>page.evaluate(()=>window.offerpilotReminders.getState());
 assertStage('fresh installed profile remains opted out',(await state()).preferences.enabled===false);await shot('native-default-off');
 await section.getByRole('checkbox',{name:'截止日期',exact:true}).click();
 await until(async()=>(await state()).preferences.scopes.includes('deadline'));
 await until(()=>section.getByRole('checkbox',{name:'截止日期',exact:true}).isChecked());
 assertStage('scope selection alone does not opt in',(await state()).preferences.enabled===false);
 await section.getByRole('switch',{name:'开启桌面本地提醒',exact:true}).click();
 if((await state()).preferences.quietEnabled)await section.getByRole('switch',{name:'勿扰时间（此设备本地时间）',exact:true}).click();
 await until(async()=>(await state()).status==='ready');
 // Observe pass-through production Notification.show and real native events.
 // Never emit click/show, replace the notification constructor, or force permission.
 await app.evaluate(({Notification,app})=>{
  globalThis.__reminderEvidence={calls:[],events:[]};const original=Notification.prototype.show;
  Notification.prototype.show=function(...args){const evidence=globalThis.__reminderEvidence,id=evidence.calls.length;
   const stored=JSON.parse(process.getBuiltinModule('fs').readFileSync(`${app.getPath('userData')}/desktop-reminders.json`,'utf8'));
   evidence.calls.push({id,at:Date.now(),title:this.title,body:this.body,historyBeforeShow:stored.history});
   for(const event of ['show','failed','click','close'])this.on(event,()=>evidence.events.push({id,event,at:Date.now()}));
   return original.apply(this,args);
  };
 });
 const nativePending=native('observe-and-click');nativePending.catch(()=>{});
 try {
 await until(()=>fs.access(readyFile).then(()=>true,()=>false),15000);
 const ownerWindow=await app.browserWindow(page);await ownerWindow.evaluate(w=>w.minimize());
 report.native.backgroundBeforeDelivery=await ownerWindow.evaluate(w=>w.isMinimized());assert.ok(report.native.backgroundBeforeDelivery,'owner must actually be minimized before native delivery');
 const item=await api(page,'/api/applications','POST',{company_name:'Reminder QA Synthetic',position_name:'Native notification acceptance',status:'pending',notes:'synthetic-only'});
 const now=Date.now();const event=await api(page,'/api/application-events','POST',{application_id:item.id,event_type:'deadline',scheduled_at:new Date(now+3600000).toISOString(),remind_at:new Date(now+3000).toISOString(),duration_minutes:30,notes:'synthetic reminder detail',status:'todo'});
 const evidence=()=>app.evaluate(()=>globalThis.__reminderEvidence);
 await until(async()=>(await evidence()).calls.length>0,90000);
 const first=await evidence();report.native.productionCalls=first.calls;
 assertStage('production reminder uses generic private text',first.calls.length===1&&first.calls[0].title==='OfferPilot'&&first.calls[0].body==='你有一条提醒。打开 OfferPilot 查看。');
 assertStage('attempt persisted before native delivery',Object.keys(first.calls[0].historyBeforeShow).some(k=>k.startsWith(`application_event:${event.id}:deadline:`)));
 report.native.observation=await nativePending;
 report.native.visible=report.native.observation.visible===true;
 if(report.native.observation.pointerClick){
  try{await until(async()=>(await evidence()).events.some(e=>e.event==='click'),10000);await page.getByRole('dialog',{name:'提醒对应事项',exact:true}).waitFor({timeout:10000});await page.getByText('synthetic reminder detail',{exact:true}).waitFor();report.native.foregroundAfterClick=await ownerWindow.evaluate(w=>w.isVisible()&&!w.isMinimized());assert.ok(report.native.foregroundAfterClick);await shot('native-click-real-item');report.native.clicked=true;}catch{report.native.clicked=false;report.native.clickOutcome='native pointer attempted but application click/readback not confirmed';}
 }
 report.native.events=(await evidence()).events;
 const saved=JSON.parse(await fs.readFile(path.join(userData,'desktop-reminders.json'),'utf8'));
 assertStage('attempt history remains after native observation',Object.keys(saved.history).some(k=>k.startsWith(`application_event:${event.id}:deadline:`)));
 ({page,app}=await restart());await settings(page);
 assertStage('explicit local opt-in survives restart',(await state()).preferences.enabled);
 await delay(65000);
 report.native.restartNativeNonReplay='not directly instrumented during startup; persisted identity checked separately from deterministic controller restart test';
 const after=JSON.parse(await fs.readFile(path.join(userData,'desktop-reminders.json'),'utf8'));
 assertStage('restart preserves the same attempted identity',Object.keys(after.history).length===Object.keys(saved.history).length&&JSON.stringify(after.history)===JSON.stringify(saved.history));
 await page.evaluate(async()=>{const s=await window.offerpilotReminders.getState();await window.offerpilotReminders.configure({...s.preferences,enabled:false});});
 assertStage('acceptance closes its explicit opt-in',(await state()).preferences.enabled===false);
 await shot('native-final-disabled');
 if(!report.native.visible||!report.native.clicked)throw Error('native-toast-visibility-or-real-click-not-observed');
 } finally {nativeAbort.abort();await nativePending.catch(()=>{});}
}
