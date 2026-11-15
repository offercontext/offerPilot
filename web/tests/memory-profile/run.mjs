/** Real Chromium + production build + real HTTP/SQLite. Only test content is synthetic. */
import { chromium } from 'playwright-core';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const out = path.resolve(process.argv[2] || path.join(root, 'evidence/memory-profile/continuation/browser'));
await mkdir(out, {recursive:true});
const data = await mkdtemp(path.join(tmpdir(),'memory-browser-'));
const origin='http://127.0.0.1:19080';
const logs=[];
const report={kind:'real production UI / real HTTP and SQLite / synthetic test content / no provider',sha:process.env.GITHUB_SHA||'local',run:process.env.GITHUB_RUN_ID||null,attempt:process.env.GITHUB_RUN_ATTEMPT||null,assertions:[],screenshots:[],pageErrors:[],consoleErrors:[],unexpectedNetwork:[],requests:[],status:'running'};
const git = (...args) => { const result=spawnSync('git',args,{cwd:root,encoding:'utf8'});assert.equal(result.status,0,result.stderr);return result.stdout.trim(); };
report.checkout={head:git('rev-parse','HEAD'),tree:git('rev-parse','HEAD^{tree}'),dirty:git('status','--porcelain'),prHead:process.env.PR_HEAD_SHA||null};
if(process.env.GITHUB_SHA)assert.equal(report.checkout.head,process.env.GITHUB_SHA);
const server=spawn(path.join(root,'.venv/bin/oc'),['start','--port','19080'],{cwd:root,env:{...process.env,OFFERPILOT_DATA:data},stdio:['ignore','pipe','pipe']});
server.stdout.on('data',b=>logs.push(b.toString()));server.stderr.on('data',b=>logs.push(b.toString()));
let browser,page;
const check=(name,condition)=>{assert(condition,name);report.assertions.push(name);};
async function api(method,pathname,body){const r=await fetch(origin+pathname,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});assert(r.ok,`${method} ${pathname}: ${r.status} ${await r.clone().text()}`);return r.json();}
async function countIs(locator,expected){for(let i=0;i<100;i++){if(await locator.count()===expected)return true;await new Promise(r=>setTimeout(r,50));}return false;}
async function shot(name){const file=path.join(out,name+'.png');await page.screenshot({path:file,fullPage:true,animations:'disabled'});report.screenshots.push({name,file,viewport:page.viewportSize()});}
async function context(locale,theme,width=1672,height=941){const c=await browser.newContext({viewport:{width,height},deviceScaleFactor:1});await c.addInitScript(({locale,theme})=>{localStorage.setItem('op-theme',theme);localStorage.setItem('offerpilot.locale',locale);},{locale,theme});await c.route('**/*',async route=>{const url=new URL(route.request().url());if(url.origin!==origin){report.unexpectedNetwork.push(url.href);return route.abort();}await route.continue();});const p=await c.newPage();p.on('pageerror',e=>report.pageErrors.push(String(e)));p.on('console',m=>{if(m.type()==='error')report.consoleErrors.push(m.text());});p.on('request',r=>{if(r.url().includes('/api/confirmed-memory'))report.requests.push({method:r.method(),url:r.url()});});return p;}
function recall(){const code=`import json\nfrom pathlib import Path\nfrom offerpilot.db import session_factory_for_data_dir\nfrom offerpilot.models import Conversation\nfrom offerpilot.context_projector.loader import ContextSourceLoader\nfrom offerpilot.context_sources.loader import load_optional_sources\np=Path(${JSON.stringify(data)})\ns=session_factory_for_data_dir(p)\nwith s() as db:\n c=Conversation(title='browser recall test');db.add(c);db.commit();cid=c.id\nl=ContextSourceLoader(p/'data.db')\ntry: print(json.dumps([m.content for x in load_optional_sources(l,cid,'question').contributors if x.name=='confirmed_memory' for m in x.messages],ensure_ascii=False))\nfinally: l._pool.close()\n`;const result=spawnSync(path.join(root,'.venv/bin/python'),['-c',code],{cwd:root,encoding:'utf8'});assert.equal(result.status,0,result.stderr);return result.stdout;}
try {
 for(let i=0;i<200;i++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{} if(i===199)throw Error('server startup failed');await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 page=await context('zh-CN','dark');await page.goto(origin+'/?view=memory');
 const profile=page.locator('section[aria-label="记忆画像"]');const detail=profile.getByRole('complementary',{name:'记忆详情'});
 await profile.getByRole('heading',{name:'我的记忆画像'}).waitFor();await profile.getByText('没有匹配的记忆',{exact:true}).waitFor();check('real empty state',true);
 await profile.getByRole('button',{name:'添加记忆'}).click();await page.getByLabel('你希望 Pilot 记住的内容').fill('希望从事 Agent 开发');await page.getByRole('button',{name:'确认保存'}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
 check('UI add persisted exactly once',(await api('GET','/api/confirmed-memory')).length===1);
 for(const content of ['项目经历：OfferPilot','技能：并发测试','偏好：简洁表达','近期准备：已完成模拟练习'])await api('POST','/api/confirmed-memory',{mutation_id:randomUUID(),expected_version:0,confirmed:true,action:'confirm',content});
 await profile.getByRole('button',{name:/^刷\s*新$/}).click();await profile.getByRole('button',{name:/项目经历：OfferPilot/}).waitFor();
 await profile.getByRole('button',{name:/希望从事 Agent 开发/}).click();await detail.getByText('希望从事 Agent 开发',{exact:true}).waitFor();await shot('desktop-zh-dark-graph');check('graph detail and source',await detail.getByText('用户明确确认',{exact:true}).isVisible());
 await profile.getByRole('radio',{name:'列表',exact:true}).check();check('list exposes every record',await countIs(profile.locator('button[aria-pressed]'),5));await shot('desktop-zh-dark-list');
 await profile.getByRole('textbox',{name:'搜索记忆'}).fill('并发');check('search narrows list',await countIs(profile.locator('button[aria-pressed]'),1));await profile.getByRole('textbox',{name:'搜索记忆'}).fill('');
 await profile.getByRole('combobox',{name:'记忆分组'}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'技能'}).click();check('group filter',await countIs(profile.locator('button[aria-pressed]'),1));
 await profile.getByRole('combobox',{name:'记忆分组'}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'全部分组'}).click();
 await detail.getByRole('button',{name:/^编\s*辑$/}).click();await page.getByLabel('你希望 Pilot 记住的内容').fill('不应保存的取消正文');await shot('edit-dialog');await page.getByRole('button',{name:/^取\s*消$/}).click();check('cancel leaves stored text intact',!(await api('GET','/api/confirmed-memory')).some(r=>r.content==='不应保存的取消正文'));
 await detail.getByRole('button',{name:/^编\s*辑$/}).click();await page.getByLabel('你希望 Pilot 记住的内容').fill('希望从事 Agent 工程开发');
 const before=report.requests.filter(r=>r.method==='POST').length;
 await page.route('**/api/confirmed-memory/**',async route=>{if(route.request().method()==='POST')await new Promise(r=>setTimeout(r,300));await route.continue();});
 await page.getByRole('button',{name:'确认保存'}).dblclick();await page.getByRole('dialog').waitFor({state:'hidden'});await detail.getByText('希望从事 Agent 工程开发',{exact:true}).waitFor();
 check('double click sends one real mutation',report.requests.filter(r=>r.method==='POST').length-before===1);
 let rows=await api('GET','/api/confirmed-memory');let edited=rows.find(r=>r.content==='希望从事 Agent 工程开发');check('edit increments exactly one version',edited?.current_version===2);let recalled=recall();check('actual recall replaces old text',recalled.includes('Agent 工程开发')&&!recalled.includes('Agent 开发'));
 await detail.getByRole('button',{name:/^撤\s*回$/}).click();await page.getByRole('button',{name:'确认撤回'}).click();await detail.getByRole('button',{name:'重新确认'}).waitFor();check('withdraw removes actual recall',!recall().includes('Agent 工程开发'));
 await profile.getByRole('combobox',{name:'记忆状态'}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'已撤回'}).click();check('withdrawn filter shows one',await countIs(profile.locator('button[aria-pressed]'),1));
 await page.reload();await profile.getByRole('button',{name:/希望从事 Agent 工程开发/}).click();await detail.getByRole('button',{name:'重新确认'}).click();await page.getByRole('button',{name:'确认保存'}).click();await page.getByRole('dialog').waitFor({state:'hidden'});check('reconfirm restores recall',recall().includes('Agent 工程开发'));
 await detail.getByRole('button',{name:'查看版本'}).click();await page.getByRole('dialog').getByText('版本 4',{exact:true}).waitFor();await shot('history');await page.getByRole('dialog').locator('.ant-modal-footer').getByRole('button',{name:/^关\s*闭$/}).click();
 await detail.getByRole('button',{name:/^删\s*除$/}).click();await page.getByRole('button',{name:/^取\s*消$/}).click();check('delete cancel preserves record',(await api('GET','/api/confirmed-memory')).some(r=>r.id===edited.id));
 await detail.getByRole('button',{name:/^删\s*除$/}).click();await page.getByRole('button',{name:'确认删除'}).click();await profile.getByText('记忆及历史正文已删除',{exact:true}).waitFor();await page.reload();check('delete survives refresh',!(await api('GET','/api/confirmed-memory')).some(r=>r.id===edited.id));check('delete removes actual recall',!recall().includes('Agent 工程开发'));
 await shot('deleted-after-refresh');
 await api('POST','/api/confirmed-memory',{mutation_id:randomUUID(),expected_version:0,confirmed:true,action:'confirm',content:'希望从事 Agent 开发'});
 for(const [locale,theme,width,height] of [['zh-CN','light',1672,941],['en-US','dark',1672,941],['en-US','light',1672,941],['zh-CN','dark',390,844],['en-US','light',390,844]]){
  page=await context(locale,theme,width,height);await page.goto(origin+'/?view=memory');await page.getByRole('heading',{name:locale==='zh-CN'?'我的记忆画像':'My memory profile'}).waitFor();await page.getByRole('button',{name:/希望从事 Agent 开发/}).click();await shot(`${locale}-${theme}-${width}`);
  const dims=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));check(`${locale}-${theme}-${width} has no document horizontal overflow`,dims.scroll<=dims.width);
 }
 check('no page errors',report.pageErrors.length===0);check('no console errors',report.consoleErrors.length===0);check('no external requests',report.unexpectedNetwork.length===0);report.status='passed';
}catch(error){report.status='failed';report.error=error.stack||String(error);console.error(report.error);if(page)await shot('failure').catch(()=>{});process.exitCode=1;}
finally{await browser?.close();server.kill('SIGTERM');await writeFile(path.join(out,'server.log'),logs.join(''));await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(`REPORT ${out} ${report.status}`);}
