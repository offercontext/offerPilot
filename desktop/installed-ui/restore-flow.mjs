import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
const sha = b => createHash('sha256').update(b).digest('hex');
export const delay = ms => new Promise(r => setTimeout(r, ms));
export async function until(fn, timeout=30000) {
 const end=Date.now()+timeout;
 do { if(await fn())return; await delay(100); } while(Date.now()<end);
 throw new Error('bounded condition did not complete');
}
export async function api(page, url, method='GET', body) {
 return page.evaluate(async ({url,method,body})=>{
  const response=await fetch(url,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok)throw new Error(`synthetic API failed: ${response.status}`);
  return response.json();
 },{url,method,body});
}
export async function navigateSettings(page) {
 await page.getByRole('navigation',{name:'主导航',exact:true}).waitFor();
 await page.getByRole('button',{name:'设置',exact:true}).click();
 await page.getByRole('button',{name:'数据与备份',exact:true}).click();
 await page.getByRole('heading',{name:'从工作区 ZIP 恢复',exact:true}).waitFor();
}
export async function restoreFlow({page:initial, dataDirectory, scratch, output, report, saveExport, restart}) {
 let page=initial;
 const check=(name,value)=>{assert.ok(value,name);report.assertions.push(name);};
 const shot=async name=>{const file=`${name}.png`;await page.screenshot({path:path.join(output,file),fullPage:true});report.screenshots.push({file,sha256:sha(await fs.readFile(path.join(output,file)))});};
 const status=text=>page.getByRole('status').filter({hasText:text});
 const button=text=>page.getByRole('button',{name:text,exact:true});
 await navigateSettings(page);await status('没有待恢复任务').waitFor();
 check('full application navigation reaches Settings restore',true);await shot('settings-navigation');
 const item=await api(page,'/api/applications','POST',{company_name:'Restore QA Synthetic',position_name:'Cloud acceptance',status:'pending',notes:'snapshot-before-restore'});
 const main='# Restore synthetic bundle\n\n![diagram](pic.png)\n';
 const png='iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAIAAADZF8uwAAAAFklEQVR4nGPkUbJgIASYCKoYVTQAigDGfAB+aijOswAAAABJRU5ErkJggg==';
 const source=await page.evaluate(async ({main,png})=>{
  const form=new FormData();form.append('file',new File([main],'source.md',{type:'text/markdown'}));
  form.append('files',new File([Uint8Array.from(atob(png),c=>c.charCodeAt(0))],'pic.png',{type:'image/png'}));
  const r=await fetch('/api/knowledge/sources',{method:'POST',body:form});if(r.status!==202)throw new Error('synthetic bundle upload failed');return (await r.json()).source;
 },{main,png});
 await until(async()=>{const s=await api(page,`/api/knowledge/sources/${source.id}`);assert.notEqual(s.extraction_status,'failed');return s.extraction_status==='extracted';});
 const assets=(await api(page,`/api/knowledge/sources/${source.id}/assets`)).items;
 assert.equal(assets.length,1);const asset=assets[0];
 const evidence=(await api(page,`/api/knowledge/sources/${source.id}/evidence`)).items;
 check('synthetic knowledge image has actual evidence association',evidence.some(e=>e.asset_id===asset.id));
 const archive=path.join(scratch,'offerpilot-backup.zip');
 await saveExport(page,archive);check('Settings button exports real ZIP', (await fs.stat(archive)).size>0);
 await api(page,`/api/applications/${item.id}`,'PUT',{notes:'changed-after-backup'});
 const marker='restore-qa-original-marker';await fs.writeFile(path.join(dataDirectory,marker),'original-directory-retained',{flag:'wx'});
 const select=()=>page.getByLabel('恢复备份文件',{exact:true}).setInputFiles(archive);
 await select();await status('预检通过，等待确认').waitFor();
 const consent=()=>page.getByRole('checkbox',{name:'我已保存当前工作，并理解覆盖范围及授权失效。',exact:true});
 const confirm=()=>button('确认恢复并等待重启');
 check('confirmation disabled before consent',await confirm().isDisabled());
 await consent().check();check('checkbox alone cannot confirm',await confirm().isDisabled());
 await page.getByLabel('输入确认短语',{exact:true}).fill('REPLACE WORKSPACE');check('two consent factors required',await confirm().isEnabled());
 await button('刷新恢复状态').click();await until(async()=>!(await consent().isChecked()));check('refresh clears consent',await confirm().isDisabled());
 await button('取消恢复').click();await status('恢复已取消').waitFor();
 check('cancellation leaves current record unchanged',(await api(page,`/api/applications/${item.id}`)).notes==='changed-after-backup');
 await select();await status('预检通过，等待确认').waitFor();
 await consent().check();await page.getByLabel('输入确认短语',{exact:true}).fill('REPLACE WORKSPACE');await shot('settings-validated');
 await confirm().click();await status('已确认，等待完整重启').waitFor();
 await page.reload();await navigateSettings(page);await status('已确认，等待完整重启').waitFor();
 check('reload does not install or overwrite current data',(await api(page,`/api/applications/${item.id}`)).notes==='changed-after-backup');await shot('settings-queued');
 const queued=await api(page,'/api/backups/restore');assert.match(queued.id,/^[a-f0-9]{32}$/);
 page=await restart();await navigateSettings(page);await status('恢复已完成').waitFor();
 check('cold restart restores snapshot record',(await api(page,`/api/applications/${item.id}`)).notes==='snapshot-before-restore');
 const restoredAssets=(await api(page,`/api/knowledge/sources/${source.id}/assets`)).items;
 const restoredEvidence=(await api(page,`/api/knowledge/sources/${source.id}/evidence`)).items;
 check('restored asset and evidence relationship matches',restoredAssets.length===1&&restoredAssets[0].id===asset.id&&restoredEvidence.some(e=>e.asset_id===asset.id));
 for(const [suffix,bytes] of [['content',Buffer.from(main)],[`assets/${asset.id}/content`,Buffer.from(png,'base64')]]) {
  const actual=await page.evaluate(async url=>{const r=await fetch(url);if(!r.ok)throw new Error('restored content unavailable');return Array.from(new Uint8Array(await r.arrayBuffer()));},`/api/knowledge/sources/${source.id}/${suffix}`);
  check(`restored ${suffix==='content'?'main':'image'} bytes match`,Buffer.from(actual).equals(bytes));
 }
 const original=path.join(path.dirname(dataDirectory),`.${path.basename(dataDirectory)}.restores`,`${queued.id}-original`,marker);
 check('original directory retained',await fs.readFile(original,'utf8')==='original-directory-retained');
 check('post-backup marker absent from restored root',!(await fs.access(path.join(dataDirectory,marker)).then(()=>true,()=>false)));
 await shot('settings-completed');return page;
}
