import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {hash} from './integrity.mjs';
export const delay=ms=>new Promise(r=>setTimeout(r,ms));
export async function until(fn,timeout=30000){const end=Date.now()+timeout;do{if(await fn())return;await delay(200);}while(Date.now()<end);throw Error('bounded-condition-timeout');}
async function api(page,url,method='GET',body){return page.evaluate(async({url,method,body})=>{const r=await fetch(url,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok)throw Error(`synthetic API status ${r.status}`);return r.json();},{url,method,body});}
async function settings(page){await page.getByRole('navigation',{name:'主导航',exact:true}).getByRole('button',{name:'设置',exact:true}).click();await page.getByRole('button',{name:'连接与自动任务',exact:true}).click();await page.getByRole('heading',{name:'桌面本地提醒',exact:true}).waitFor();}
export async function reminderFlow({page,app,userData,output,report,restart}){
 const check=(name,value)=>{assert.ok(value,name);report.assertions.push(name);};
 const state=()=>page.evaluate(()=>window.offerpilotReminders.getState());
 const durable=async()=>JSON.parse(await fs.readFile(path.join(userData,'desktop-reminders.json'),'utf8'));
 const shot=async name=>{const file=`${name}.png`;await page.screenshot({path:path.join(output,file),fullPage:true});report.screenshots.push({file,sha256:await hash(path.join(output,file))});};
 report.native={visible:'not-tested',clicked:'not-tested',physicalSuspendResume:'not-tested',scope:'no native notification trigger; only settings/restore'};
 await settings(page);const section=()=>page.getByRole('region',{name:'桌面本地提醒',exact:true});
 check('fresh installed profile is disabled',(await state()).preferences.enabled===false);
 await section().getByRole('checkbox',{name:'截止日期',exact:true}).click();
 await until(async()=>(await state()).preferences.scopes.includes('deadline'));
 check('scope alone remains disabled',(await state()).preferences.enabled===false);
 // Pause before enabling: there are no events and no native notification calls.
 await section().getByRole('switch',{name:'暂停提醒',exact:true}).click();
 await until(async()=>(await state()).preferences.paused);
 await section().getByRole('switch',{name:'开启桌面本地提醒',exact:true}).click();
 await until(async()=>(await state()).preferences.enabled&&(await state()).status==='paused');
 const before=await durable();check('desktop context persisted before subscription',/^[a-f0-9]{64}$/.test(before.workspaceId));
 const saved=await api(page,'/api/applications','POST',{company_name:'Restore QA synthetic',position_name:'Before backup',status:'pending'});
 const bytes=await page.evaluate(async()=>{const r=await fetch('/api/backups/export');if(!r.ok)throw Error('export failed');return Array.from(new Uint8Array(await r.arrayBuffer()));});
 const archive=path.join(output,'synthetic-workspace.zip');await fs.writeFile(archive,Buffer.from(bytes));
 report.archiveSha256=await hash(archive);
 const later=await api(page,'/api/applications','POST',{company_name:'Restore QA later',position_name:'After backup',status:'pending'});
 await shot('settings-before-restore');
 let previous=before.workspaceId;
 for(const stage of ['enabled-paused','disabled']){
  await page.getByLabel('恢复备份文件',{exact:true}).setInputFiles(archive);
  await page.getByText('预检通过，等待确认',{exact:true}).waitFor();
  check(`${stage}: preview preserves workspace identity`,(await api(page,'/api/reminders/desktop-context')).workspace_id===previous);
  await page.getByRole('checkbox',{name:'我已保存当前工作，并理解覆盖范围及授权失效。',exact:true}).check();
  await page.getByRole('textbox',{name:'输入确认短语',exact:true}).fill('REPLACE WORKSPACE');
  await page.getByRole('button',{name:'确认恢复并等待重启',exact:true}).click();
  await page.getByText('已确认，等待完整重启',{exact:true}).waitFor();
  await shot(`queued-${stage}`);
  ({page,app}=await restart());await settings(page);
  await page.getByText('恢复已完成',{exact:true}).waitFor();
  const current=await durable(),publicState=await state(),context=await api(page,'/api/reminders/desktop-context');
  check(`${stage}: successful restore rotates identity`,current.workspaceId!==previous&&context.workspace_id===current.workspaceId);
  check(`${stage}: restore cannot revive subscription`,publicState.preferences.enabled===false&&current.preferences.enabled===false);
  check(`${stage}: pause and local scope retained`,current.preferences.paused&&current.preferences.scopes.includes('deadline'));
  check(`${stage}: old dedupe domain removed`,Object.keys(current.history).length===0&&Object.keys(current.scopeSince).length===0);
  check(`${stage}: backed-up application reads through actual API`,(await api(page,`/api/applications/${saved.id}`)).position_name==='Before backup');
  check(`${stage}: post-backup application actually replaced`,await page.evaluate(async id=>(await fetch(`/api/applications/${id}`)).status===404,later.id));
  await shot(`completed-${stage}`);previous=current.workspaceId;
 }
 ({page,app}=await restart());await settings(page);
 const after=await durable();check('ordinary restart retains final identity and disabled preference',after.workspaceId===previous&&!after.preferences.enabled);
 await shot('settings-final-disabled');
}
