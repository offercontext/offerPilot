/** Real restore component + production HTTP routes, synthetic data, actual restart.
 * Not an installed Electron dialog test or a complete Settings navigation test.
 */
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { readFile, writeFile, mkdir, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';
import { build } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright-core';
const here=path.dirname(fileURLToPath(import.meta.url));
const repo=path.resolve(here,'../../..');
const output=path.resolve(process.argv[2] || 'artifacts/backup-restore-browser');
const dist=path.join(output,'dist');
const port=18773, origin=`http://127.0.0.1:${port}`;
const report={scope:'Production restore component and HTTP API; synthetic workspace; process exit and cold restart. Not installed Electron.',assertions:[],screenshots:[],failures:[],pageErrors:[],externalRequests:[]};
const check=(label,value)=>{assert.ok(value,label);report.assertions.push(label);};
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let api,browser;
async function stopApi(){
 if(!api)return;
 if(api.exitCode!==null || api.signalCode!==null){api=undefined;return;}
 const child=api;
 const exited=new Promise(resolve=>child.once('exit',resolve));
 child.kill();
 await Promise.race([exited,sleep(15000).then(()=>{if(child.exitCode===null && child.signalCode===null)throw new Error('API did not exit; cannot safely restore');})]);
 if(api===child)api=undefined;
}
async function startApi(data){
 const identity=randomUUID();
 const python=process.env.PYTHON || path.join(repo,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
 api=spawn(python,[path.join(here,'serve.py'),data,String(port),dist,identity],{cwd:repo,env:{...process.env,LITELLM_LOCAL_MODEL_COST_MAP:'True'},stdio:['ignore','pipe','pipe']});
 api.stdout.on('data',chunk=>process.stdout.write(chunk));api.stderr.on('data',chunk=>process.stderr.write(chunk));
 let spawnError;api.on('error',error=>{spawnError=error;});
 for(let i=0;i<300;i++){
  if(spawnError)throw spawnError;
  if(api.exitCode!==null || api.signalCode!==null)throw new Error(`API exited ${api.exitCode}`);
  try{const health=await fetch(origin+'/api/health');if(health.ok && health.headers.get('x-restore-fixture')===identity)return;}catch{}
  await sleep(100);
 }
 throw new Error('API startup timed out');
}
await mkdir(output,{recursive:true});
try{
 report.commit=execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim();
 report.files={};
 for(const name of ['web/src/components/BackupRestore.tsx','web/src/services/backupRestore.ts','src/offerpilot/backup_install.py','src/offerpilot/backup_routes.py','web/tests/backup-restore/run.mjs','web/package-lock.json'])report.files[name]=hash(await readFile(path.join(repo,name)));
 await build({configFile:false,root:here,plugins:[react()],build:{outDir:dist,emptyOutDir:true}});
 if(process.argv.includes('--build-only'))report.buildOnly=true;
 else{
  browser=await chromium.launch({headless:true,args:['--disable-background-networking']});
  report.browser=browser.version();
  for(const locale of ['zh-CN','en-US']){
   const data=await mkdtemp(path.join(tmpdir(),'restore-browser-synthetic-'));
   await startApi(data);
   const context=await browser.newContext({viewport:{width:1100,height:1100}});
   await context.addInitScript(locale=>localStorage.setItem('offerpilot.locale',locale),locale);
   const page=await context.newPage();
   page.on('pageerror',e=>report.pageErrors.push(String(e)));
   await context.route('**/*',async route=>{
    if(new URL(route.request().url()).origin!==origin){report.externalRequests.push(route.request().url());await route.abort();}else await route.continue();
   });
   const zh=locale==='zh-CN';
   const button=(cn,en)=>page.getByRole('button',{name:zh?cn:en,exact:true});
   const status=(cn,en)=>page.getByRole('status').filter({hasText:zh?cn:en});
   const screenshot=async name=>{const file=`${locale}-${name}.png`;await page.screenshot({path:path.join(output,file),fullPage:true});report.screenshots.push({file,sha256:hash(await readFile(path.join(output,file)))});};
   await page.goto(origin);
   await status('没有待恢复任务','No pending restore').waitFor();
   const response=await fetch(origin+'/api/backups/export');check('actual ZIP export succeeds',response.ok);
   const zip=Buffer.from(await response.arrayBuffer());
   const file=page.locator('input[type=file]');
   await file.setInputFiles({name:'backup.zip',mimeType:'application/zip',buffer:zip});
   await status('预检通过，等待确认','Validated; awaiting confirmation').waitFor();
   const confirm=button('确认恢复并等待重启','Confirm restore for next restart');
   check('confirmation initially disabled',await confirm.isDisabled());
   await page.getByRole('checkbox').check();
   check('checkbox alone cannot confirm',await confirm.isDisabled());
   await page.getByRole('textbox').fill('REPLACE WORKSPACE');
   check('two consent factors enable confirmation',await confirm.isEnabled());
   await screenshot('validated');
   await button('刷新恢复状态','Refresh restore status').click();
   await page.waitForFunction(()=>document.querySelector('input[type=checkbox]')?.checked===false);
   check('refresh clears prior consent',await confirm.isDisabled());
   await button('取消恢复','Cancel restore').click();
   await status('恢复已取消','Restore cancelled').waitFor();
   await file.setInputFiles({name:'backup.zip',mimeType:'application/zip',buffer:zip});
   await status('预检通过，等待确认','Validated; awaiting confirmation').waitFor();
   await page.getByRole('checkbox').check();await page.getByRole('textbox').fill('REPLACE WORKSPACE');
   await confirm.click();
   await status('已确认，等待完整重启','Confirmed; awaiting a full restart').waitFor();
   await screenshot('queued');
   await page.reload();await status('已确认，等待完整重启','Confirmed; awaiting a full restart').waitFor();
   check('page reload does not install',true);
   await stopApi();await startApi(data);
   await page.reload();await status('恢复已完成','Restore completed').waitFor();
   check('real process restart completes restore',true);await screenshot('completed');
   await context.close();await stopApi();
  }
  check('no page errors',report.pageErrors.length===0);check('no external browser requests',report.externalRequests.length===0);
 }
}catch(error){report.failures.push(error.stack||String(error));}
finally{
 await browser?.close();await stopApi().catch(error=>report.failures.push(String(error)));
 report.status=report.failures.length?'failed':report.buildOnly?'build-only-not-browser-verified':'passed';
 await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));
 console.log(`Restore browser regression: ${report.status}`);
 if(report.failures.length)process.exitCode=1;
}
