import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawn,execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { chromium } from 'playwright-core';
import { restoreFlow,until } from './restore-flow.mjs';
const source=path.resolve(process.env.RESTORE_PRODUCT_SOURCE||'.');
const output=path.resolve(process.env.RESTORE_EVIDENCE||'/tmp/restore-full-settings-evidence');
const scratch=await fs.mkdtemp(path.join(os.tmpdir(),'restore-full-settings-'));const data=path.join(scratch,'data');
const origin='http://127.0.0.1:18774';let child,browser,page;
const report={scope:'Full production application Settings navigation and real HTTP recovery; not Electron',assertions:[],screenshots:[],failures:[],pageErrors:[],externalRequests:[]};
await fs.mkdir(output,{recursive:true});
async function stop(){
 if(!child)return;const c=child;
 if(c.exitCode!==null||c.signalCode!==null){child=undefined;return;}
 let timer;const exited=new Promise(resolve=>c.once('exit',resolve));c.kill();
 try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('API failed to exit')),15000);})]);}finally{clearTimeout(timer);}
 child=undefined;
}
async function start(){
 const identity=randomUUID();
 child=spawn(path.join(source,'.venv/bin/python'),[path.join(source,'web/tests/backup-restore/serve.py'),data,'18774',path.join(source,'web/dist'),identity],{cwd:source,env:{...process.env,LITELLM_LOCAL_MODEL_COST_MAP:'True'},stdio:['ignore','ignore','pipe']});
 child.stderr.on('data',()=>{});let error;child.on('error',e=>{error=e;});
 await until(async()=>{if(error)throw error;if(child.exitCode!==null)throw new Error('API exited');try{const r=await fetch(origin+'/api/health');return r.ok&&r.headers.get('x-restore-fixture')===identity;}catch{return false;}},60000);
}
try{
 report.productCommit=execFileSync('git',['rev-parse','HEAD'],{cwd:source,encoding:'utf8'}).trim();
 await start();browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.RESTORE_CHROMIUM?{executablePath:process.env.RESTORE_CHROMIUM}:{})});
 const context=await browser.newContext({viewport:{width:1280,height:1100}});
 await context.route('**/*',route=>{const url=new URL(route.request().url());if(['http:','https:'].includes(url.protocol)&&url.origin!==origin){report.externalRequests.push(url.origin);return route.abort();}return route.continue();});
 page=await context.newPage();page.on('pageerror',()=>report.pageErrors.push('page-error'));await page.goto(origin);
 await restoreFlow({page,dataDirectory:data,scratch,output,report,saveExport:async(p,file)=>{const pending=p.waitForEvent('download');pending.catch(()=>{});await p.getByRole('button').filter({has:p.getByText('导出完整数据',{exact:true})}).click({timeout:10000});await(await pending).saveAs(file);},restart:async()=>{await stop();await start();await page.reload();return page;}});
 assert.deepEqual(report.pageErrors,[]);assert.deepEqual(report.externalRequests,[]);
}catch(error){report.failures.push(String(error));if(page&&!page.isClosed())await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});}
finally{await browser?.close();await stop().catch(e=>report.failures.push(String(e)));report.status=report.failures.length?'failed':'passed';await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));if(report.failures.length)process.exitCode=1;}
