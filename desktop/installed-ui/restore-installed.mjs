import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn,execFileSync } from 'node:child_process';
import { _electron } from 'playwright-core';
import { getCurrentFuseWire,FuseState,FuseV1Options } from '@electron/fuses';
import { extractFile } from '@electron/asar';
import { fileURLToPath } from 'node:url';
import { hash,treeFiles,verifyPayload,normalizeSourceText } from './integrity.mjs';
import { DESKTOP_SOURCE_FILES } from './desktop-source-manifest.mjs';
import { waitForDesktopSurfaces,readDesktopSecurity,validatePartitionIsolation } from './desktop-surfaces.mjs';
import { validateSecurity,sameWindowsPath } from './contract.mjs';
import { observeDevToolsDisabled } from './devtools-probe.mjs';
import { nativeCapabilityInstrumentation,assertNativeDownload } from './capability-probes.mjs';
import { restoreFlow,until } from './restore-flow.mjs';
const here=path.dirname(fileURLToPath(import.meta.url));
const pin=JSON.parse(await fs.readFile(path.join(here,'restore-pin.json'),'utf8'));
const source=path.resolve(process.env.RESTORE_PRODUCT_SOURCE||'product');
const artifact=path.resolve(process.env.RESTORE_INSTALLER_DIR||'installer');
const output=path.resolve(process.env.RESTORE_EVIDENCE||'restore-evidence');
const report={scope:'Pinned NSIS-installed Electron: full Settings navigation, synthetic API data, actual UI export/restore, normal process exit and cold restart',pin,assertions:[],screenshots:[],failures:[],pageErrors:[],externalRequests:[],launches:[],fullGate:'independent-not-certified',nativeDialogPointerValidated:false,ordinaryUserUacSmartScreenValidated:false};
let app,page,exe,backend,installDir,userData,scratch,environment,owned=[],stage='environment';
await fs.mkdir(output,{recursive:true});
const exists=f=>fs.access(f).then(()=>true,()=>false);
async function command(executable,args,env=environment,timeout=180000){
 return new Promise((resolve,reject)=>{const child=spawn(executable,args,{env,windowsHide:true,stdio:['ignore','pipe','pipe']});let chunks=[],bytes=0;const timer=setTimeout(()=>{child.kill();reject(new Error('owned command timed out'));},timeout);
 child.stdout.on('data',b=>{bytes+=b.length;if(bytes<8*1024*1024)chunks.push(b);});child.stderr.resume();child.once('error',()=>{clearTimeout(timer);reject(new Error('command spawn failed'));});child.once('exit',code=>{clearTimeout(timer);if(code!==0||bytes>=8*1024*1024)reject(new Error(`command failed: ${code}`));else resolve(Buffer.concat(chunks).toString('utf8').replace(/^\uFEFF/,''));});});
}
async function windows(mode,extra={}){return JSON.parse(await command('pwsh.exe',['-NoProfile','-NonInteractive','-File',path.join(here,'windows.ps1'),'-Mode',mode],{...environment,...extra},mode==='install'?210000:30000));}
async function launch(){
 stage='launch-preflight';assert.deepEqual((await windows('snapshot')).processes,[]);
 app=await _electron.launch({executablePath:exe,cwd:installDir,env:environment,chromiumSandbox:true,bypassCSP:false,timeout:90000});
 app.context().on('request',request=>{const u=new URL(request.url());if(['http:','https:','ws:','wss:'].includes(u.protocol)&&u.hostname!=='127.0.0.1')report.externalRequests.push(u.origin);});
 const identity=await app.evaluate(({app})=>({pid:process.pid,exe:process.execPath,userData:app.getPath('userData')}));
 assert.ok(sameWindowsPath(identity.exe,exe));assert.ok(sameWindowsPath(identity.userData,userData));
 const snap=await windows('snapshot');const main=snap.processes.find(p=>p.pid===identity.pid&&sameWindowsPath(p.path,exe));assert.ok(main?.created);
 owned=[main,...snap.processes.filter(p=>p.parentPid===main.pid&&sameWindowsPath(p.path,backend))];
 await until(()=>exists(path.join(userData,'desktop-port.json')),90000);
 const {port}=JSON.parse(await fs.readFile(path.join(userData,'desktop-port.json'),'utf8'));assert.ok(Number.isInteger(port)&&port>0&&port<65536);
 const surfaces=await waitForDesktopSurfaces(app,`http://127.0.0.1:${port}`);page=surfaces.owner.page;page.setDefaultTimeout(30000);
 for(const surface of [page,surfaces.haru.page])surface.on('pageerror',()=>report.pageErrors.push('page-error'));
 const ids={};for(const [role,surface] of [['owner',page],['haru',surfaces.haru.page]])ids[role]=await(await app.browserWindow(surface)).evaluate(w=>w.id);
 const security=await app.evaluate(readDesktopSecurity,ids);
 for(const role of ['owner','haru']){security[role].devToolsProbe=await app.evaluate(observeDevToolsDisabled,ids[role]);validateSecurity(security[role]);}
 validatePartitionIsolation(security.partitionIsolation);
 const runtime=await windows('snapshot');assert.ok(runtime.processes.some(p=>p.parentPid===main.pid&&sameWindowsPath(p.path,backend)));
 assert.ok(runtime.listeners.every(l=>['127.0.0.1','::1'].includes(l.address)));
 owned=runtime.processes;report.launches.push({mainPid:main.pid,created:main.created,processCount:owned.length,port,securityValidated:true});
 stage='full-settings-flow';return page;
}
async function stop(){
 if(!app)return;stage='normal-exit';const previous=app;
 await app.evaluate(({app})=>{setTimeout(()=>app.quit(),0);});
 await until(async()=>{const snap=await windows('snapshot');return snap.processes.length===0&&snap.listeners.length===0;},60000);
 app=undefined;owned=[];report.assertions.push('actual installed processes and listeners exited normally');
 // close the disconnected automation transport only after OS exit is proven.
 await previous.close().catch(()=>{});
}
async function saveExport(p,savePath){
 const key='restore-settings-export';const owner=await app.browserWindow(p);const ownerURL=p.url();
 const run=(operation,extra={})=>app.evaluate(nativeCapabilityInstrumentation,{operation,key,ownerURL,...extra});
 await run('install',{owner});
 try{
  await run('arm',{active:{mode:'save',urlPolicy:'owner-settings-export-blob',filename:'offerpilot-backup.zip',savePath}});
  const response=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/backups/export'&&r.request().method()==='GET');
  response.catch(()=>{});await p.getByRole('button',{name:'导出完整数据',exact:true}).click({timeout:10000});const r=await response;assert.equal(r.status(),200);const bytes=await r.body();
  let snapshot;await until(async()=>{snapshot=await run('snapshot');assert.deepEqual(snapshot.failures,[]);return snapshot.downloads.some(d=>d.terminal);});
  assert.equal(snapshot.downloads.length,1);assertNativeDownload(snapshot.downloads[0],'save',bytes.length);
  assert.deepEqual(snapshot.dialogs,[{mode:'save',expected:true,choice:'controlled-synthetic-path'}]);assert.equal(snapshot.externalLaunchAttempts,0);assert.equal(snapshot.ownerURLUnchanged,true);assert.deepEqual(snapshot.messages,[]);assert.deepEqual(snapshot.navigations,[]);
  assert.deepEqual(await fs.readFile(savePath),bytes);report.assertions.push('native save choice yields exact real Settings response bytes');
 }finally{await run('restore');}
}
try{
 assert.equal(process.platform,'win32');assert.ok(process.env.RUNNER_TEMP&&process.env.APPDATA);
 assert.equal(execFileSync('git',['rev-parse','HEAD'],{cwd:source,encoding:'utf8'}).trim(),pin.buildCommit);
 userData=path.join(process.env.APPDATA,'OfferPilot Desktop');assert.equal(await exists(userData),false,'existing real profile is a blocker');
 scratch=await fs.mkdtemp(path.join(process.env.RUNNER_TEMP,'Restore 中文 '));installDir=path.join(scratch,'Application 安装');exe=path.join(installDir,'OfferPilot Desktop.exe');backend=path.join(installDir,'resources/backend/offerpilot-backend.exe');
 environment=Object.fromEntries(Object.entries(process.env).filter(([key])=>/^(SystemRoot|WINDIR|SystemDrive|COMSPEC|PATH|PATHEXT|TEMP|TMP|APPDATA|LOCALAPPDATA|USERPROFILE|HOMEDRIVE|HOMEPATH|ProgramFiles|ProgramFiles\(x86\)|ProgramData|ALLUSERSPROFILE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE|OS|SESSIONNAME)$/i.test(key)));
 Object.assign(environment,{LITELLM_LOCAL_MODEL_COST_MAP:'True',PYTHONUTF8:'1',PYTHONUNBUFFERED:'1',UI_EXE:exe,UI_BACKEND:backend,UI_USER_DATA:userData,UI_INSTALL_DIR:installDir});
 const installer=path.join(artifact,pin.installer);stage='installer-hash';assert.equal(await hash(installer),pin.installerSha256);
 const unpack=path.join(scratch,'unpack'),payload=path.join(scratch,'payload');const seven=path.join(process.env.ProgramFiles,'7-Zip/7z.exe');
 await command(seven,['x',installer,`-o${unpack}`,'-y']);const archives=(await treeFiles(unpack)).filter(n=>path.basename(n)==='app-64.7z');assert.equal(archives.length,1);
 await command(seven,['x',path.join(unpack,archives[0]),`-o${payload}`,'-y']);stage='install';assert.equal((await windows('install',{UI_INSTALLER:installer})).exitCode,0);assert.equal(await exists(userData),false);
 report.payloadFiles=await verifyPayload(installDir,payload);report.installedExeSha256=await hash(exe);
 for(const name of DESKTOP_SOURCE_FILES)assert.equal(normalizeSourceText(extractFile(path.join(installDir,'resources/app.asar'),name)),normalizeSourceText(await fs.readFile(path.join(source,'desktop',name))));
 const fuses=await getCurrentFuseWire(exe);assert.equal(fuses[FuseV1Options.EnableNodeCliInspectArguments],FuseState.ENABLE);
 await launch();const dataDirectory=path.join(userData,'data');
 await restoreFlow({page,dataDirectory,scratch,output,report,saveExport,restart:async()=>{await stop();return launch();}});
 assert.notEqual(report.launches[0].mainPid,report.launches[1].mainPid);assert.notEqual(report.launches[0].created,report.launches[1].created);
 await stop();assert.equal(await hash(exe),report.installedExeSha256);assert.deepEqual(report.pageErrors,[]);assert.deepEqual(report.externalRequests,[]);
}catch(error){
 report.failures.push({stage,type:error.name,code:error.code??null,message:String(error.message).replace(/https?:\/\/\S+/g,'[URL]').slice(0,3000)});
 if(page&&!page.isClosed())try{await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});report.screenshots.push({file:'failure.png',sha256:await hash(path.join(output,'failure.png'))});}catch{/* Preserve the original failure. */}
}
finally{
 if(app){try{await stop();}catch{report.failures.push({stage:'normal-cleanup',type:'Failed'});if(owned.length)try{report.cleanup=await windows('cleanup',{UI_OWNED_IDENTITIES:JSON.stringify(owned.slice().reverse())});}catch{report.failures.push({stage:'owned-cleanup',type:'Failed'});}}}
 report.status=report.failures.length?'failed':'passed';await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,assertions:report.assertions,failures:report.failures}));if(report.failures.length)process.exitCode=1;
}
